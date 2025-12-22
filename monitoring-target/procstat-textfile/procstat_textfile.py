#!/usr/bin/env python3
import os
import time
import sys
import subprocess
from typing import Dict, List, Tuple, Any

PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")
CLK_TCK = os.sysconf("SC_CLK_TCK")

PROCFS_PATH = os.getenv("PROCFS_PATH", "/host/proc")


def sanitize(s: str) -> str:
    if not s:
        return ""
    return "".join(c if c.isalnum() or c in ["_", ":", "-", "."] else "_" for c in s)[
        :200
    ]


def read_env() -> Tuple[int, str, int, int, float]:
    interval = int(os.getenv("INTERVAL_SECONDS", "5"))
    out_dir = os.getenv("OUTPUT_DIR", "/textfile")
    top_n = int(os.getenv("TOP_N", "0"))
    min_rss = int(os.getenv("MIN_RSS_BYTES", "0"))
    min_cpu = float(os.getenv("MIN_CPU_PERCENT", "0"))
    return interval, out_dir, top_n, min_rss, min_cpu


def ensure_dir(path: str):
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass


def write_metrics(path: str, lines: List[str]):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            for line in lines:
                f.write(line + "\n")
        os.replace(tmp, path)
    except OSError:
        pass


def load_uid_map() -> Dict[int, str]:
    mapping = {}
    try:
        passwd_path = f"{PROCFS_PATH}/../etc/passwd"
        if not os.path.exists(passwd_path):
            passwd_path = "/etc/passwd"

        with open(passwd_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":")
                if len(parts) >= 3:
                    try:
                        uid = int(parts[2])
                        user = parts[0]
                        mapping[uid] = user
                    except ValueError:
                        continue
    except Exception:
        pass
    return mapping


def get_process_pids() -> List[str]:
    pids = []
    try:
        with os.scandir(PROCFS_PATH) as it:
            for entry in it:
                if entry.is_dir() and entry.name.isdigit():
                    pids.append(entry.name)
    except Exception:
        pass
    return pids


def read_file_content(path: str) -> str:
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return ""


def get_process_info(pid: str, uid_map: Dict[int, str]) -> Dict[str, Any]:
    proc_dir = f"{PROCFS_PATH}/{pid}"

    comm = read_file_content(f"{proc_dir}/comm")
    if not comm:
        return {}

    cmdline_raw = read_file_content(f"{proc_dir}/cmdline")
    cmd_parts = cmdline_raw.split("\0")
    exe = comm
    if len(cmd_parts) > 0 and cmd_parts[0]:
        exe = cmd_parts[0]

    uid = "unknown"
    username = "unknown"
    try:
        stat_info = os.stat(proc_dir)
        uid_val = stat_info.st_uid
        username = uid_map.get(uid_val, str(uid_val))
    except OSError:
        pass

    rss_bytes = 0
    statm = read_file_content(f"{proc_dir}/statm")
    if statm:
        parts = statm.split()
        if len(parts) >= 2:
            try:
                rss_pages = int(parts[1])
                rss_bytes = rss_pages * PAGE_SIZE
            except ValueError:
                pass

    stat_content = read_file_content(f"{proc_dir}/stat")
    total_time_ticks = 0
    if stat_content:
        rpar_idx = stat_content.rfind(")")
        if rpar_idx != -1:
            rest = stat_content[rpar_idx + 1 :].strip()
            fields = rest.split()
            if len(fields) >= 14:
                try:
                    utime = int(fields[11])
                    stime = int(fields[12])
                    total_time_ticks = utime + stime
                except ValueError:
                    pass

    return {
        "pid": int(pid),
        "name": sanitize(comm),
        "username": sanitize(username),
        "exe": sanitize(exe),
        "rss": rss_bytes,
        "cpu_ticks": total_time_ticks,
    }


def collect_gpu_metrics() -> Dict[int, List[Dict[str, int]]]:
    metrics: Dict[str, Dict[str, int]] = {}
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "pmon", "-c", "1"], stderr=subprocess.DEVNULL, text=True
        )
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            parts = line.split()
            if len(parts) < 8 or parts[1] == "-":
                continue
            try:
                pid = int(parts[1])
                gpu_idx = parts[0]
                sm = int(parts[3]) if parts[3].isdigit() else 0
                mem = int(parts[4]) if parts[4].isdigit() else 0
                key = f"{pid}:{gpu_idx}"
                metrics[key] = {
                    "gpu": gpu_idx,
                    "pid": pid,
                    "sm": sm,
                    "mem": mem,
                    "fb": 0,
                }
            except ValueError:
                continue
    except Exception:
        pass

    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "query-compute-apps",
                "--format=csv,noheader,nounits",
                "--query-compute-apps=pid,used_memory,index",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        for line in out.splitlines():
            parts = [s.strip() for s in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                pid = int(parts[0])
                fb_mib = int(parts[1])
                gpu_idx = parts[2]
                key = f"{pid}:{gpu_idx}"
                if key in metrics:
                    metrics[key]["fb"] = fb_mib
                else:
                    metrics[key] = {
                        "gpu": gpu_idx,
                        "pid": pid,
                        "sm": 0,
                        "mem": 0,
                        "fb": fb_mib,
                    }
            except ValueError:
                continue
    except Exception:
        pass

    result: Dict[int, List[Dict[str, int]]] = {}
    for m in metrics.values():
        pid = m["pid"]
        if pid not in result:
            result[pid] = []
        result[pid].append(m)
    return result


def build_prom_lines(
    proc_list: List[Dict[str, Any]], gpu_map: Dict[int, List[Dict[str, int]]]
) -> List[str]:
    lines = []

    hostname = sanitize(os.uname().nodename)

    for p in proc_list:
        pid = p["pid"]
        labels = f'pid="{pid}",process="{p["name"]}",user="{p["username"]}",exe="{p["exe"]}",instance="{hostname}"'

        lines.append(f"proc_cpu_percent{{{labels}}} {p['cpu_percent']:.1f}")
        lines.append(f"proc_memory_rss_bytes{{{labels}}} {p['rss']}")

        for g in gpu_map.get(pid, []):
            gpu_labels = f'{labels},gpu="{g.get("gpu", "unknown")}"'
            lines.append(f'proc_gpu_sm_percent{{{gpu_labels}}} {g.get("sm", 0)}')
            lines.append(f'proc_gpu_mem_percent{{{gpu_labels}}} {g.get("mem", 0)}')
            lines.append(f'proc_gpu_fb_mem_mib{{{gpu_labels}}} {g.get("fb", 0)}')

    return lines


def main():
    interval, out_dir, top_n, min_rss, min_cpu = read_env()
    ensure_dir(out_dir)

    prev_ticks_map = {}
    prev_time = time.time()

    uid_map = load_uid_map()

    print(
        f"Starting procstat collector (Host-Procfs Mode). Reading from {PROCFS_PATH}. Interval: {interval}s",
        flush=True,
    )

    while True:
        loop_start = time.time()

        pids = get_process_pids()

        current_procs = []
        current_ticks_map = {}
        now_time = time.time()
        time_delta = now_time - prev_time
        if time_delta <= 0:
            time_delta = 0.0001

        for pid_str in pids:
            info = get_process_info(pid_str, uid_map)
            if not info:
                continue

            pid = info["pid"]
            current_ticks = info["cpu_ticks"]

            cpu_percent = 0.0
            if pid in prev_ticks_map:
                delta_ticks = current_ticks - prev_ticks_map[pid]
                if delta_ticks >= 0:
                    cpu_seconds = delta_ticks / CLK_TCK
                    cpu_percent = (cpu_seconds / time_delta) * 100.0

            current_ticks_map[pid] = current_ticks
            info["cpu_percent"] = cpu_percent

            if min_cpu > 0 and cpu_percent < min_cpu:
                continue
            if min_rss > 0 and info["rss"] < min_rss:
                continue

            current_procs.append(info)

        prev_ticks_map = current_ticks_map
        prev_time = now_time

        gpu_map = collect_gpu_metrics()
        lines = build_prom_lines(current_procs, gpu_map)
        write_metrics(os.path.join(out_dir, "procstats.prom"), lines)

        elapsed = time.time() - loop_start
        wait_time = max(0.0, interval - elapsed)
        time.sleep(wait_time)


if __name__ == "__main__":
    main()

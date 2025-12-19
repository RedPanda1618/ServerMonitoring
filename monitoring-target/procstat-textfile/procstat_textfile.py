#!/usr/bin/env python3
import os
import time
import psutil
import datetime
import subprocess
import traceback
from typing import Dict, Any, List, Tuple


# Helper: sanitize label value per Prometheus exposition format
def sanitize(s: str) -> str:
    return "".join(c if c.isalnum() or c in ["_", ":", "-", "."] else "_" for c in s)[
        :200
    ]


def read_env() -> Tuple[int, str, int, int, float]:
    interval = int(os.getenv("INTERVAL_SECONDS", "1"))
    out_dir = os.getenv("OUTPUT_DIR", "/textfile")
    top_n = int(os.getenv("TOP_N", "0"))  # 0 disables top-n filter
    min_rss = int(os.getenv("MIN_RSS_BYTES", "0"))
    min_cpu = float(os.getenv("MIN_CPU_PERCENT", "0"))
    return interval, out_dir, top_n, min_rss, min_cpu


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def write_metrics(path: str, lines: List[str]):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for line in lines:
            f.write(line + "\n")
    os.replace(tmp, path)


def collect_gpu_metrics() -> Dict[int, List[Dict[str, int]]]:
    """
    pmonからSM/Mem使用率(%)を、query-compute-appsからメモリ使用量(MiB)を取得し、
    PIDごとにマージして返す。
    """
    metrics: Dict[str, Dict[str, int]] = {}  # Key: "pid:gpu_idx", Value: metric dict

    # 1. pmon で SM(%), Mem(%) を取得
    try:
        # -s u (utilization) を指定して明示的に使用率を取得
        out = subprocess.check_output(
            ["nvidia-smi", "pmon", "-s", "u", "-c", "1"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            # 期待フォーマット: gpu pid type sm mem enc dec command
            if len(parts) < 8:
                continue
            if parts[1] == "-":
                continue  # アイドル行

            try:
                pid = int(parts[1])
                gpu_idx = parts[0]

                # 数値でなければ0
                def val(x):
                    return int(x) if x.isdigit() else 0

                key = f"{pid}:{gpu_idx}"
                metrics[key] = {
                    "gpu": gpu_idx,
                    "pid": pid,
                    "sm": val(parts[3]),
                    "mem": val(parts[4]),
                    "fb": 0,  # ここでは取れないので一旦0
                }
            except ValueError:
                continue
    except Exception:
        # pmonが失敗しても query-compute-apps で最低限の情報を取るため継続
        pass

    # 2. query-compute-apps で FB(MiB) を取得 (これが確実)
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
            # CSV: pid, used_memory_mib, gpu_index
            parts = [s.strip() for s in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                pid = int(parts[0])
                fb_mib = int(parts[1])
                gpu_idx = parts[2]

                key = f"{pid}:{gpu_idx}"
                if key in metrics:
                    # 既にpmonで見つかっていれば fb を更新
                    metrics[key]["fb"] = fb_mib
                else:
                    # pmonに出てこないがメモリを使っている場合 (Cuda init直後など)
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

    # 結果を整形: Dict[int, List[Dict]]
    result: Dict[int, List[Dict[str, int]]] = {}
    for m in metrics.values():
        pid = m["pid"]
        entry = {"gpu": m["gpu"], "sm": m["sm"], "mem": m["mem"], "fb": m["fb"]}
        if pid not in result:
            result[pid] = []
        result[pid].append(entry)

    return result


def build_metrics(
    now: float, procs: List[psutil.Process], gpu_map: Dict[int, List[Dict[str, int]]]
) -> List[str]:
    ts_ms = int(now * 1000)
    lines: List[str] = []

    # HELP / TYPE definitions
    lines.append("# HELP proc_cpu_percent Process CPU percent over interval")
    lines.append("# TYPE proc_cpu_percent gauge")
    lines.append("# HELP proc_memory_rss_bytes Resident Set Size in bytes")
    lines.append("# TYPE proc_memory_rss_bytes gauge")
    lines.append("# HELP proc_memory_vms_bytes Virtual Memory Size in bytes")
    lines.append("# TYPE proc_memory_vms_bytes gauge")
    lines.append("# HELP proc_open_fds Open file descriptors count")
    lines.append("# TYPE proc_open_fds gauge")
    lines.append("# HELP proc_threads Number of threads in the process")
    lines.append("# TYPE proc_threads gauge")
    lines.append("# HELP proc_gpu_sm_percent NVIDIA per-process SM utilization percent")
    lines.append("# TYPE proc_gpu_sm_percent gauge")
    lines.append(
        "# HELP proc_gpu_mem_percent NVIDIA per-process GPU memory utilization percent"
    )
    lines.append("# TYPE proc_gpu_mem_percent gauge")
    lines.append("# HELP proc_gpu_fb_mem_mib NVIDIA per-process framebuffer memory MiB")
    lines.append("# TYPE proc_gpu_fb_mem_mib gauge")

    hostname = sanitize(os.uname().nodename)

    for p in procs:
        try:
            with p.oneshot():
                pid = p.pid
                name = sanitize(p.name() or "")
                username = sanitize(p.username() or "")
                cmdline = " ".join(p.cmdline()[:1]) if p.cmdline() else name
                exe = sanitize(cmdline or name)
                cpu = getattr(p, "_last_cpu_percent", p.cpu_percent(None))
                mem = p.memory_info()
                rss = int(mem.rss)
                vms = int(mem.vms)
                num_threads = p.num_threads()
                try:
                    open_fds = p.num_fds()
                except Exception:
                    open_fds = 0
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

        labels = f'pid="{pid}",process="{name}",user="{username}",exe="{exe}",instance="{hostname}"'

        lines.append(f"proc_cpu_percent{{{labels}}} {cpu}")
        lines.append(f"proc_memory_rss_bytes{{{labels}}} {rss}")
        lines.append(f"proc_memory_vms_bytes{{{labels}}} {vms}")
        lines.append(f"proc_open_fds{{{labels}}} {open_fds}")
        lines.append(f"proc_threads{{{labels}}} {num_threads}")

        g_list = gpu_map.get(pid, [])
        for g in g_list:
            gpu_id = g.get("gpu", "unknown")
            gpu_labels = f'{labels},gpu="{gpu_id}"'

            lines.append(f'proc_gpu_sm_percent{{{gpu_labels}}} {g.get("sm", 0)}')
            lines.append(f'proc_gpu_mem_percent{{{gpu_labels}}} {g.get("mem", 0)}')
            lines.append(f'proc_gpu_fb_mem_mib{{{gpu_labels}}} {g.get("fb", 0)}')

    return lines


def main():
    interval, out_dir, top_n, min_rss, min_cpu = read_env()
    ensure_dir(out_dir)

    # Prime CPU percent to measure deltas
    for p in psutil.process_iter(attrs=[]):
        try:
            p.cpu_percent(None)
        except Exception:
            pass

    out_path = os.path.join(out_dir, "procstats.prom")

    while True:
        start = time.time()
        procs = []
        for p in psutil.process_iter(attrs=["pid", "name", "username"]):
            try:
                if min_rss or min_cpu:
                    mi = p.memory_info()
                    if min_rss and mi.rss < min_rss:
                        continue
                procs.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        time.sleep(0.01)

        filtered: List[psutil.Process] = []
        for p in procs:
            try:
                cpu = p.cpu_percent(None)
                if min_cpu and cpu < min_cpu:
                    continue
                p._last_cpu_percent = cpu
                filtered.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        if top_n and len(filtered) > top_n:

            def keyfn(pr: psutil.Process):
                try:
                    r = pr.memory_info().rss
                except Exception:
                    r = 0
                return (getattr(pr, "_last_cpu_percent", 0.0), r)

            filtered = sorted(filtered, key=keyfn, reverse=True)[:top_n]

        # Use new collection function
        gpu_map = collect_gpu_metrics()

        lines = build_metrics(time.time(), filtered, gpu_map)
        write_metrics(out_path, lines)

        elapsed = time.time() - start
        sleep = max(0.0, interval - elapsed)
        time.sleep(sleep)


if __name__ == "__main__":
    main()

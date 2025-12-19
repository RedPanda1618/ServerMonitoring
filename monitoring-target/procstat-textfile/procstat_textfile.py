#!/usr/bin/env python3
import os
import time
import psutil
import datetime
import subprocess
import traceback
from typing import Dict, Any, List, Tuple


def sanitize(s: str) -> str:
    return "".join(c if c.isalnum() or c in ["_", ":", "-", "."] else "_" for c in s)[
        :200
    ]


def read_env() -> Tuple[int, str, int, int, float]:
    interval = int(os.getenv("INTERVAL_SECONDS", "1"))
    out_dir = os.getenv("OUTPUT_DIR", "/textfile")
    top_n = int(os.getenv("TOP_N", "0"))
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
    metrics: Dict[str, Dict[str, int]] = {}

    # 1. pmon で SM(%), Mem(%) を取得
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "pmon", "-c", "1"], stderr=subprocess.DEVNULL, text=True
        )
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 8:
                continue  # command列まであるか確認
            if parts[1] == "-":
                continue

            try:
                pid = int(parts[1])
                gpu_idx = parts[0]

                def val(x):
                    return int(x) if x.isdigit() else 0

                key = f"{pid}:{gpu_idx}"
                metrics[key] = {
                    "gpu": gpu_idx,
                    "pid": pid,
                    "sm": val(parts[3]),
                    "mem": val(parts[4]),
                    "fb": 0,  # pmonからは取れないため0
                }
            except ValueError:
                continue
    except Exception:
        pass

    # 2. query-compute-apps で FB(MiB) を取得 (これが重要)
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
        entry = {"gpu": m["gpu"], "sm": m["sm"], "mem": m["mem"], "fb": m["fb"]}
        if pid not in result:
            result[pid] = []
        result[pid].append(entry)
    return result


def build_metrics(
    now: float, procs: List[psutil.Process], gpu_map: Dict[int, List[Dict[str, int]]]
) -> List[str]:
    lines: List[str] = []
    # HELP definitions
    lines.append("# HELP proc_cpu_percent Process CPU percent")
    lines.append("# TYPE proc_cpu_percent gauge")
    lines.append("# HELP proc_memory_rss_bytes RSS bytes")
    lines.append("# TYPE proc_memory_rss_bytes gauge")
    lines.append("# HELP proc_gpu_sm_percent GPU SM util")
    lines.append("# TYPE proc_gpu_sm_percent gauge")
    lines.append("# HELP proc_gpu_mem_percent GPU Mem util")
    lines.append("# TYPE proc_gpu_mem_percent gauge")
    lines.append("# HELP proc_gpu_fb_mem_mib GPU Framebuffer MiB")
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
                rss = int(p.memory_info().rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

        labels = f'pid="{pid}",process="{name}",user="{username}",exe="{exe}",instance="{hostname}"'
        lines.append(f"proc_cpu_percent{{{labels}}} {cpu}")
        lines.append(f"proc_memory_rss_bytes{{{labels}}} {rss}")

        for g in gpu_map.get(pid, []):
            gpu_labels = f'{labels},gpu="{g.get("gpu", "unknown")}"'
            lines.append(f'proc_gpu_sm_percent{{{gpu_labels}}} {g.get("sm", 0)}')
            lines.append(f'proc_gpu_mem_percent{{{gpu_labels}}} {g.get("mem", 0)}')
            lines.append(
                f'proc_gpu_fb_mem_mib{{{gpu_labels}}} {g.get("fb", 0)}'
            )  # ここが正しく出るようになります

    return lines


def main():
    interval, out_dir, top_n, min_rss, min_cpu = read_env()
    ensure_dir(out_dir)
    for p in psutil.process_iter(attrs=[]):
        try:
            p.cpu_percent(None)
        except:
            pass

    while True:
        start = time.time()
        procs = []
        for p in psutil.process_iter(attrs=["pid", "name", "username"]):
            procs.append(p)

        time.sleep(0.01)  # CPU計測用待機

        # CPU/RSS フィルタリング
        filtered = []
        for p in procs:
            try:
                cpu = p.cpu_percent(None)
                if min_cpu and cpu < min_cpu:
                    continue
                if min_rss and p.memory_info().rss < min_rss:
                    continue
                p._last_cpu_percent = cpu
                filtered.append(p)
            except:
                continue

        gpu_map = collect_gpu_metrics()  # 新しい関数を使用
        lines = build_metrics(time.time(), filtered, gpu_map)
        write_metrics(os.path.join(out_dir, "procstats.prom"), lines)

        time.sleep(max(0.0, interval - (time.time() - start)))


if __name__ == "__main__":
    main()

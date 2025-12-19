# Monitoring System

This repository provides a monitoring system for GPU-equipped machines, built with Prometheus, Grafana, Loki, and Fluentd.

## Overview

This system is composed of two main parts:

- `monitoring-server`: Collects and visualizes metrics and logs.
- `monitoring-target`: Exports metrics and logs from the target machine.

## Usage

### 1. Initial Setup

#### 1.1. Configure Environment Variables

First, copy the sample environment file for the monitoring server:

```bash
cp monitoring-server/.env.sample monitoring-server/.env
```

Then, edit `monitoring-server/.env` to set your desired Grafana administrator password.

#### 1.2. Generate Configuration Files

Generate the necessary configuration files by running the following command from the root of the project:

```bash
python generate_configs.py
```

This script will create `prometheus.yml` and `fluent.conf` based on the definitions in `env.targets.yaml`.

### 2. Start Monitoring Server

On the monitoring server machine, run the setup script to build and start the monitoring services:

```bash
cd monitoring-server
./setup.sh
```

This script will create necessary data directories, set permissions, build Docker images, and start the services.

### 3. Start Monitoring Target

On the target machine, you need to install the NVIDIA Container Toolkit to enable GPU monitoring. Please follow the official installation guide:

- **NVIDIA Container Toolkit Installation Guide:** [https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)

After installing the NVIDIA Container Toolkit, run the setup script to configure and start the monitoring agents:

````bash
cd monitoring-target
./setup.sh

Note about GPU support:

- The `dcgm_exporter` (GPU metrics) is behind a Docker Compose profile named `gpu`.
- The `monitoring-target/setup.sh` script auto-detects `nvidia-smi` and will start GPU services with `--profile gpu` if NVIDIA is present.
- To force start with GPU services manually, run:

```bash
docker compose --profile gpu up -d
````

- To start without GPU services (for machines without NVIDIA drivers), run:

```bash
docker compose up -d
```

This script will install `auditd`, configure audit rules, and then build and start the Docker services for the monitoring agents.

### 4. Access Grafana

Once the `monitoring-server` services are running, you can access the Grafana dashboard through your web browser at:

```

http://localhost:3000

```

Use the administrator credentials configured in `monitoring-server/.env` to log in.

## Components

### `monitoring-server`

- **Prometheus**: Collects metrics.
- **Grafana**: Visualizes metrics and logs.
- **Loki**: Aggregates logs.
- **Fluentd**: Receives logs from the target and forwards them to Loki.

### `monitoring-target`

- **Node Exporter**: Exports system-level metrics.
- **DCGM Exporter**: Exports GPU metrics.
- **Process Exporter**: Exports process-level metrics (CPU, memory usage per process).
- **Auditd**: Audits system calls and generates security logs.
- **Fluent-Package**: Forwards logs to the monitoring server.

## Configuration

- `env.targets.yaml`: Define the IP addresses and instance names of your monitoring servers and target machines here.
- `monitoring-server/.env`: Set the Grafana admin password.
- The `.gitignore` file is configured to exclude sensitive files, logs, and generated configurations from being committed to the repository.

**Note: If auditd shows most users as `unknown`**

- **File**: `/etc/audit/auditd.conf`
- **Before**: `name_format = NONE`
- **After**: `name_format = HOSTNAME`
- **Apply on the host OS**: run `sudo systemctl restart auditd.service`

If this setting is not applied, audit logs may show most users as `unknown`.

## Per-process metrics (CPU/Mem/GPU)

This repository now includes per-process metrics collection using two mechanisms:

- Process Exporter (`ncabatoff/process-exporter`): summary by process name and command.
- Textfile Collector (`procstat_textfile`): detailed per-PID metrics exposed via Node Exporter textfile collector.

What is collected by `procstat_textfile`:

- CPU percent per PID
- Memory RSS/VMS per PID
- Open file descriptors count per PID
- Threads per PID
- NVIDIA GPU per-process utilization (SM%, MEM%) and framebuffer memory (MiB) when `nvidia-smi` is available

Output file: `monitoring-target/textfile_collector_output/procstats.prom` (mounted into Node Exporter as `/etc/node-exporter/textfile_collector`). Prometheus scrapes these via the existing `node_exporter` job.

Tuning via environment variables (service `procstat_textfile` in `monitoring-target/docker-compose.yml`):

- `INTERVAL_SECONDS` (default 1): scrape interval.
- `TOP_N` (default 0): if > 0, keep only top N processes by CPU (tie-breaker by RSS).
- `MIN_RSS_BYTES` (default 0): filter out processes with RSS below this.
- `MIN_CPU_PERCENT` (default 0): filter out processes with CPU below this.

GPU per-process data also available via DCGM Exporter with a custom metrics CSV enabled in compose.

```

```

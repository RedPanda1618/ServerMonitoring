#!/bin/bash

set -e

export HOSTNAME

sudo apt update
sudo apt install -y auditd

sudo systemctl unmask auditd
sudo systemctl enable auditd
sudo systemctl restart auditd

echo "Applying audit rules..."
sudo cp auditd-config/audit.rules /etc/audit/rules.d/99-server-monitoring.rules
sudo chmod 640 /etc/audit/rules.d/99-server-monitoring.rules

sudo augenrules --load

echo "Current audit rules:"
sudo auditctl -l

mkdir -p textfile_collector_output

docker compose build --no-cache

if command -v nvidia-smi >/dev/null 2>&1; then
    echo "NVIDIA detected: starting GPU services (profile 'gpu')"
    docker compose --profile gpu up -d
else
    echo "NVIDIA not detected: starting without GPU services"
    docker compose up -d
fi

echo "Waiting for services to initialize..."
sleep 10
echo "Current status of Docker containers:"
docker compose ps
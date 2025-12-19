#!/bin/bash

set -e

mkdir -p ./grafana/data
sudo chown -R 472:472 ./grafana/data
mkdir -p ./fluentd/fluentd_buffer
mkdir -p ./loki/loki_data
mkdir -p ./prometheus/prometheus_data
## Allow setting Grafana admin password via env or generate one and write to .env
# Usage: GRAFANA_ADMIN_PASSWORD=yourpass ./setup.sh

# Determine Grafana admin user and password.
# Priority: GF_SECURITY_ADMIN_USER/GF_SECURITY_ADMIN_PASSWORD env >
# GRAFANA_ADMIN_USER/GRAFANA_ADMIN_PASSWORD env > interactive prompt > random
## Determine Grafana admin user and password with precedence:
## 1) environment variables GF_SECURITY_ADMIN_USER / GF_SECURITY_ADMIN_PASSWORD
## 2) alternate env names GRAFANA_ADMIN_USER / GRAFANA_ADMIN_PASSWORD
## 3) existing .env file values (if .env exists)
## 4) interactive prompt (only when running in a TTY)
## 5) random generation

# Helper to strip surrounding quotes
strip_quotes() {
	local s="$1"
	s="$(echo "$s" | sed -E 's/^\"(.*)\"$/\1/; s/^\'\''(.*)\'\''$/\1/')"
	echo "$s"
}

# Start with empty
grafana_user=""
grafana_pwd=""

# 1) env vars
if [ -n "$GF_SECURITY_ADMIN_USER" ]; then
	grafana_user="$GF_SECURITY_ADMIN_USER"
elif [ -n "$GRAFANA_ADMIN_USER" ]; then
	grafana_user="$GRAFANA_ADMIN_USER"
fi

if [ -n "$GF_SECURITY_ADMIN_PASSWORD" ]; then
	grafana_pwd="$GF_SECURITY_ADMIN_PASSWORD"
elif [ -n "$GRAFANA_ADMIN_PASSWORD" ]; then
	grafana_pwd="$GRAFANA_ADMIN_PASSWORD"
fi

# 2) if still empty and .env exists, load from it (do not overwrite .env)
if [ -f .env ]; then
	# parse lines like KEY=VALUE (take last if multiple)
	if [ -z "$grafana_user" ]; then
		_u=$(grep -E '^GF_SECURITY_ADMIN_USER=' .env | tail -n1 | cut -d'=' -f2- || true)
		_u=$(strip_quotes "${_u}")
		if [ -n "$_u" ]; then
			grafana_user="$_u"
		fi
	fi
	if [ -z "$grafana_pwd" ]; then
		_p=$(grep -E '^GF_SECURITY_ADMIN_PASSWORD=' .env | tail -n1 | cut -d'=' -f2- || true)
		_p=$(strip_quotes "${_p}")
		if [ -n "$_p" ]; then
			grafana_pwd="$_p"
		fi
	fi
fi

# 3) interactive prompt if running in a TTY and value still empty
if [ -z "$grafana_user" ]; then
	if [ -t 0 ]; then
		read -p "Grafana admin user (leave empty to generate random): " grafana_user
	fi
fi
if [ -z "$grafana_user" ]; then
	grafana_user="admin_$(openssl rand -hex 4)"
fi

if [ -z "$grafana_pwd" ]; then
	if [ -t 0 ]; then
		read -s -p "Grafana admin password (leave empty to generate random): " grafana_pwd
		echo
	fi
fi
if [ -z "$grafana_pwd" ]; then
	if command -v openssl >/dev/null 2>&1; then
		grafana_pwd=$(openssl rand -base64 12)
	else
		grafana_pwd=$(head -c 12 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 16)
	fi
fi

# If .env exists, do not overwrite; otherwise write values so docker-compose picks them up
if [ -f .env ]; then
	echo ".env already exists; using values from .env or environment. Not overwriting .env."
	echo "Grafana admin user: $grafana_user"
else
	cat > .env <<EOF
GF_SECURITY_ADMIN_USER=$grafana_user
GF_SECURITY_ADMIN_PASSWORD=$grafana_pwd
EOF
	echo "Grafana admin user: $grafana_user"
	echo "Grafana admin password written to .env (shown below):"
	echo "$grafana_pwd"
fi

docker-compose build --no-cache
docker-compose up -d
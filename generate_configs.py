import yaml
from pathlib import Path

# Define file paths
ROOT = Path(__file__).parent
ENV_YAML = ROOT / "env.targets.yaml"
PROMETHEUS_YML = ROOT / "monitoring-server/prometheus/config/prometheus.yml"
FLUENT_CONF = ROOT / "monitoring-target/fluent-package/conf/fluent.conf"


# Load env.targets.yaml
def load_env():
    if not ENV_YAML.exists():
        raise FileNotFoundError(f"{ENV_YAML} not found.")
    with open(ENV_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)


def generate_prometheus_yml(env):
    # Base configuration
    prometheus = {
        "global": {"scrape_interval": "1s", "evaluation_interval": "1s"},
        "scrape_configs": [
            {
                "job_name": "prometheus",
                "static_configs": [{"targets": ["localhost:9090"]}],
            },
            {
                "job_name": "node_exporter",
                "static_configs": [
                    {
                        "targets": [f"{t['ip']}:9100"],
                        "labels": {"instance": t["instance"]},
                    }
                    for t in env["targets"]
                ],
            },
            {
                "job_name": "dcgm_exporter",
                "static_configs": [
                    {
                        "targets": [f"{t['ip']}:9400"],
                        "labels": {"instance": t["instance"]},
                    }
                    for t in env["targets"]
                ],
            },
            {
                "job_name": "process_exporter",
                "static_configs": [
                    {
                        "targets": [f"{t['ip']}:9256"],
                        "labels": {"instance": t["instance"]},
                    }
                    for t in env["targets"]
                ],
            },
        ],
    }
    if not PROMETHEUS_YML.parent.exists():
        PROMETHEUS_YML.parent.mkdir(parents=True, exist_ok=True)
    with open(PROMETHEUS_YML, "w", encoding="utf-8") as f:
        yaml.dump(prometheus, f, allow_unicode=True, sort_keys=False)


def generate_fluent_conf(env):
    # Use the first IP from monitoring_servers
    servers = env["monitoring_servers"]
    server_blocks = "\n".join(
        [
            f"  <server>\n    host \"{s['ip']}\"\n    port 24224\n  </server>"
            for s in servers
        ]
    )

    # Fluentd configuration template
    # 1. Use 'logfmt' parser to automatically extract ALL parameters (a0, a1, arch, comm, exe, items, etc.)
    # 2. Use record_transformer to derive 'user' (preferring string UID) and 'args' (decoding proctitle)
    fluent_conf = (
        """# Input: Tail the standard auditd log file
<source>
  @type tail
  path /mnt/auditlogs/audit.log
  pos_file /fluentd/log/audit.log.pos
  tag "go.audit.raw"
  <parse>
    @type none
  </parse>
</source>

# Parse the message field as key=value pairs (logfmt)
# This extracts 'uid', 'UID', 'proctitle', 'comm', 'exe', 'success', 'syscall', etc.
<filter go.audit.raw>
  @type parser
  key_name message
  reserve_data true
  remove_key_name_field true
  <parse>
    @type logfmt
    types proctitle:string,a0:string,a1:string,a2:string,a3:string,uid:string,gid:string,UID:string,GID:string
  </parse>
</filter>

# Add enriched fields
<filter go.audit.raw>
  @type record_transformer
  enable_ruby true

  renew_record true

  <record>
    hostname ${ ENV.fetch('HOST_HOSTNAME', 'unknown') }

    # Audit ID extraction
    auditID ${ m = record.fetch("msg", ""); m.slice(/:(\d+)\):/, 1) || "unknown" }

    # 必要なフィールドを明示的に文字列としてコピー (巨大数値エラー回避)
    exe ${ record.fetch("exe", "unknown").to_s }
    comm ${ record.fetch("comm", "unknown").to_s }
    success ${ record.fetch("success", "unknown").to_s }
    syscall ${ record.fetch("syscall", "unknown").to_s }

    # User logic: resolve UID to username
    user ${ u = record.fetch("uid", nil); name = record.fetch("UID", nil); if name && name != ''; name; elsif u.nil?; "unknown"; else begin; require 'etc'; Etc.getpwuid(u.to_i).name; rescue; (u == "0" ? "root" : "uid_" + u.to_s); end; end }


    # Args logic (proctitleをデコードしてargsにする。元のproctitleは捨てるか、必要なら文字列として残す)
    # ここでは args だけ残します
    args ${ hex = record.fetch("proctitle", nil); hex = hex.to_s if hex.is_a?(Integer); hex ? [hex].pack("H*").tr("\\u0000", " ").strip : "unknown" }
  </record>
</filter>

<filter go.audit.raw>
    @type grep
    <exclude>
    key args
    pattern /procstat_textfile\.py/
    </exclude>
    <exclude>
    key exe
    pattern /process-exporter|node_exporter|dcgm-exporter/
    </exclude>
</filter>

<match go.audit.raw>
  @type forward
  send_timeout 60s
  recover_wait 10s
  hard_timeout 60s
  phi_failure_detector false
  <buffer>
    @type file
    path /fluentd/log/buffer/forward_audit
    flush_mode interval
    flush_interval 1s
    flush_at_shutdown true
    retry_forever true
    chunk_limit_size 1M
    queue_limit_length 128
  </buffer>
"""
        + server_blocks
        + """
</match>

# Use label for internal logs to avoid deprecation warning
<label @FLUENT_LOG>
  <match fluent.**>
    @type stdout
  </match>
</label>
"""
    )
    if not FLUENT_CONF.parent.exists():
        FLUENT_CONF.parent.mkdir(parents=True, exist_ok=True)
    with open(FLUENT_CONF, "w", encoding="utf-8") as f:
        f.write(fluent_conf)


def main():
    print("Generating configuration files from env.targets.yaml...")
    env = load_env()
    if not env:
        print("Error: env.targets.yaml is empty or not found.")
        return
    generate_prometheus_yml(env)
    print("prometheus.yml generated.")
    if not env.get("monitoring_servers"):
        print("Warning: No monitoring servers found in env.targets.yaml.")
        return
    if not env.get("targets"):
        print("Warning: No targets found in env.targets.yaml.")
        return
    generate_fluent_conf(env)
    print("fluent.conf generated.")
    message = (
        "Configuration files generated successfully.\n"
        "Please check the following files:\n"
        f"- {PROMETHEUS_YML}\n"
        f"- {FLUENT_CONF}"
    )
    print(message)


if __name__ == "__main__":
    main()

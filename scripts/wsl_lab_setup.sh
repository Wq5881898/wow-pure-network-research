#!/usr/bin/env bash
# Run as root inside the dedicated WoWLab distro after Windows reboot/import.
set -euo pipefail
test "$(id -u)" = 0 || { echo 'Run as root in WoWLab'; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git build-essential cmake python3 python3-venv protobuf-compiler docker.io docker-compose-v2
if ! id wowlab >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash wowlab
fi
usermod -aG docker wowlab
# Only set the dedicated lab distro's configuration, never host-wide .wslconfig.
python3 - <<'PY'
import configparser
from pathlib import Path
p=Path('/etc/wsl.conf')
c=configparser.ConfigParser()
if p.exists():
    c.read(p)
for section in ('boot','user'):
    if not c.has_section(section):
        c.add_section(section)
c['boot']['systemd']='true'
c['user']['default']='wowlab'
with p.open('w') as f:
    c.write(f)
PY
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fL --connect-timeout 20 --max-time 240 'https://golang.google.cn/dl/go1.27.3.linux-amd64.tar.gz' -o "$tmp/go.tar.gz"
echo "65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40  $tmp/go.tar.gz" | sha256sum -c -
if test ! -e /usr/local/go; then
  tar -C /usr/local -xzf "$tmp/go.tar.gz"
fi
ln -sfn /usr/local/go/bin/go /usr/local/bin/go
ln -sfn /usr/local/go/bin/gofmt /usr/local/bin/gofmt
if test "$(cat /proc/1/comm)" = systemd; then
  systemctl enable --now docker
else
  echo 'WSL systemd configured; terminate and relaunch WoWLab before Docker verification.'
fi
go version
protoc --version
docker compose version
echo 'Base packages configured. Next verify Docker daemon, registry access and Windows auth/world reachability.'

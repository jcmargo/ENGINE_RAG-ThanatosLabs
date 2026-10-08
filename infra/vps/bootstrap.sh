#!/bin/bash
# Alta del host. No arranca la aplicación ni pide el certificado.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "bootstrap status=refused reason=root-required" >&2
  exit 1
fi
if [[ $# -ne 1 || ! -s "$1" ]]; then
  echo "usage: bootstrap.sh <deploy-public-key>" >&2
  exit 1
fi

PUBKEY="$1"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BIN=/opt/engine-rag/bin
find "$SRC/infra/vps" -type f \( -name '*.sh' -o -name '*.service' -o -name '*.timer' -o -name '*.conf' \) -exec sed -i 's/\r$//' {} +

timedatectl set-timezone UTC

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg ufw unattended-upgrades
install -d -m 0755 /etc/apt/apt.conf.d
cat > /etc/apt/apt.conf.d/52engine-rag-unattended <<'EOF'
Unattended-Upgrade::Automatic-Reboot "false";
EOF
systemctl enable --now unattended-upgrades

if ! command -v docker >/dev/null 2>&1; then
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker

if ! id deploy >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash deploy
fi
install -d -m 0700 -o deploy -g deploy /home/deploy/.ssh
install -m 0600 -o deploy -g deploy "$PUBKEY" /home/deploy/.ssh/authorized_keys

if [[ ! -f /swapfile ]]; then
  fallocate -l 8G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
fi
swapon /swapfile || true
grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
echo 'vm.swappiness=10' > /etc/sysctl.d/99-engine-rag.conf
sysctl vm.swappiness=10

install -d -m 0755 "$BIN" /etc/engine-rag /etc/systemd/system/ollama.service.d
for script in "$SRC"/infra/vps/engine-rag-*.sh; do
  install -m 0755 "$script" "$BIN/$(basename "$script" .sh)"
done
install -m 0440 "$SRC/infra/vps/sudoers-deploy" /etc/sudoers.d/engine-rag-deploy
visudo -cf /etc/sudoers.d/engine-rag-deploy

install -m 0644 "$SRC/infra/vps/engine-rag-backup.service" /etc/systemd/system/engine-rag-backup.service
install -m 0644 "$SRC/infra/vps/engine-rag-backup.timer" /etc/systemd/system/engine-rag-backup.timer
install -m 0644 "$SRC/infra/vps/engine-rag-watch.service" /etc/systemd/system/engine-rag-watch.service
install -m 0644 "$SRC/infra/vps/engine-rag-watch.timer" /etc/systemd/system/engine-rag-watch.timer
install -m 0644 "$SRC/infra/vps/engine-rag-docker-user.service" /etc/systemd/system/engine-rag-docker-user.service
install -m 0644 "$SRC/infra/vps/ollama.service.d.conf" /etc/systemd/system/ollama.service.d/engine-rag.conf
systemctl daemon-reload
systemctl enable engine-rag-backup.timer engine-rag-watch.timer engine-rag-docker-user.service

ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
systemctl restart docker
systemctl restart engine-rag-docker-user.service

echo "bootstrap status=ok"

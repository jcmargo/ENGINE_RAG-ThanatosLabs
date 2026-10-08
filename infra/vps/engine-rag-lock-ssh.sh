#!/bin/bash
# Cierra root y la contraseña. Ejecutar solo después de probar la llave de deploy.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "lock status=refused reason=root-required" >&2
  exit 1
fi
if [[ ! -s /home/deploy/.ssh/authorized_keys ]]; then
  echo "lock status=refused reason=missing-deploy-key" >&2
  exit 1
fi

install -d -m 0755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/99-engine-rag.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
EOF
sshd -t
systemctl reload ssh
echo "ssh status=locked"

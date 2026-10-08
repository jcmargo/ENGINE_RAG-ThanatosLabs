#!/bin/bash
# Arranca el release ya extraído en /opt/engine-rag/src.
# Si el DNS no apunta al VPS, termina antes de pedir el certificado.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "deploy status=refused reason=root-required" >&2
  exit 1
fi

SRC=/opt/engine-rag/src
ENV_FILE=/etc/engine-rag/product.env
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$SRC/infra/compose.yml" -f "$SRC/infra/compose.vps.yml")

test -f "$ENV_FILE"
test -f /etc/engine-rag/source.env
test -d "$SRC/infra"
test -s /etc/engine-rag/restic-password

ln -sfn /etc/engine-rag/product.env "$SRC/.env.product"
ln -sfn /etc/engine-rag/restic-password "$SRC/.restic-password"
find "$SRC/infra/vps" -type f \( -name '*.sh' -o -name '*.service' -o -name '*.timer' -o -name '*.conf' \) -exec sed -i 's/\r$//' {} +
for script in "$SRC"/infra/vps/engine-rag-*.sh; do
  install -m 0755 "$script" "/opt/engine-rag/bin/$(basename "$script" .sh)"
done

# shellcheck disable=SC1091
set -a
. /etc/engine-rag/source.env
set +a
: "${RAG_DOMAIN:?RAG_DOMAIN}"
: "${RAG_EXPECTED_IPV4:?RAG_EXPECTED_IPV4}"
: "${RAG_EXPECTED_IPV6:?RAG_EXPECTED_IPV6}"

if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
fi
install -d -m 0755 /etc/systemd/system/ollama.service.d
install -m 0644 "$SRC/infra/vps/ollama.service.d.conf" /etc/systemd/system/ollama.service.d/engine-rag.conf
systemctl daemon-reload
systemctl enable ollama

"${COMPOSE[@]}" build

python3 - <<'PY'
import ipaddress
import os
import socket
import sys

domain = os.environ["RAG_DOMAIN"]
expected = {
    socket.AF_INET: ipaddress.ip_address(os.environ["RAG_EXPECTED_IPV4"]),
    socket.AF_INET6: ipaddress.ip_address(os.environ["RAG_EXPECTED_IPV6"]),
}
for family, want in expected.items():
    try:
        infos = socket.getaddrinfo(domain, None, family, socket.SOCK_STREAM)
    except socket.gaierror as error:
        print(f"dns status=unresolved family={family} error={error}", file=sys.stderr)
        sys.exit(2)
    got = {ipaddress.ip_address(item[4][0].split("%")[0]) for item in infos}
    if want not in got:
        print(
            f"dns status=mismatch family={family} expected={want} got={','.join(map(str, got))}",
            file=sys.stderr,
        )
        sys.exit(2)
print("dns status=ok")
PY

"${COMPOSE[@]}" up -d --wait --wait-timeout 1200
if ! id ollama >/dev/null 2>&1; then
  echo "deploy status=refused reason=ollama-user-missing" >&2
  exit 1
fi
install -d -o ollama -g ollama -m 0750 /var/lib/ollama/models
systemctl reset-failed ollama || true
systemctl restart ollama

for _ in $(seq 1 60); do
  if curl -fsS --max-time 2 http://172.28.0.1:11434/api/tags >/dev/null; then
    break
  fi
  sleep 2
done
curl -fsS --max-time 5 http://172.28.0.1:11434/api/tags >/dev/null
OLLAMA_HOST=172.28.0.1:11434 ollama pull qwen2.5:7b

backend_id="$(docker image inspect --format '{{.Id}}' "engine-rag-backend:${ENGINE_RAG_VERSION:-0.1.0}")"
gateway_id="$(docker image inspect --format '{{.Id}}' "engine-rag-gateway:${ENGINE_RAG_VERSION:-0.1.0}")"
install -d -m 0750 /etc/engine-rag
umask 027
cat > /etc/engine-rag/release.env <<EOF
ENGINE_RAG_VERSION=${ENGINE_RAG_VERSION:-0.1.0}
ENGINE_RAG_REVISION=${ENGINE_RAG_REVISION:-unknown}
ENGINE_RAG_SOURCE_SHA256=${ENGINE_RAG_SOURCE_SHA256:-unknown}
ENGINE_RAG_BACKEND_ID=${backend_id}
ENGINE_RAG_GATEWAY_ID=${gateway_id}
EOF
chmod 0640 /etc/engine-rag/release.env
echo "deploy status=ok"

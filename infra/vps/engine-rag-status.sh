#!/bin/bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "status refused reason=root-required" >&2
  exit 1
fi

src=/opt/engine-rag/src
docker compose --env-file /etc/engine-rag/product.env \
  -f "$src/infra/compose.yml" -f "$src/infra/compose.vps.yml" ps
systemctl is-active ollama docker engine-rag-backup.timer engine-rag-watch.timer || true

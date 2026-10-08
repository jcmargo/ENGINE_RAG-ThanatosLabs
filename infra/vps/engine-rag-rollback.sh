#!/bin/bash
# Vuelve al digest anterior. No ejecuta alembic downgrade.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "rollback status=refused reason=root-required" >&2
  exit 1
fi

previous=/etc/engine-rag/release.env.previous
if [[ ! -f "$previous" ]]; then
  echo "rollback status=refused reason=missing-previous" >&2
  exit 1
fi

read_key() {
  awk -F= -v key="$1" '$1 == key {print substr($0, index($0, "=") + 1)}' "$2"
}

version="$(read_key ENGINE_RAG_VERSION "$previous")"
backend_id="$(read_key ENGINE_RAG_BACKEND_ID "$previous")"
gateway_id="$(read_key ENGINE_RAG_GATEWAY_ID "$previous")"
if [[ -z "$version" || -z "$backend_id" || -z "$gateway_id" ]]; then
  echo "rollback status=refused reason=incomplete-previous" >&2
  exit 1
fi
echo "rollback backend=${backend_id} gateway=${gateway_id} alembic=upgrade-only"
if [[ "${1:-}" == "--dry-run" ]]; then
  echo "rollback status=dry-run"
  exit 0
fi

docker image inspect "$backend_id" >/dev/null
docker image inspect "$gateway_id" >/dev/null
docker tag "$backend_id" "engine-rag-backend:${version}"
docker tag "$gateway_id" "engine-rag-gateway:${version}"
cp -a "$previous" /etc/engine-rag/release.env

src=/opt/engine-rag/src
docker compose --env-file /etc/engine-rag/product.env \
  -f "$src/infra/compose.yml" -f "$src/infra/compose.vps.yml" \
  up -d --no-build --wait --wait-timeout 900
echo "rollback status=ok"

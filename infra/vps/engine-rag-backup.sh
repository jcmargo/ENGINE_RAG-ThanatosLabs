#!/bin/bash
# Falla cerrado: un directorio local no es un destino de backup.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "backup status=refused reason=root-required" >&2
  exit 1
fi

repo=""
if [[ -f /etc/engine-rag/backup.env ]]; then
  repo="$(awk -F= '/^RESTIC_REPOSITORY=/{print substr($0, index($0, "=") + 1)}' /etc/engine-rag/backup.env | tail -n 1)"
  repo="${repo%\"}"
  repo="${repo#\"}"
fi

case "$repo" in
  s3:*|sftp:*|rest:*|rclone:*|b2:*|azure:*|gs:*|swift:*)
    ;;
  *)
    echo "backup status=blocked reason=external-repository-required" >&2
    exit 1
    ;;
esac

src=/opt/engine-rag/src
export RESTIC_REPOSITORY="$repo"
exec docker compose --env-file /etc/engine-rag/product.env \
  -f "$src/infra/compose.yml" -f "$src/infra/compose.vps.yml" \
  --profile backup run --rm backup

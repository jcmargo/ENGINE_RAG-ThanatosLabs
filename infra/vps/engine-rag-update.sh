#!/bin/bash
# Niega la actualización si no hay un repositorio restic externo.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "update status=refused reason=root-required" >&2
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
    echo "update status=blocked reason=backup-required" >&2
    exit 1
    ;;
esac

if [[ ! -f /etc/engine-rag/release.env ]]; then
  echo "update status=refused reason=missing-release" >&2
  exit 1
fi

if [[ "${1:-}" == "--dry-run" ]]; then
  echo "update status=ready repository=remote"
  exit 0
fi

cp -a /etc/engine-rag/release.env /etc/engine-rag/release.env.previous
exec /opt/engine-rag/bin/engine-rag-deploy

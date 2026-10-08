#!/bin/bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "watch status=refused reason=root-required" >&2
  exit 1
fi

fail=0
disk="$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}')"
if [[ "$disk" -ge 85 ]]; then
  echo "watch disk_percent=${disk} status=high" >&2
  fail=1
else
  echo "watch disk_percent=${disk} status=ok"
fi

avail="$(awk '/MemAvailable/ {print $2}' /proc/meminfo)"
if [[ "$avail" -lt 524288 ]]; then
  echo "watch memory_available_kb=${avail} status=low" >&2
  fail=1
else
  echo "watch memory_available_kb=${avail} status=ok"
fi

src=/opt/engine-rag/src
compose=(docker compose --env-file /etc/engine-rag/product.env -f "$src/infra/compose.yml" -f "$src/infra/compose.vps.yml")
if ! "${compose[@]}" exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=10)"; then
  echo "watch readyz status=down" >&2
  fail=1
else
  echo "watch readyz status=ok"
fi

queue="$("${compose[@]}" exec -T postgres psql -U "${RAG_POSTGRES_USER:-engine_rag}" -d "${RAG_POSTGRES_DB:-engine_rag}" -tAc "select count(*) from ingestion_jobs where status in ('dead','failed')" || echo err)"
queue="${queue//[[:space:]]/}"
if [[ "$queue" == "err" || "$queue" -gt 0 ]]; then
  echo "watch queue_failed=${queue} status=high" >&2
  fail=1
else
  echo "watch queue_failed=${queue} status=ok"
fi

if [[ -f /etc/engine-rag/backup.env ]] && grep -Eq '^RESTIC_REPOSITORY=(s3:|sftp:|rest:|rclone:|b2:|azure:|gs:|swift:)' /etc/engine-rag/backup.env; then
  if ! systemctl is-failed --quiet engine-rag-backup.service; then
    echo "watch backup status=ok"
  else
    echo "watch backup status=failed" >&2
    fail=1
  fi
else
  echo "watch backup status=blocked"
fi

exit "$fail"

#!/bin/sh
set -eu
export HOME=/tmp
export XDG_CONFIG_HOME=/tmp
export XDG_DATA_HOME=/tmp
if [ "${RAG_PUBLIC_HTTPS:-false}" = "true" ]; then
  sed 's|__HSTS_LINE__|Strict-Transport-Security "max-age=31536000; includeSubDomains"|' \
    /etc/caddy/Caddyfile.template > /tmp/Caddyfile
else
  sed 's|__HSTS_LINE__||' /etc/caddy/Caddyfile.template > /tmp/Caddyfile
fi
exec caddy run --config /tmp/Caddyfile --adapter caddyfile

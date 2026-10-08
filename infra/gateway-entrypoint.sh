#!/bin/sh
set -eu
export HOME=/tmp
export XDG_CONFIG_HOME=/tmp
if [ "${RAG_TLS_MODE:-local}" = "public" ]; then
  export XDG_DATA_HOME=/data
  cp /etc/caddy/Caddyfile.public /tmp/Caddyfile
else
  export XDG_DATA_HOME=/tmp
  if [ "${RAG_PUBLIC_HTTPS:-false}" = "true" ]; then
    sed 's|__HSTS_LINE__|Strict-Transport-Security "max-age=31536000; includeSubDomains"|' \
      /etc/caddy/Caddyfile.template > /tmp/Caddyfile
  else
    sed 's|__HSTS_LINE__||' /etc/caddy/Caddyfile.template > /tmp/Caddyfile
  fi
fi
exec caddy run --config /tmp/Caddyfile --adapter caddyfile

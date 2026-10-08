#!/bin/bash
# Tira el tráfico que entra por la interfaz pública hacia puertos internos.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "docker-user status=refused reason=root-required" >&2
  exit 1
fi

for _ in $(seq 1 30); do
  if iptables -L DOCKER-USER >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
iptables -L DOCKER-USER >/dev/null

iface="$(ip -4 route show default | awk '{print $5; exit}')"
if [[ -z "$iface" ]]; then
  echo "docker-user status=refused reason=missing-interface" >&2
  exit 1
fi

rule=( -i "$iface" -p tcp -m multiport --dports 5432,6333,8000,11434 -j DROP )
if ! iptables -C DOCKER-USER "${rule[@]}" 2>/dev/null; then
  iptables -I DOCKER-USER 1 "${rule[@]}"
fi

if ip6tables -L DOCKER-USER >/dev/null 2>&1; then
  v6="$(ip -6 route show default | awk '{print $5; exit}')"
  if [[ -n "$v6" ]]; then
    rule6=( -i "$v6" -p tcp -m multiport --dports 5432,6333,8000,11434 -j DROP )
    if ! ip6tables -C DOCKER-USER "${rule6[@]}" 2>/dev/null; then
      ip6tables -I DOCKER-USER 1 "${rule6[@]}"
    fi
  fi
fi

if ! ufw status | grep -q '172.28.0.1 11434'; then
  ufw allow from 172.28.0.0/24 to 172.28.0.1 port 11434 proto tcp comment engine-rag-ollama
fi

echo "docker-user status=ok interface=${iface}"

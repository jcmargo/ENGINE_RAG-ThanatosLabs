#!/bin/bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "reboot status=refused reason=root-required" >&2
  exit 1
fi

systemctl reboot

#!/usr/bin/env bash
# Reversible service rollback. Keep device selection and private Apple records.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
    echo 'Run this rollback script with sudo on the Pi.' >&2
    exit 1
fi
systemctl disable --now ulab-relay.service
systemctl stop ulab-iproxy.service
systemctl daemon-reload
echo 'Relay disabled. Existing gadget configuration, SSH, and host trust are preserved.'

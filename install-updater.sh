#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ $EUID -eq 0 ]] || { echo "Execute com sudo: sudo ./install-updater.sh" >&2; exit 1; }

install -m 755 "$project_dir/bin/bisq-auto-update" /usr/local/sbin/bisq-auto-update
install -m 644 "$project_dir/systemd/bisq-auto-update.service" /etc/systemd/system/
install -m 644 "$project_dir/systemd/bisq-auto-update.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now bisq-auto-update.timer
systemctl list-timers bisq-auto-update.timer --no-pager

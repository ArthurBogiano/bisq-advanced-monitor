#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

for command in python3 systemctl javac; do
    command -v "$command" >/dev/null || { echo "Dependência ausente: $command" >&2; exit 1; }
done
for binary in /usr/bin/bisq-daemon /usr/bin/bisq-cli; do
    [[ -x "$binary" ]] || { echo "Bisq headless ausente: $binary" >&2; exit 1; }
done

install -d "$HOME/.local/bin" "$HOME/.config/systemd/user" \
    "$HOME/.config/bisq" "$HOME/.config/bisq-monitor" \
    "$HOME/.local/state/bisq-monitor"
install -m 700 "$project_dir/src/bisq-offer-monitor.py" "$HOME/.local/bin/"
install -m 700 "$project_dir/bin/bisq-top" "$HOME/.local/bin/"
install -m 700 "$project_dir/bin/bisqctl" "$HOME/.local/bin/"
install -m 700 "$project_dir/bin/bisq-gui" "$HOME/.local/bin/"
install -m 700 "$project_dir/bin/bisq-offer-details" "$HOME/.local/bin/"
install -m 700 "$project_dir/bin/bisq-wallet-details" "$HOME/.local/bin/"
install -d "$HOME/.local/lib/bisq-monitor"
javac -cp '/opt/bisq-cli/lib/*:/opt/bisq-daemon/lib/*' -d "$HOME/.local/lib/bisq-monitor" \
    "$project_dir/src/BisqOfferDetails.java" "$project_dir/src/BisqWalletDetails.java"
install -m 644 "$project_dir/systemd/"*.service "$HOME/.config/systemd/user/"

if [[ ! -f "$HOME/.config/bisq/daemon.env" ]]; then
    install -m 600 "$project_dir/config/daemon.env.example" "$HOME/.config/bisq/daemon.env"
    echo "Edite ~/.config/bisq/daemon.env antes de iniciar o daemon." >&2
fi
if [[ ! -f "$HOME/.config/bisq-monitor/telegram.env" ]]; then
    install -m 600 "$project_dir/config/telegram.env.example" "$HOME/.config/bisq-monitor/telegram.env"
    echo "Edite ~/.config/bisq-monitor/telegram.env antes de iniciar o monitor." >&2
fi

systemctl --user daemon-reload
systemctl --user enable bisq-daemon.service bisq-offer-monitor.service

echo "Instalação concluída. Configure os arquivos .env e execute:"
echo "  systemctl --user start bisq-daemon.service bisq-offer-monitor.service"
echo "  bisq-top"

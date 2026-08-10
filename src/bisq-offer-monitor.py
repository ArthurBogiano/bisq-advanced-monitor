#!/usr/bin/env python3
"""Monitor Bisq offers and notify state/health changes through Telegram."""

import json
import logging
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
STATE_DIR = HOME / ".local/state/bisq-monitor"
STATE_FILE = STATE_DIR / "state.json"
ENV_FILE = HOME / ".config/bisq/daemon.env"
DB_DIR = HOME / ".local/share/Bisq/btc_mainnet/db"
CLI = "/usr/bin/bisq-cli"
API_PORT = os.getenv("BISQ_API_PORT", "9998")
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
INTERVAL = int(os.getenv("MONITOR_INTERVAL", "60"))
LOG = logging.getLogger("bisq-monitor")


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"offers": {}, "healthy": None, "dao_synced": None,
                "initialized": False}


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    temporary.replace(STATE_FILE)


def daemon_password():
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("BISQ_API_PASSWORD="):
            return line.split("=", 1)[1].strip().strip("\"").strip("'")
    raise RuntimeError("BISQ_API_PASSWORD não encontrada")


def cli(method, *args, timeout=25):
    command = [CLI, "--host=127.0.0.1", f"--port={API_PORT}",
               f"--password={daemon_password()}", method, *args]
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{method} excedeu o limite de {timeout}s") from None
    output = "\n".join(
        line for line in (result.stdout + "\n" + result.stderr).splitlines()
        if not line.startswith(("Aug ", "INFO:"))
    ).strip()
    if result.returncode:
        raise RuntimeError(output or f"{method} falhou ({result.returncode})")
    if output.startswith("Error:"):
        raise RuntimeError(output)
    return output


def telegram(method, data=None, timeout=20):
    encoded = urllib.parse.urlencode(data or {}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/{method}", data=encoded)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description", "erro desconhecido do Telegram"))
    return payload.get("result")


def send(state, text):
    telegram("sendMessage", {"chat_id": CHAT_ID, "text": text,
                              "disable_web_page_preview": "true"})
    return True


def emit(state, kind, title, detail, telegram_text=None):
    events = state.setdefault("events", [])
    events.append({"time": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                   "kind": kind, "title": title, "detail": detail})
    del events[:-100]
    if telegram_text:
        try:
            send(state, telegram_text)
        except Exception as exc:
            LOG.error("Falha ao enviar alerta ao Telegram: %s", exc)


def sanitize_state(state):
    try:
        password = daemon_password()
    except Exception:
        return
    for event in state.get("events", []):
        event["detail"] = str(event.get("detail", "")).replace(password, "<redacted>")
    if state.get("last_error"):
        state["last_error"] = str(state["last_error"]).replace(password, "<redacted>")


def currencies():
    output = cli("getpaymentaccts")
    result = {"BSQ"}
    for line in output.splitlines()[1:]:
        match = re.search(r"\s([A-Z]{3,5})\s+(?:PIX|SEPA|TRANSFERWISE|NATIONAL_BANK|BSQ_SWAP)\s+", line)
        if match:
            result.add(match.group(1))
    return sorted(result)


def parse_offers(output, currency):
    offers = {}
    for line in output.splitlines():
        if not re.match(r"^(YES|NO)\s+(BUY|SELL)\s+", line):
            continue
        fields = re.split(r"\s{2,}", line.strip())
        if len(fields) < 7:
            continue
        enabled, direction, price, btc_range, fiat_range = fields[:5]
        offer_id = fields[-1].strip()
        created = fields[-2].strip()
        payment = fields[-3].strip()
        offers[offer_id] = {
            "id": offer_id, "enabled": enabled == "YES", "direction": direction,
            "currency": currency, "price": price, "btc_range": btc_range,
            "fiat_range": fiat_range, "payment": payment, "created": created,
        }
    return offers


def collect_offers():
    result = {}
    for currency in currencies():
        for direction in ("buy", "sell"):
            output = cli("getmyoffers", f"--direction={direction}",
                         f"--currency-code={currency}")
            result.update(parse_offers(output, currency))
    return result


def collect_market_prices():
    prices = {}
    for currency in ("BRL", "EUR"):
        output = cli("getbtcprice", f"--currency-code={currency}")
        match = re.search(r"(?:price[^0-9]*|^)([0-9][0-9,]*(?:\.[0-9]+)?)", output,
                          re.IGNORECASE | re.MULTILINE)
        if not match:
            raise RuntimeError(f"Cotação BTC/{currency} inválida: {output}")
        prices[currency] = match.group(1).replace(",", "")
    return prices


def was_accepted(offer_id):
    needle = offer_id.encode()
    for name in ("PendingTrades", "ClosedTrades", "FailedTrades"):
        try:
            if needle in (DB_DIR / name).read_bytes():
                return True
        except OSError:
            pass
    return False


def offer_label(offer):
    side = "COMPRA" if offer["direction"] == "BUY" else "VENDA"
    return (f'{side} {offer["currency"]} | {offer["btc_range"]} BTC | '
            f'{offer["price"]} {offer["currency"]}/BTC | {offer["payment"]} | '
            f'ID …{offer["id"][-12:]}')


def check_once(state):
    try:
        dao_synced = cli("getdaostatus").strip().lower() == "true"
        offers = collect_offers()
        healthy = True
        error = None
    except Exception as exc:
        dao_synced, offers, healthy, error = (False, state.get("offers", {}),
                                               False, str(exc))

    try:
        market_prices = collect_market_prices()
        price_error = None
    except Exception as exc:
        market_prices = state.get("market_prices", {})
        price_error = str(exc)

    previous_price_error = state.get("price_error")
    if price_error and not previous_price_error:
        emit(state, "warning", "COTAÇÃO TEMPORARIAMENTE INDISPONÍVEL", price_error)
    elif previous_price_error and not price_error:
        emit(state, "recovered", "COTAÇÃO RECUPERADA",
             "BTC/BRL e BTC/EUR voltaram a responder.")

    previous_health = state.get("healthy")
    if previous_health is True and not healthy:
        emit(state, "error", "ERRO NA BISQ", f"API/daemon indisponível: {error[:500]}",
             f"🚨 ERRO NA BISQ\nAPI/daemon indisponível. As ofertas podem não estar publicadas.\nDetalhe: {error[:500]}")
    elif previous_health is False and healthy:
        emit(state, "recovered", "BISQ RECUPERADA", "API/daemon voltou a responder.",
             "✅ Bisq recuperada\nAPI/daemon voltou a responder.")

    previous_dao = state.get("dao_synced")
    if healthy and previous_dao is True and not dao_synced:
        emit(state, "warning", "DAO NÃO SINCRONIZADO", "As ofertas podem ficar indisponíveis.",
             "⚠️ Bisq perdeu a sincronização do DAO. As ofertas podem ficar indisponíveis.")
    elif healthy and previous_dao is False and dao_synced:
        emit(state, "recovered", "DAO SINCRONIZADO", "Sincronização restabelecida.",
             "✅ Sincronização do DAO restabelecida.")

    if healthy and state.get("initialized"):
        old = state.get("offers", {})
        for offer_id, offer in offers.items():
            before = old.get(offer_id)
            if before and before.get("enabled") and not offer["enabled"]:
                emit(state, "paused", "OFERTA PAUSADA", offer_label(offer),
                     "⏸️ OFERTA PAUSADA\n" + offer_label(offer))
            elif before and not before.get("enabled") and offer["enabled"]:
                emit(state, "active", "OFERTA REATIVADA", offer_label(offer),
                     "▶️ OFERTA REATIVADA\n" + offer_label(offer))
            elif not before:
                emit(state, "active", "NOVA OFERTA ATIVA", offer_label(offer),
                     "🆕 OFERTA ATIVA\n" + offer_label(offer))
        for offer_id, offer in old.items():
            if offer_id not in offers:
                accepted = was_accepted(offer_id)
                title = "OFERTA ACEITA" if accepted else "OFERTA REMOVIDA"
                emit(state, "accepted" if accepted else "removed", title, offer_label(offer),
                     ("🤝" if accepted else "🗑️") + f" {title}\n{offer_label(offer)}")

    state.update({"offers": offers, "healthy": healthy,
                  "market_prices": market_prices,
                  "price_error": price_error,
                  "dao_synced": dao_synced, "initialized": state.get("initialized") or healthy,
                  "last_check": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                  "last_error": error})
    save_state(state)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = load_state()
    sanitize_state(state)
    LOG.info("Monitor iniciado; intervalo=%ss; chat_id=%s", INTERVAL, CHAT_ID)
    while True:
        try:
            check_once(state)
        except Exception:
            LOG.exception("Falha inesperada no ciclo de monitoramento")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()

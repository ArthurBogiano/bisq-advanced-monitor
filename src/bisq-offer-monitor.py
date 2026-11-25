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
OFFER_DETAILS = str(HOME / ".local/bin/bisq-offer-details")
API_PORT = os.getenv("BISQ_API_PORT", "9998")
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
INTERVAL = int(os.getenv("MONITOR_INTERVAL", "60"))
OPPORTUNITY_CURRENCIES = tuple(
    item.strip().upper() for item in os.getenv(
        "OPPORTUNITY_CURRENCIES", "BRL,EUR").split(",") if item.strip())
MIN_ADVANTAGE_PCT = float(os.getenv("OPPORTUNITY_MIN_ADVANTAGE_PCT", "1.5"))
REALERT_IMPROVEMENT_PCT = float(os.getenv("OPPORTUNITY_REALERT_IMPROVEMENT_PCT", "1.0"))
MAX_OPPORTUNITY_ALERTS = int(os.getenv("OPPORTUNITY_MAX_ALERTS_PER_CYCLE", "3"))
OPPORTUNITY_RESET_HOURS = int(os.getenv("OPPORTUNITY_RESET_HOURS", "24"))
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


def parse_market_offers(output, currency):
    """Parse public offer-book rows (which do not contain the Enabled column)."""
    offers = {}
    for line in output.splitlines():
        if not re.match(r"^(BUY|SELL)\s+", line):
            continue
        fields = re.split(r"\s{2,}", line.strip())
        if len(fields) < 7:
            continue
        direction, price, btc_range, fiat_range = fields[:4]
        offer_id = fields[-1].strip()
        offers[offer_id] = {
            "id": offer_id, "direction": direction, "currency": currency,
            "price": price, "btc_range": btc_range, "fiat_range": fiat_range,
            "payment": fields[-3].strip(), "created": fields[-2].strip(),
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


def collect_market_offers():
    result = {}
    for currency in OPPORTUNITY_CURRENCIES:
        for direction in ("buy", "sell"):
            try:
                process = subprocess.run([OFFER_DETAILS, direction, currency], text=True,
                                         capture_output=True, timeout=35)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"detalhes de ofertas {direction}/{currency} excederam 35s") from None
            if process.returncode:
                raise RuntimeError(process.stderr.strip() or
                                   f"detalhes de ofertas falharam ({process.returncode})")
            for line in process.stdout.splitlines():
                fields = line.split("\t")
                if len(fields) != 17:
                    continue
                (offer_id, side, price, min_amount, amount, min_volume, volume,
                 payment, buyer_deposit, seller_deposit, tx_fee, maker_fee,
                 maker_fee_btc, created_ms, min_taker_fee, max_taker_fee,
                 fee_rate) = fields
                minimum, maximum = int(min_amount) / 1e8, int(amount) / 1e8
                btc_range = (f"{maximum:.8f}" if minimum == maximum else
                             f"{minimum:.8f} - {maximum:.8f}")
                fiat_range = volume if min_volume == volume else f"{min_volume} - {volume}"
                created = datetime.fromtimestamp(
                    int(created_ms) / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
                result[offer_id] = {
                    "id": offer_id, "direction": side, "currency": currency,
                    "price": price, "btc_range": btc_range, "fiat_range": fiat_range,
                    "payment": payment, "created": created,
                    "amount_sats": int(amount), "min_amount_sats": int(min_amount),
                    "buyer_deposit_sats": int(buyer_deposit),
                    "seller_deposit_sats": int(seller_deposit),
                    "tx_fee_sats": int(tx_fee), "maker_fee_sats": int(maker_fee),
                    "maker_fee_currency": "BTC" if maker_fee_btc == "true" else "BSQ",
                    "min_taker_fee_sats": int(min_taker_fee),
                    "max_taker_fee_sats": int(max_taker_fee),
                    "tx_fee_rate_sat_vbyte": int(fee_rate),
                }
    return result


def number(value):
    return float(str(value).replace(",", "").strip())


def format_number(value, decimals=2):
    rendered = f"{float(value):,.{decimals}f}"
    return rendered.replace(",", "X").replace(".", ",").replace("X", ".")


def format_money(value, currency):
    symbol = "R$" if currency == "BRL" else "€"
    return f"{symbol} {format_number(value)}"


def convert_fiat_range(value, source_currency, eur_brl):
    values = re.findall(r"[0-9][0-9,]*(?:\.[0-9]+)?", str(value))
    converted = []
    for raw in values:
        amount = number(raw)
        converted.append(amount / eur_brl if source_currency == "BRL"
                         else amount * eur_brl)
    target = "EUR" if source_currency == "BRL" else "BRL"
    return " – ".join(format_money(amount, target) for amount in converted)


def btc_amounts(value):
    values = re.findall(r"[0-9]+(?:\.[0-9]+)?", str(value))
    amounts = [float(raw) for raw in values]
    return (amounts[0], amounts[-1]) if amounts else (0.0, 0.0)


def format_range(values, formatter):
    low, high = values
    return formatter(low) if abs(low - high) < 1e-12 else f"{formatter(low)} – {formatter(high)}"


def opportunity_for(offer, market_prices):
    market = number(market_prices[offer["currency"]])
    price = number(offer["price"])
    if offer["direction"] == "SELL":
        action = "COMPRAR BTC"
        advantage = (market - price) / market * 100
    else:
        action = "VENDER BTC"
        advantage = (price - market) / market * 100
    deposit_sats = (offer.get("buyer_deposit_sats", 0) if offer["direction"] == "SELL"
                    else offer.get("seller_deposit_sats", 0))
    amount_sats = offer.get("amount_sats", 0)
    deposit_pct = deposit_sats / amount_sats * 100 if amount_sats else 0
    return {**offer, "action": action, "market_price": market,
            "offer_price": price, "advantage_pct": advantage,
            "taker_deposit_sats": deposit_sats,
            "taker_deposit_pct": deposit_pct}


def opportunity_label(item):
    return (f'{item["action"]} em {item["currency"]} | '
            f'vantagem {item["advantage_pct"]:.2f}% | '
            f'oferta {item["offer_price"]:,.2f} vs mercado '
            f'{item["market_price"]:,.2f} | {item["btc_range"]} BTC | '
            f'{item["payment"]} | ID …{item["id"][-12:]}')


def opportunity_report(item, market_prices):
    btc_brl = number(market_prices["BRL"])
    btc_eur = number(market_prices["EUR"])
    eur_brl = btc_brl / btc_eur
    currency = item["currency"]
    native_range = item.get("fiat_range") or "não informado"
    btc_range = btc_amounts(item.get("btc_range", ""))
    capital_brl = tuple(amount * item["offer_price"] *
                        (eur_brl if currency == "EUR" else 1) for amount in btc_range)
    capital_eur = tuple(amount * item["offer_price"] /
                        (eur_brl if currency == "BRL" else 1) for amount in btc_range)
    gross_native = tuple(abs(item["offer_price"] - item["market_price"]) * amount
                         for amount in btc_range)
    gross_brl = tuple(value * (eur_brl if currency == "EUR" else 1)
                      for value in gross_native)
    gross_eur = tuple(value / (eur_brl if currency == "BRL" else 1)
                      for value in gross_native)
    deposit_pct = float(item.get("taker_deposit_pct", 0))
    deposit_btc = tuple(amount * deposit_pct / 100 for amount in btc_range)
    mining_sats = int(item.get("tx_fee_rate_sat_vbyte", 0)) * 233
    taker_fee_sats = (int(item.get("min_taker_fee_sats", 0)),
                      int(item.get("max_taker_fee_sats", 0)))
    total_fee_btc = tuple((fee + mining_sats) / 1e8 for fee in taker_fee_sats)
    wallet_btc = tuple(amount + deposit + fee for amount, deposit, fee in
                       zip(btc_range, deposit_btc, total_fee_btc))
    if item["action"] == "COMPRAR BTC":
        wallet_btc = tuple(deposit + fee for deposit, fee in
                           zip(deposit_btc, total_fee_btc))
    net_native = tuple(gross - fee * item["market_price"] for gross, fee in
                       zip(gross_native, total_fee_btc))
    net_brl = tuple(value * (eur_brl if currency == "EUR" else 1)
                    for value in net_native)
    net_eur = tuple(value / (eur_brl if currency == "BRL" else 1)
                    for value in net_native)
    maker_side = "COMPRA BTC" if item["direction"] == "BUY" else "VENDA BTC"
    created = item.get("created", "não informada").replace("T", " ").replace("Z", " UTC")
    detected = datetime.now(timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M:%S %Z")
    price_lines = (
        f"🏷️ Oferta: {format_money(item['offer_price'], 'BRL')} / BTC\n"
        f"📊 Mercado BTC/BRL: {format_money(btc_brl, 'BRL')}\n"
    )
    range_lines = f"💵 Valor em {currency}: {native_range}\n"
    if currency == "EUR":
        offer_brl = item["offer_price"] * eur_brl
        converted_range = convert_fiat_range(native_range, currency, eur_brl)
        price_lines = (
            f"🏷️ Oferta: {format_money(item['offer_price'], 'EUR')} / BTC\n"
            f"🔄 Oferta em reais: {format_money(offer_brl, 'BRL')} / BTC\n"
            f"📊 Mercado BTC/EUR: {format_money(btc_eur, 'EUR')}\n"
            f"📊 Mercado BTC/BRL: {format_money(btc_brl, 'BRL')}\n"
            f"💱 EUR/BRL: R$ {format_number(eur_brl, 4)} por € 1\n"
            f"💱 BRL/EUR: € {format_number(1 / eur_brl, 4)} por R$ 1\n"
        )
        range_lines += f"🔁 Equivalente em BRL: {converted_range or 'não informado'}\n"
    required = (f"{format_range(btc_range, lambda value: format_number(value, 8))} BTC"
                if item["action"] == "VENDER BTC" else
                format_range(capital_brl if currency == "BRL" else capital_eur,
                             lambda value: format_money(value, currency)))
    capital_lines = (
        f"🇧🇷 Capital da negociação: {format_range(capital_brl, lambda value: format_money(value, 'BRL'))}\n"
    )
    profit_lines = (
        f"📈 Lucro/economia bruta: {format_range(gross_brl, lambda value: format_money(value, 'BRL'))}\n"
        f"✅ Resultado após taxas BTC estimadas: {format_range(net_brl, lambda value: format_money(value, 'BRL'))}\n"
    )
    if currency == "EUR":
        capital_lines = (
            f"🇪🇺 Capital da negociação: {format_range(capital_eur, lambda value: format_money(value, 'EUR'))}\n"
            f"🇧🇷 Equivalente em reais: {format_range(capital_brl, lambda value: format_money(value, 'BRL'))}\n"
        )
        profit_lines = (
            f"📈 Lucro/economia bruta: {format_range(gross_eur, lambda value: format_money(value, 'EUR'))}\n"
            f"✅ Resultado após taxas BTC estimadas: {format_range(net_eur, lambda value: format_money(value, 'EUR'))}\n"
            f"🇧🇷 Resultado estimado em reais: {format_range(net_brl, lambda value: format_money(value, 'BRL'))}\n"
        )
    return (
        "🚀 RELATÓRIO DE OPORTUNIDADE — BISQ\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Ação para você: {item['action']}\n"
        f"📈 Vantagem estimada: {format_number(item['advantage_pct'])}%\n"
        f"📋 Ordem do anunciante: {maker_side}\n"
        f"🌍 Mercado: {currency}\n\n"
        "💰 PREÇOS\n"
        f"{price_lines}\n"
        "📦 LIMITES DA ORDEM\n"
        f"₿ Quantidade: {item.get('btc_range', 'não informada')} BTC\n"
        f"{range_lines}\n"
        "💼 CAPITAL E RESULTADO ESTIMADOS\n"
        f"₿ BTC negociado: {format_range(btc_range, lambda value: format_number(value, 8))} BTC\n"
        f"{capital_lines}"
        f"👛 Principal necessário: {required}\n"
        f"🔐 Depósito de segurança: {format_number(deposit_pct)}% = "
        f"{format_range(deposit_btc, lambda value: format_number(value, 8))} BTC\n"
        f"🧾 Taxa do taker + mineração estimada: {format_range(total_fee_btc, lambda value: format_number(value, 8))} BTC\n"
        f"👛 BTC necessário na carteira: {format_range(wallet_btc, lambda value: format_number(value, 8))} BTC\n"
        f"{profit_lines}"
        "ℹ️ Depósito retorna ao concluir a negociação; custos bancários não estão incluídos\n\n"
        "🏦 DETALHES\n"
        f"💳 Pagamento: {item.get('payment', 'não informado')}\n"
        f"🕒 Criada em: {created}\n"
        f"🔎 Detectada em: {detected}\n"
        f"🆔 ID completo: {item['id']}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ Sinal automático. Confirme preço, limites, taxas, conta e dados da contraparte antes de negociar."
    )


def evaluate_opportunities(state, public_offers, market_prices, my_offers):
    own_ids = set(my_offers)
    candidates = []
    for offer in public_offers.values():
        if offer["id"] in own_ids or offer["currency"] not in market_prices:
            continue
        try:
            item = opportunity_for(offer, market_prices)
        except (ValueError, ZeroDivisionError):
            continue
        if item["advantage_pct"] >= MIN_ADVANTAGE_PCT:
            candidates.append(item)
    candidates.sort(key=lambda item: item["advantage_pct"], reverse=True)

    now = datetime.now(timezone.utc).astimezone()
    alerts = state.setdefault("opportunity_alerts", {})
    alertable = []
    for item in candidates:
        previous = alerts.get(item["id"])
        improved = (previous and item["advantage_pct"] >=
                    float(previous.get("advantage_pct", 0)) + REALERT_IMPROVEMENT_PCT)
        if not previous or improved:
            alertable.append(item)
        best_advantage = max(item["advantage_pct"],
                             float((previous or {}).get("advantage_pct", 0)))
        alerts[item["id"]] = {"advantage_pct": best_advantage,
                              "last_seen": now.isoformat(timespec="seconds")}

    for item in alertable[:MAX_OPPORTUNITY_ALERTS]:
        detail = opportunity_label(item)
        emit(state, "opportunity", "OPORTUNIDADE DE MERCADO", detail,
             opportunity_report(item, market_prices))

    for offer_id in list(alerts):
        try:
            last_seen = datetime.fromisoformat(alerts[offer_id]["last_seen"])
            expired = (now - last_seen).total_seconds() > OPPORTUNITY_RESET_HOURS * 3600
        except (KeyError, TypeError, ValueError):
            expired = True
        if expired:
            alerts.pop(offer_id, None)
    return candidates[:20]


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

    try:
        public_offers = collect_market_offers() if healthy else {}
        opportunities = evaluate_opportunities(
            state, public_offers, market_prices, offers) if market_prices else []
        opportunity_error = None
    except Exception as exc:
        opportunities = state.get("opportunities", [])
        opportunity_error = str(exc)

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
                  "opportunities": opportunities,
                  "opportunity_error": opportunity_error,
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

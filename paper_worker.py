import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from market_microstructure import estimate_market_fill
from orderbook_stream import OrderBookStream
from paper_engine import PaperLedger, RiskLimits, evaluate_risk
from polymarket_api import get_order_book, get_user_trades


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = DATA_DIR / "paper_config.json"
STATUS_PATH = DATA_DIR / "paper_worker_status.json"
STOP_PATH = DATA_DIR / "paper_worker.stop"
PID_PATH = DATA_DIR / "paper_worker.pid"


def load_wallets():
    wallets = []
    for line in (ROOT / "wallets.txt").read_text(encoding="utf-8-sig").splitlines():
        address = line.split("#", 1)[0].strip().lower()
        if address:
            wallets.append(address)
    return wallets


def load_config():
    defaults = {
        "delay_seconds": 1,
        "poll_seconds": 3,
        "max_signal_age_seconds": 30,
        "leader_size_pct": 10.0,
        "fee_rate": 0.03,
        "risk_limits": {},
    }
    if CONFIG_PATH.exists():
        defaults.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    defaults["risk_limits"] = RiskLimits(
        **defaults.get("risk_limits", {})
    )
    return defaults


def write_status(**values):
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        **values,
    }
    temporary = STATUS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, STATUS_PATH)


def signal_from_trade(wallet, trade):
    transaction_hash = str(trade.get("transactionHash", ""))
    token_id = str(trade.get("asset", ""))
    timestamp = int(float(trade.get("timestamp", 0) or 0))
    return {
        "signal_key": "|".join([
            transaction_hash, token_id, str(timestamp), str(trade.get("side", ""))
        ]),
        "leader_wallet": wallet,
        "token_id": token_id,
        "condition_id": str(trade.get("conditionId", "")),
        "title": str(trade.get("title", "")),
        "side": str(trade.get("side", "")).upper(),
        "leader_price": float(trade.get("price", 0) or 0),
        "leader_size": float(trade.get("size", 0) or 0),
        "timestamp": timestamp,
    }


def process_signal(signal, ledger, config):
    limits = config["risk_limits"]
    age = time.time() - signal["timestamp"]
    if age > config["max_signal_age_seconds"]:
        ledger.record_rejection(signal, "sygnal jest za stary")
        return
    remaining_delay = max(0, config["delay_seconds"] - age)
    if remaining_delay:
        time.sleep(remaining_delay)

    if signal["side"] == "SELL":
        positions = ledger.open_positions_for(
            signal["leader_wallet"], signal["token_id"]
        )
        shares = sum(float(row["shares"]) for row in positions)
        if shares <= 0:
            ledger.record_rejection(signal, "brak pozycji do zamkniecia")
            return
        fill = estimate_market_fill(
            get_order_book(signal["token_id"]),
            side="SELL",
            amount=shares,
            amount_type="shares",
            max_slippage_pct_points=limits.max_slippage_pct_points,
            fee_rate=config["fee_rate"],
        )
        if fill.fill_ratio < limits.min_fill_ratio:
            ledger.record_rejection(
                signal, "za mala plynnosc do wyjscia"
            )
            return
        ledger.record_exit(signal, fill)
        return
    if signal["side"] != "BUY":
        ledger.record_rejection(signal, "nieobslugiwany typ sygnalu")
        return

    totals = ledger.totals()
    bankroll = (
        limits.starting_bankroll
        + totals["daily_realized_pnl"]
        + totals["unrealized_pnl"]
    )
    stake = min(
        max(0, bankroll * limits.risk_per_trade_pct / 100),
        limits.max_stake_usdc,
        max(0, signal["leader_size"] * signal["leader_price"]
            * config["leader_size_pct"] / 100),
    )
    book = get_order_book(signal["token_id"])
    fill = estimate_market_fill(
        book,
        side="BUY",
        amount=stake,
        amount_type="usdc",
        max_slippage_pct_points=limits.max_slippage_pct_points,
        fee_rate=config["fee_rate"],
    )
    allowed, reason = evaluate_risk(
        ledger, limits, signal["condition_id"], stake, fill
    )
    if allowed:
        ledger.record_fill(signal, stake, fill)
    else:
        ledger.record_rejection(signal, reason)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    DATA_DIR.mkdir(exist_ok=True)
    STOP_PATH.unlink(missing_ok=True)
    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    ledger = PaperLedger(DATA_DIR / "paper_trading.db")
    config = load_config()
    wallets = load_wallets()
    bootstrapped = False
    stream = OrderBookStream(DATA_DIR / "orderbook_events.jsonl")
    stream_assets = set()

    try:
        while not STOP_PATH.exists():
            new_signals = []
            errors = []
            for wallet in wallets:
                try:
                    trades = sorted(
                        get_user_trades(wallet, limit=100),
                        key=lambda row: int(float(row.get("timestamp", 0) or 0)),
                    )
                    for trade in trades:
                        signal = signal_from_trade(wallet, trade)
                        if not signal["token_id"] or ledger.has_signal(signal["signal_key"]):
                            continue
                        if not bootstrapped:
                            ledger.record_rejection(
                                signal, "historyczny sygnal przy starcie workera"
                            )
                            continue
                        new_signals.append(signal)
                except Exception as exc:
                    errors.append(f"{wallet}: {exc}")

            bootstrapped = True
            for signal in new_signals:
                try:
                    process_signal(signal, ledger, config)
                except Exception as exc:
                    ledger.record_rejection(signal, f"blad paper execution: {exc}")

            _, positions = ledger.frames()
            active_assets = set(
                positions.loc[positions["status"] == "OPEN", "token_id"].astype(str)
            ) if not positions.empty else set()
            if active_assets and active_assets != stream_assets:
                stream.stop()
                stream = OrderBookStream(DATA_DIR / "orderbook_events.jsonl")
                stream.start(active_assets)
                stream_assets = active_assets

            marks = {}
            for asset in active_assets:
                book = stream.snapshot(asset)
                if not book:
                    continue
                bids = book.get("bids", [])
                if bids:
                    marks[asset] = max(float(level["price"]) for level in bids)
            if marks:
                ledger.mark_positions(marks)

            write_status(
                running=True,
                wallets=len(wallets),
                new_signals=len(new_signals),
                websocket_connected=stream.connected,
                websocket_assets=len(stream_assets),
                websocket_error=stream.last_error,
                errors=errors,
                totals=ledger.totals(),
            )
            if args.once:
                break
            time.sleep(config["poll_seconds"])
    finally:
        stream.stop()
        write_status(running=False, reason="worker stopped")
        PID_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

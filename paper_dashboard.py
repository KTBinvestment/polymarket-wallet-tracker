import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from market_microstructure import estimate_market_fill
from paper_engine import PaperLedger, RiskLimits
from polymarket_api import get_order_book


def _config_path(data_dir: Path) -> Path:
    return data_dir / "paper_config.json"


def load_paper_config(data_dir: Path):
    defaults = {
        "delay_seconds": 1,
        "poll_seconds": 3,
        "max_signal_age_seconds": 30,
        "leader_size_pct": 10.0,
        "fee_rate": 0.03,
        "risk_limits": {},
    }
    path = _config_path(data_dir)
    if path.exists():
        try:
            defaults.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return defaults


def save_paper_config(data_dir: Path, config):
    path = _config_path(data_dir)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2), encoding="utf-8")
    temporary.replace(path)


def _worker_running(data_dir: Path) -> bool:
    pid_path = data_dir / "paper_worker.pid"
    if not pid_path.exists():
        return False
    try:
        import psutil
        return psutil.pid_exists(int(pid_path.read_text(encoding="utf-8")))
    except Exception:
        return False


def _start_worker(root: Path):
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [sys.executable, str(root / "paper_worker.py")],
        cwd=root,
        creationflags=creation_flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def render_paper_dashboard(frame: pd.DataFrame, data_dir: Path):
    root = Path(__file__).resolve().parent
    data_dir = Path(data_dir)
    data_dir.mkdir(exist_ok=True)
    ledger = PaperLedger(data_dir / "paper_trading.db")
    config = load_paper_config(data_dir)
    risk = RiskLimits(**config.get("risk_limits", {}))

    st.header("Live paper trading")
    st.caption(
        "Ten proces nie sklada realnych zlecen. Obserwuje liderow, czyta publiczny "
        "orderbook, symuluje wykonanie i zapisuje wynik do lokalnej bazy."
    )

    with st.expander("Konfiguracja i limity ryzyka", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        delay = c1.selectbox(
            "Opoznienie",
            [1, 2, 5],
            index=[1, 2, 5].index(int(config.get("delay_seconds", 1))),
            format_func=lambda value: f"{value}s",
        )
        bankroll = c2.number_input(
            "Kapital paper (USDC)", min_value=10.0,
            value=float(risk.starting_bankroll), step=100.0,
        )
        risk_pct = c3.number_input(
            "Ryzyko na sygnal (%)", min_value=0.1, max_value=10.0,
            value=float(risk.risk_per_trade_pct), step=0.1,
        )
        max_stake = c4.number_input(
            "Maks. stawka (USDC)", min_value=1.0,
            value=float(risk.max_stake_usdc), step=5.0,
        )

        c5, c6, c7, c8 = st.columns(4)
        daily_loss = c5.number_input(
            "Limit straty dziennej", min_value=1.0,
            value=float(risk.max_daily_loss_usdc), step=5.0,
        )
        market_exposure = c6.number_input(
            "Ekspozycja na rynek", min_value=1.0,
            value=float(risk.max_market_exposure_usdc), step=5.0,
        )
        total_exposure = c7.number_input(
            "Ekspozycja laczna", min_value=1.0,
            value=float(risk.max_total_exposure_usdc), step=10.0,
        )
        open_positions = c8.number_input(
            "Maks. otwartych pozycji", min_value=1, max_value=100,
            value=int(risk.max_open_positions), step=1,
        )

        c9, c10, c11 = st.columns(3)
        max_spread = c9.number_input(
            "Maks. spread (pp)", min_value=0.0,
            value=float(risk.max_spread_pct_points), step=0.25,
        )
        max_slippage = c10.number_input(
            "Maks. poslizg (pp)", min_value=0.0,
            value=float(risk.max_slippage_pct_points), step=0.25,
        )
        min_fill = c11.slider(
            "Minimalne wykonanie (%)", 1, 100,
            int(risk.min_fill_ratio * 100),
        )

        if st.button("Zapisz konfiguracje paper tradingu"):
            save_paper_config(data_dir, {
                **config,
                "delay_seconds": delay,
                "risk_limits": {
                    **risk.__dict__,
                    "starting_bankroll": bankroll,
                    "risk_per_trade_pct": risk_pct,
                    "max_stake_usdc": max_stake,
                    "max_daily_loss_usdc": daily_loss,
                    "max_market_exposure_usdc": market_exposure,
                    "max_total_exposure_usdc": total_exposure,
                    "max_open_positions": int(open_positions),
                    "max_spread_pct_points": max_spread,
                    "max_slippage_pct_points": max_slippage,
                    "min_fill_ratio": min_fill / 100,
                },
            })
            st.success("Konfiguracja zapisana.")

    worker_status = {}
    status_path = data_dir / "paper_worker_status.json"
    if status_path.exists():
        try:
            worker_status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    running = _worker_running(data_dir)
    emergency = ledger.emergency_stop()

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Worker", "DZIALA" if running else "STOP")
    s2.metric("WebSocket", "ONLINE" if worker_status.get("websocket_connected") else "OFFLINE")
    s3.metric("Awaryjny STOP", "WLACZONY" if emergency else "wylaczony")
    s4.metric("Monitorowane tokeny", worker_status.get("websocket_assets", 0))

    b1, b2, b3 = st.columns(3)
    if b1.button("Uruchom paper trading", disabled=running or emergency):
        _start_worker(root)
        st.success("Paper worker uruchomiony.")
        st.rerun()
    if b2.button("Zatrzymaj paper trading", disabled=not running):
        (data_dir / "paper_worker.stop").write_text("stop", encoding="utf-8")
        st.warning("Wyslano polecenie zatrzymania.")
    if b3.button(
        "Wylacz awaryjnie" if not emergency else "Odblokuj po kontroli",
        type="primary" if not emergency else "secondary",
    ):
        ledger.set_emergency_stop(not emergency)
        if not emergency:
            (data_dir / "paper_worker.stop").write_text("stop", encoding="utf-8")
        st.rerun()

    totals = ledger.totals()
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Otwarte pozycje", totals["open_positions"])
    p2.metric("Ekspozycja", f"{totals['total_exposure']:.2f} USDC")
    p3.metric("P/L niezrealizowany", f"{totals['unrealized_pnl']:.2f} USDC")
    p4.metric("P/L dzisiaj", f"{totals['daily_realized_pnl']:.2f} USDC")

    st.subheader("Test aktualnego orderbooka")
    assets = []
    if "asset" in frame.columns:
        assets = [
            str(value) for value in frame["asset"].dropna().astype(str).unique()
            if value
        ][:200]
    if assets:
        selected_asset = st.selectbox("Token", assets)
        amount = st.number_input(
            "Testowa kwota (USDC)", min_value=1.0, value=25.0, step=5.0
        )
        if st.button("Sprawdz plynnosc teraz"):
            try:
                fill = estimate_market_fill(
                    get_order_book(selected_asset),
                    side="BUY",
                    amount=amount,
                    max_slippage_pct_points=max_slippage,
                    fee_rate=float(config.get("fee_rate", 0.03)),
                )
                st.json(fill.to_dict())
            except Exception as exc:
                st.error(f"Nie udalo sie pobrac orderbooka: {exc}")
    else:
        st.info("Brak tokenow w migawce.")

    signals, positions = ledger.frames()
    st.subheader("Dziennik paper tradingu")
    if signals.empty:
        st.info("Brak sygnalow. Uruchom worker i pozostaw komputer wlaczony.")
    else:
        st.dataframe(signals, use_container_width=True, hide_index=True)
    if not positions.empty:
        st.subheader("Pozycje paper")
        st.dataframe(positions, use_container_width=True, hide_index=True)

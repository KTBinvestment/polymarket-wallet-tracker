from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from polymarket_api import (
    DEFAULT_LIMIT,
    PolymarketAPIError,
    get_user_activity,
    get_user_trades,
    test_data_api,
)

SPORT_KEYWORDS = [
    " vs ", " v ", "spread:", "o/u", "over", "under", "will ",
    "nba", "wnba", "nfl", "nhl", "mlb", "atp", "wta", "ufc", "fifa",
    "premier league", "champions league", "roland garros", "tennis", "baseball",
    "soccer", "football", "basketball", "hockey", "marlins", "red sox",
]

COPY_DELAYS_SECONDS = [1, 2, 5]

st.set_page_config(page_title="Polymarket Copy Research", layout="wide")
st.title("Polymarket Wallet Tracker - etap 3")
st.caption(
    "Bezpieczny research portfeli Polymarket. Aplikacja nie handluje, "
    "nie uzywa kluczy prywatnych i nie laczy sie z Twoim portfelem."
)

wallets_file = Path("wallets.txt")
wallets_file.touch(exist_ok=True)
Path("data").mkdir(exist_ok=True)


def short_wallet(w: str) -> str:
    return f"{w[:6]}...{w[-4:]}" if isinstance(w, str) and len(w) > 12 else str(w)


def is_sport_title(title: str) -> bool:
    t = str(title or "").lower()
    return any(k in t for k in SPORT_KEYWORDS)


def normalize_number(series):
    return pd.to_numeric(series, errors="coerce")


def parse_wallets(text: str):
    return [w.strip() for w in text.splitlines() if w.strip() and not w.strip().startswith("#")]


def fetch_wallet_records(wallet: str, limit: int):
    try:
        return get_user_trades(wallet, limit=limit), "trades", None
    except PolymarketAPIError as trades_error:
        try:
            records = get_user_activity(wallet, limit=limit)
            warning = f"{wallet}: /trades error: {trades_error}; uzyto fallback /activity."
            return records, "activity", warning
        except PolymarketAPIError as activity_error:
            raise PolymarketAPIError(
                f"/trades: {trades_error}; /activity: {activity_error}"
            ) from activity_error


def append_records(target, records, wallet: str, source: str):
    for item in records:
        row = dict(item)
        row["watchedWallet"] = wallet
        row["walletShort"] = short_wallet(wallet)
        row["source"] = source
        target.append(row)


def same_market_candidates(history: pd.DataFrame, leader: pd.Series, target_time):
    candidates = history[history["datetime_utc"] >= target_time].copy()
    if candidates.empty:
        return candidates

    if "conditionId" in candidates.columns and pd.notna(leader.get("conditionId")):
        candidates = candidates[candidates["conditionId"] == leader.get("conditionId")]
    elif "title" in candidates.columns and pd.notna(leader.get("title")):
        candidates = candidates[candidates["title"] == leader.get("title")]

    if "asset" in candidates.columns and pd.notna(leader.get("asset")):
        candidates = candidates[candidates["asset"] == leader.get("asset")]
    elif "outcome" in candidates.columns and pd.notna(leader.get("outcome")):
        candidates = candidates[candidates["outcome"] == leader.get("outcome")]

    if "_row_id" in candidates.columns:
        candidates = candidates[candidates["_row_id"] != leader.get("_row_id")]

    return candidates.sort_values("datetime_utc", ascending=True)


def slippage_for_side(side: str, leader_price: float, copy_price: float):
    side = str(side or "").upper()
    if side == "BUY":
        return copy_price - leader_price
    if side == "SELL":
        return leader_price - copy_price
    return abs(copy_price - leader_price)


def simulate_copy_entries(
    frame: pd.DataFrame,
    max_slippage_pct: float,
    max_leader_rows: int,
    max_match_seconds: int,
):
    required = {"datetime_utc", "price_num"}
    if frame.empty or not required.issubset(frame.columns):
        return pd.DataFrame()

    history = frame.copy().reset_index(drop=True)
    history["_row_id"] = history.index
    history = history.dropna(subset=["datetime_utc", "price_num"]).sort_values("datetime_utc")
    if history.empty:
        return pd.DataFrame()

    leaders = history.sort_values("datetime_utc", ascending=False).head(max_leader_rows)
    max_slippage = max_slippage_pct / 100
    rows = []

    for _, leader in leaders.iterrows():
        leader_price = float(leader["price_num"])
        for delay_s in COPY_DELAYS_SECONDS:
            target_time = leader["datetime_utc"] + pd.Timedelta(seconds=delay_s)
            candidates = same_market_candidates(history, leader, target_time)

            row = {
                "leader_time": leader["datetime_utc"],
                "delay_s": delay_s,
                "wallet": leader.get("walletShort", ""),
                "side": leader.get("side", ""),
                "outcome": leader.get("outcome", ""),
                "title": leader.get("title", ""),
                "leader_price": leader_price,
                "copy_time": pd.NaT,
                "copy_price": None,
                "seconds_after_move": None,
                "slippage_pct_points": None,
                "result": "brak pozniejszej ceny",
            }

            if not candidates.empty:
                copied = candidates.iloc[0]
                copy_price = float(copied["price_num"])
                slippage = slippage_for_side(leader.get("side", ""), leader_price, copy_price)
                seconds_after = (copied["datetime_utc"] - leader["datetime_utc"]).total_seconds()
                matched_in_time = seconds_after <= max_match_seconds
                result = "OK" if slippage <= max_slippage else "za duzy poslizg"
                if not matched_in_time:
                    result = "brak ceny w oknie czasowym"
                row.update({
                    "copy_time": copied["datetime_utc"],
                    "copy_price": copy_price,
                    "seconds_after_move": seconds_after,
                    "seconds_after_for_stats": seconds_after if matched_in_time else None,
                    "slippage_pct_points": slippage * 100 if matched_in_time else None,
                    "result": result,
                })

            rows.append(row)

    return pd.DataFrame(rows)


with st.sidebar:
    st.header("Portfele")
    default_wallets = "\n".join([
        line.strip()
        for line in wallets_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ])
    wallets_text = st.text_area(
        "Wklej adresy portfeli, po jednym w linii",
        value=default_wallets,
        height=180,
    )
    limit = st.slider("Ile rekordow pobierac na portfel", 10, 500, DEFAULT_LIMIT, step=10)
    only_sports = st.checkbox("Pokaz tylko prawdopodobne rynki sportowe", value=True)
    min_size = st.number_input("Minimalny size", min_value=0.0, value=0.0, step=10.0)

    st.header("Symulator")
    max_slippage_pct = st.number_input(
        "Maksymalny poslizg w punktach procentowych",
        min_value=0.0,
        max_value=20.0,
        value=2.0,
        step=0.5,
    )
    max_sim_rows = st.slider("Ile ostatnich ruchow symulowac", 10, 200, 50, step=10)
    max_match_seconds = st.slider(
        "Maksymalny czas dopasowania ceny (sekundy)",
        5,
        600,
        30,
        step=5,
    )

    sidebar_wallets = parse_wallets(wallets_text)
    if st.button("Test API"):
        test_wallet = sidebar_wallets[0] if sidebar_wallets else None
        with st.spinner("Sprawdzam data-api.polymarket.com..."):
            try:
                result = test_data_api(test_wallet)
                st.success(result["message"])
            except (PolymarketAPIError, ValueError) as exc:
                st.error(f"Blad polaczenia z Data API: {exc}")

    save = st.button("Zapisz portfele")
    if save:
        wallets_file.write_text(wallets_text.strip() + "\n", encoding="utf-8")
        st.success("Zapisano wallets.txt")

wallets = parse_wallets(wallets_text)

if not wallets:
    st.info("Wklej pierwszy adres portfela Polymarket po lewej stronie i kliknij 'Zapisz portfele'.")
    st.stop()

all_rows = []
errors = []
status_rows = []

st.subheader("Status portfeli")
status_panel = st.container()

for wallet in wallets:
    wallet_label = short_wallet(wallet)
    status_row = {
        "portfel": wallet_label,
        "status": "pobieram",
        "rekordy": 0,
        "zrodlo": "",
        "szczegoly": "",
    }
    status_rows.append(status_row)

    with status_panel:
        status_slot = st.empty()
    status_slot.info(f"{wallet_label}: pobieram")

    try:
        records, source, warning = fetch_wallet_records(wallet, limit=limit)
        if warning:
            errors.append(warning)

        status_row["zrodlo"] = source
        status_row["rekordy"] = len(records)

        if records:
            append_records(all_rows, records, wallet, source)
            status_row["status"] = f"pobrano {len(records)} rekordow"
            status_slot.success(f"{wallet_label}: pobrano {len(records)} rekordow ({source})")
        else:
            status_row["status"] = "brak danych"
            status_slot.warning(f"{wallet_label}: brak danych ({source})")
    except (PolymarketAPIError, ValueError) as exc:
        message = str(exc)
        status_row["status"] = "blad polaczenia"
        status_row["szczegoly"] = message
        errors.append(f"{wallet}: {message}")
        status_slot.error(f"{wallet_label}: blad polaczenia")

st.dataframe(pd.DataFrame(status_rows), use_container_width=True, hide_index=True)

if errors:
    with st.expander("Bledy / ostrzezenia"):
        for err in errors:
            st.write(err)

if not all_rows:
    st.warning("Nie pobrano zadnych transakcji. Sprawdz adresy portfeli albo kliknij 'Test API' w panelu bocznym.")
    st.stop()

df = pd.DataFrame(all_rows)

if "timestamp" in df.columns:
    df["datetime_utc"] = pd.to_datetime(df["timestamp"], unit="s", errors="coerce", utc=True)
    df = df.sort_values("datetime_utc", ascending=False, na_position="last")
else:
    df["datetime_utc"] = pd.NaT

if "title" not in df.columns:
    df["title"] = ""
if "size" not in df.columns:
    df["size"] = 0
if "price" not in df.columns:
    df["price"] = None
if "side" not in df.columns:
    df["side"] = ""
if "outcome" not in df.columns:
    df["outcome"] = ""

df["size_num"] = normalize_number(df["size"])
df["price_num"] = normalize_number(df["price"])
df["notional_est"] = df["size_num"] * df["price_num"]
df["is_sport_guess"] = df["title"].apply(is_sport_title)

filtered = df.copy()
if only_sports:
    filtered = filtered[filtered["is_sport_guess"] == True]
if min_size > 0:
    filtered = filtered[filtered["size_num"].fillna(0) >= min_size]

# Save snapshots locally for later analysis.
df.to_csv(Path("data") / "latest_raw.csv", index=False)
filtered.to_csv(Path("data") / "latest_filtered.csv", index=False)

st.subheader("Szybki podglad")
col1, col2, col3, col4 = st.columns(4)
col1.metric("Portfele", len(wallets))
col2.metric("Pobrane rekordy", len(df))
col3.metric("Po filtrach", len(filtered))
col4.metric("Sport guess", int(df["is_sport_guess"].sum()))

if len(filtered) == 0:
    st.warning("Po filtrach nie zostaly zadne rekordy. Odznacz filtr sportowy albo zmniejsz minimalny size.")
    st.stop()

st.subheader("Symulator kopiowania 1s / 2s / 5s")
st.caption(
    "To jest tylko test historyczny na pobranych rekordach. Aplikacja nie sklada zlecen, "
    "tylko sprawdza, czy po wybranym opoznieniu widac podobna cene w tym samym rynku/outcome. "
    "Dopasowania pozniejsze niz limit z panelu bocznego sa odrzucane."
)
simulated = simulate_copy_entries(
    filtered,
    max_slippage_pct=max_slippage_pct,
    max_leader_rows=max_sim_rows,
    max_match_seconds=max_match_seconds,
)

if simulated.empty:
    st.warning("Symulator nie ma jeszcze danych do porownania. Zwieksz limit pobierania albo dodaj wiecej portfeli.")
else:
    sim_summary = (
        simulated.assign(ok=simulated["result"].eq("OK"))
        .groupby("delay_s", dropna=False)
        .agg(
            proby=("result", "count"),
            ok=("ok", "sum"),
            w_oknie_czasowym=("seconds_after_for_stats", "count"),
            mediana_poslizgu=("slippage_pct_points", "median"),
            mediana_czasu_po_ruchu=("seconds_after_for_stats", "median"),
        )
        .reset_index()
    )
    sim_summary["ok_pct"] = (sim_summary["ok"] / sim_summary["proby"] * 100).round(1)

    s1, s2, s5 = st.columns(3)
    for col, delay in zip([s1, s2, s5], COPY_DELAYS_SECONDS):
        row = sim_summary[sim_summary["delay_s"] == delay]
        if row.empty:
            col.metric(f"Po {delay}s", "brak danych")
        else:
            value = row.iloc[0]
            col.metric(f"Po {delay}s", f"{value['ok_pct']}% OK", f"{int(value['ok'])}/{int(value['proby'])} prob")

    st.caption(f"Liczymy tylko dopasowania znalezione maksymalnie {max_match_seconds}s po ruchu lidera.")
    st.dataframe(sim_summary, use_container_width=True, hide_index=True)

    sim_cols = [
        "leader_time", "delay_s", "wallet", "side", "outcome", "leader_price",
        "copy_price", "slippage_pct_points", "seconds_after_move", "result", "title"
    ]
    st.dataframe(simulated[sim_cols], use_container_width=True, height=360)

    sim_csv = simulated.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Pobierz CSV symulatora",
        data=sim_csv,
        file_name="polymarket_copy_simulator.csv",
        mime="text/csv",
    )

# Wallet ranking / research summary
st.subheader("Ranking obserwowanych portfeli - wersja research")
summary = (
    filtered.groupby(["watchedWallet", "walletShort"], dropna=False)
    .agg(
        trades=("title", "count"),
        unique_markets=("title", "nunique"),
        total_size=("size_num", "sum"),
        avg_size=("size_num", "mean"),
        avg_price=("price_num", "mean"),
        est_notional=("notional_est", "sum"),
        first_seen=("datetime_utc", "min"),
        last_seen=("datetime_utc", "max"),
    )
    .reset_index()
)
summary["activity_window"] = summary["first_seen"].astype(str) + " -> " + summary["last_seen"].astype(str)
summary = summary.sort_values(["trades", "est_notional"], ascending=False)
st.dataframe(
    summary[["walletShort", "trades", "unique_markets", "total_size", "avg_size", "avg_price", "est_notional", "activity_window", "watchedWallet"]],
    use_container_width=True,
    height=220,
)

st.subheader("Najwieksze ruchy")
biggest = filtered.sort_values("size_num", ascending=False).head(30)
preferred_cols_big = ["datetime_utc", "walletShort", "side", "outcome", "price", "size", "notional_est", "title"]
st.dataframe(biggest[[c for c in preferred_cols_big if c in biggest.columns]], use_container_width=True, height=320)

st.subheader("Ostatnie ruchy po filtrach")
preferred_cols = [
    "datetime_utc", "walletShort", "side", "outcome", "price", "size", "notional_est", "title", "eventSlug",
    "conditionId", "asset", "transactionHash", "watchedWallet", "source"
]
cols = [c for c in preferred_cols if c in filtered.columns] + [c for c in filtered.columns if c not in preferred_cols]
st.dataframe(filtered[cols], use_container_width=True, height=520)

csv = filtered[cols].to_csv(index=False).encode("utf-8")
st.download_button("Pobierz filtrowany CSV", data=csv, file_name="polymarket_wallet_filtered.csv", mime="text/csv")

raw_csv = df.to_csv(index=False).encode("utf-8")
st.download_button("Pobierz pelny RAW CSV", data=raw_csv, file_name="polymarket_wallet_raw.csv", mime="text/csv")

st.info("Etap 3: symulator pokazuje, czy historycznie po 1s/2s/5s nadal pojawiala sie podobna cena. To nadal tylko research, bez handlu.")
st.caption(f"Ostatnie odswiezenie: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

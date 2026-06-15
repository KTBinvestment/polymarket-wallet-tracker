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
from wallet_discovery import discover_copy_wallets, fetch_wallet_profit

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


def polymarket_profile_url(wallet: str) -> str:
    return f"https://polymarket.com/profile/{wallet}"


def trader_name(row, fallback_wallet: str) -> str:
    for key in ("name", "pseudonym"):
        value = row.get(key, "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return short_wallet(fallback_wallet)


def is_sport_title(title: str) -> bool:
    t = str(title or "").lower()
    return any(k in t for k in SPORT_KEYWORDS)


def normalize_number(series):
    return pd.to_numeric(series, errors="coerce")


def parse_wallets(text: str):
    wallets = []
    for line in text.splitlines():
        clean = line.split("#", 1)[0]
        clean = clean.replace("\ufeff", "").replace("\u200b", "").strip()
        if clean:
            wallets.append(clean.lower())
    return wallets


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
        row["traderName"] = trader_name(row, wallet)
        row["profileUrl"] = polymarket_profile_url(wallet)
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
                "traderName": leader.get("traderName", leader.get("walletShort", "")),
                "profileUrl": leader.get("profileUrl", polymarket_profile_url(leader.get("watchedWallet", ""))),
                "watchedWallet": leader.get("watchedWallet", ""),
                "side": leader.get("side", ""),
                "outcome": leader.get("outcome", ""),
                "title": leader.get("title", ""),
                "leader_price": leader_price,
                "leader_size": leader.get("size_num", None),
                "leader_notional_est": leader.get("notional_est", None),
                "copy_time": pd.NaT,
                "copy_price": None,
                "seconds_after_move": None,
                "seconds_after_for_stats": None,
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


def calculate_copy_stake(
    bankroll: float,
    leader_notional: float,
    risk_per_trade_pct: float,
    max_stake_usdc: float,
    leader_size_pct: float,
):
    risk_stake = bankroll * risk_per_trade_pct / 100
    stake_limits = [risk_stake, max_stake_usdc]
    if pd.notna(leader_notional) and float(leader_notional) > 0 and leader_size_pct > 0:
        stake_limits.append(float(leader_notional) * leader_size_pct / 100)
    return max(0.0, min(stake_limits))


def simulate_money_management(
    simulated: pd.DataFrame,
    selected_delay_s: int,
    starting_bankroll: float,
    risk_per_trade_pct: float,
    max_stake_usdc: float,
    leader_size_pct: float,
):
    if simulated.empty:
        return pd.DataFrame(), {}

    signals = simulated[simulated["delay_s"] == selected_delay_s].copy()
    if signals.empty:
        return pd.DataFrame(), {}

    signals = signals.sort_values("leader_time", ascending=True)
    bankroll = float(starting_bankroll)
    ledger = []

    for _, signal in signals.iterrows():
        result = signal.get("result", "")
        copied = result == "OK" and bankroll > 0
        stake = 0.0
        slippage_cost = 0.0
        action = "pomijam"

        if copied:
            stake = calculate_copy_stake(
                bankroll=bankroll,
                leader_notional=signal.get("leader_notional_est", 0),
                risk_per_trade_pct=risk_per_trade_pct,
                max_stake_usdc=max_stake_usdc,
                leader_size_pct=leader_size_pct,
            )
            if stake <= 0:
                copied = False
                action = "pomijam - stawka 0"
            else:
                slippage_points = signal.get("slippage_pct_points", 0)
                slippage_points = 0 if pd.isna(slippage_points) else max(float(slippage_points), 0)
                slippage_cost = stake * slippage_points / 100
                bankroll = max(0.0, bankroll - slippage_cost)
                action = "kopiuje"
        elif result:
            action = f"pomijam - {result}"

        ledger.append({
            "leader_time": signal.get("leader_time"),
            "delay_s": selected_delay_s,
            "traderName": signal.get("traderName", ""),
            "profileUrl": signal.get("profileUrl", ""),
            "wallet": signal.get("wallet", ""),
            "akcja": action,
            "stawka_usdc": round(stake, 2),
            "koszt_poslizgu_usdc": round(slippage_cost, 4),
            "bankroll_po": round(bankroll, 2),
            "leader_notional_est": signal.get("leader_notional_est"),
            "leader_price": signal.get("leader_price"),
            "copy_price": signal.get("copy_price"),
            "slippage_pct_points": signal.get("slippage_pct_points"),
            "result": result,
            "title": signal.get("title", ""),
        })

    ledger_frame = pd.DataFrame(ledger)
    copied_mask = ledger_frame["akcja"].eq("kopiuje") if not ledger_frame.empty else pd.Series(dtype=bool)
    copied_rows = ledger_frame[copied_mask]
    summary = {
        "sygnaly": len(ledger_frame),
        "skopiowane": int(copied_mask.sum()),
        "pominiete": int((~copied_mask).sum()) if not ledger_frame.empty else 0,
        "kapital_startowy": round(float(starting_bankroll), 2),
        "bankroll_po_kosztach": round(float(ledger_frame["bankroll_po"].iloc[-1]), 2) if not ledger_frame.empty else round(float(starting_bankroll), 2),
        "koszt_poslizgu": round(float(ledger_frame["koszt_poslizgu_usdc"].sum()), 4) if not ledger_frame.empty else 0.0,
        "srednia_stawka": round(float(copied_rows["stawka_usdc"].mean()), 2) if not copied_rows.empty else 0.0,
        "max_stawka": round(float(copied_rows["stawka_usdc"].max()), 2) if not copied_rows.empty else 0.0,
    }
    return ledger_frame, summary

with st.sidebar:
    st.header("Portfele")
    default_wallets = "\n".join(parse_wallets(wallets_file.read_text(encoding="utf-8-sig")))
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

    st.header("Money management")
    money_delay_s = st.selectbox(
        "Opoznienie dla symulacji kapitalu",
        COPY_DELAYS_SECONDS,
        index=0,
        format_func=lambda value: f"{value}s",
    )
    starting_bankroll = st.number_input(
        "Kapital startowy (USDC)",
        min_value=10.0,
        value=1000.0,
        step=100.0,
    )
    risk_per_trade_pct = st.number_input(
        "Ryzyko na jeden ruch (%)",
        min_value=0.1,
        max_value=25.0,
        value=1.0,
        step=0.1,
    )
    max_stake_usdc = st.number_input(
        "Maksymalna stawka na ruch (USDC)",
        min_value=1.0,
        value=25.0,
        step=5.0,
    )
    leader_size_pct = st.number_input(
        "Maksymalnie % pozycji lidera",
        min_value=0.1,
        max_value=100.0,
        value=10.0,
        step=1.0,
    )

    st.header("Szukacz walletow")
    discovery_total_trades = st.slider("Ile publicznych tradeow skanowac", 500, 3500, 2500, step=500)
    discovery_min_attempts = st.slider("Minimum prob dla kandydata", 3, 30, 10, step=1)
    discovery_min_profit = st.number_input(
        "Minimalny profit tradera ($)",
        min_value=0.0,
        value=1000.0,
        step=500.0,
    )
    discovery_min_ok_1s = st.slider("Minimum OK po 1s (%)", 0, 100, 40, step=5)
    discovery_top_to_add = st.slider("Ile top walletow dopisac", 3, 25, 8, step=1)
    if st.button("Szukaj nowych walletow"):
        with st.spinner("Skanuje publiczne transakcje i licze ranking..."):
            try:
                discovery = discover_copy_wallets(
                    total_trades=discovery_total_trades,
                    max_slippage_pct=max_slippage_pct,
                    max_match_seconds=max_match_seconds,
                    min_attempts=discovery_min_attempts,
                    min_profit=discovery_min_profit,
                    min_ok_1s_pct=discovery_min_ok_1s,
                )
                ranking = discovery["ranking"]
                st.session_state["discovery_ranking"] = ranking
                st.session_state["discovery_counts"] = {
                    "raw": len(discovery["raw"]),
                    "sports": len(discovery["sports"]),
                    "wallets": int(discovery["sports"]["proxyWallet"].nunique()) if not discovery["sports"].empty else 0,
                }
                Path("data").mkdir(exist_ok=True)
                ranking.to_csv(Path("data") / "wallet_discovery_ranking.csv", index=False)
                st.success(f"Znaleziono {len(ranking)} profitowych kandydatow. Ranking zapisany do data/wallet_discovery_ranking.csv")
            except Exception as exc:
                st.error(f"Nie udalo sie wykonac skanu: {exc}")

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
discovery_ranking = st.session_state.get("discovery_ranking")
if discovery_ranking is not None and not discovery_ranking.empty:
    st.subheader("Nowi kandydaci z publicznych transakcji")
    counts = st.session_state.get("discovery_counts", {})
    d1, d2, d3 = st.columns(3)
    d1.metric("Przeskanowane trade'y", counts.get("raw", 0))
    d2.metric("Sport/e-sport", counts.get("sports", 0))
    d3.metric("Unikalne wallety", counts.get("wallets", 0))

    existing_wallets = set(parse_wallets(wallets_text))
    new_candidates = discovery_ranking[~discovery_ranking["wallet"].isin(existing_wallets)].copy()
    if new_candidates.empty:
        st.info("Wszystkie znalezione top wallety sa juz na Twojej liscie obserwowanych.")
    else:
        display_cols = [
            "traderName", "profileUrl", "wallet", "profitAmount", "qualityScore", "score",
            "ok_pct_1s", "ok_1s", "w_oknie_1s", "proby_1s",
            "ok_pct_2s", "ok_2s", "w_oknie_2s", "proby_2s",
            "ok_pct_5s", "ok_5s", "w_oknie_5s", "proby_5s",
        ]
        display_cols = [col for col in display_cols if col in new_candidates.columns]
        st.dataframe(
            new_candidates[display_cols].head(30),
            use_container_width=True,
            hide_index=True,
            height=280,
            column_config={"profileUrl": st.column_config.LinkColumn("Profil", display_text="Otworz")},
        )

        top_wallets = new_candidates["wallet"].head(discovery_top_to_add).tolist()
        st.text_area(
            "Top adresy do sprawdzenia",
            value="\n".join(top_wallets),
            height=160,
        )
        if st.button(f"Dopisz top {len(top_wallets)} do obserwowanych"):
            combined = parse_wallets(wallets_file.read_text(encoding="utf-8-sig")) + top_wallets
            unique_wallets = list(dict.fromkeys(combined))
            wallets_file.write_text("\n".join(unique_wallets) + "\n", encoding="utf-8")
            st.success("Dopisano kandydatow do wallets.txt. Odswiezam aplikacje...")
            st.rerun()

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

    st.subheader("Money management")
    st.caption(
        "To nie liczy koncowego zysku z rynku, bo tu nie mamy jeszcze pelnego zamkniecia pozycji. "
        "Ta sekcja pokazuje, ile kapitalu byloby angazowane i ile kosztowalby sam poslizg wejscia."
    )
    money_ledger, money_summary = simulate_money_management(
        simulated=simulated,
        selected_delay_s=money_delay_s,
        starting_bankroll=starting_bankroll,
        risk_per_trade_pct=risk_per_trade_pct,
        max_stake_usdc=max_stake_usdc,
        leader_size_pct=leader_size_pct,
    )

    if money_ledger.empty:
        st.warning("Brak danych do symulacji money management dla wybranego opoznienia.")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Kapital startowy", f"{money_summary['kapital_startowy']:.2f} USDC")
        m2.metric("Po kosztach wejsc", f"{money_summary['bankroll_po_kosztach']:.2f} USDC")
        m3.metric("Skopiowane", f"{money_summary['skopiowane']}/{money_summary['sygnaly']}")
        m4.metric("Koszt poslizgu", f"{money_summary['koszt_poslizgu']:.4f} USDC")

        m5, m6, m7 = st.columns(3)
        m5.metric("Srednia stawka", f"{money_summary['srednia_stawka']:.2f} USDC")
        m6.metric("Najwieksza stawka", f"{money_summary['max_stawka']:.2f} USDC")
        m7.metric("Pominiete", money_summary["pominiete"])

        money_cols = [
            "leader_time", "delay_s", "traderName", "profileUrl", "wallet", "akcja",
            "stawka_usdc", "koszt_poslizgu_usdc", "bankroll_po", "leader_notional_est",
            "leader_price", "copy_price", "slippage_pct_points", "result", "title"
        ]
        st.dataframe(
            money_ledger[[col for col in money_cols if col in money_ledger.columns]],
            use_container_width=True,
            hide_index=True,
            height=300,
            column_config={"profileUrl": st.column_config.LinkColumn("Profil", display_text="Otworz")},
        )
        money_csv = money_ledger.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Pobierz CSV money management",
            data=money_csv,
            file_name="polymarket_money_management.csv",
            mime="text/csv",
        )
    wallet_rank_source = simulated.copy()
    if "seconds_after_for_stats" not in wallet_rank_source.columns:
        wallet_rank_source["seconds_after_for_stats"] = None
    wallet_rank_source["ok"] = wallet_rank_source["result"].eq("OK")

    wallet_delay = (
        wallet_rank_source.groupby(["wallet", "traderName", "profileUrl", "watchedWallet", "delay_s"], dropna=False)
        .agg(
            proby=("result", "count"),
            ok=("ok", "sum"),
            w_oknie=("seconds_after_for_stats", "count"),
            mediana_poslizgu=("slippage_pct_points", "median"),
            mediana_czasu=("seconds_after_for_stats", "median"),
        )
        .reset_index()
    )
    wallet_delay["ok_pct"] = (wallet_delay["ok"] / wallet_delay["proby"] * 100).round(1)

    wallet_ranking = None
    for delay in COPY_DELAYS_SECONDS:
        part = wallet_delay[wallet_delay["delay_s"] == delay][[
            "wallet", "traderName", "profileUrl", "watchedWallet", "proby", "ok", "w_oknie", "ok_pct",
            "mediana_poslizgu", "mediana_czasu"
        ]].rename(columns={
            "proby": f"proby_{delay}s",
            "ok": f"ok_{delay}s",
            "w_oknie": f"w_oknie_{delay}s",
            "ok_pct": f"ok_pct_{delay}s",
            "mediana_poslizgu": f"mediana_poslizgu_{delay}s",
            "mediana_czasu": f"mediana_czasu_{delay}s",
        })
        wallet_ranking = part if wallet_ranking is None else wallet_ranking.merge(
            part,
            on=["wallet", "traderName", "profileUrl", "watchedWallet"],
            how="outer",
        )

    if wallet_ranking is not None and not wallet_ranking.empty:
        for col in ["ok_pct_1s", "ok_pct_2s", "ok_pct_5s"]:
            if col not in wallet_ranking.columns:
                wallet_ranking[col] = 0
        wallet_ranking["score"] = (
            wallet_ranking["ok_pct_1s"].fillna(0) * 0.5
            + wallet_ranking["ok_pct_2s"].fillna(0) * 0.3
            + wallet_ranking["ok_pct_5s"].fillna(0) * 0.2
        ).round(1)
        wallet_ranking = wallet_ranking.sort_values(
            ["score", "ok_pct_1s", "ok_1s"],
            ascending=False,
            na_position="last",
        )

        profit_rows = []
        for wallet in wallet_ranking["watchedWallet"].dropna().unique():
            profit = fetch_wallet_profit(str(wallet))
            profit_rows.append({"watchedWallet": wallet, **profit})
        if profit_rows:
            profit_frame = pd.DataFrame(profit_rows)
            profit_frame["profitAmount"] = pd.to_numeric(profit_frame["profitAmount"], errors="coerce")
            wallet_ranking = wallet_ranking.merge(profit_frame, on="watchedWallet", how="left")
            wallet_ranking["traderName"] = wallet_ranking.apply(
                lambda row: row.get("profitName") or row.get("profitPseudonym") or row.get("traderName"),
                axis=1,
            )

        st.subheader("Ranking walletow pod kopiowanie")
        st.caption(
            "Score mocniej premiuje szybkie kopiowanie: 1s ma wage 50%, 2s ma 30%, 5s ma 20%. "
            "Przy malej liczbie prob patrz tez na kolumny proby i w_oknie."
        )
        ranking_cols = [
            "traderName", "profileUrl", "wallet", "profitAmount", "qualityScore", "score",
            "ok_pct_1s", "ok_1s", "w_oknie_1s", "proby_1s",
            "ok_pct_2s", "ok_2s", "w_oknie_2s", "proby_2s",
            "ok_pct_5s", "ok_5s", "w_oknie_5s", "proby_5s",
            "watchedWallet",
        ]
        ranking_cols = [col for col in ranking_cols if col in wallet_ranking.columns]
        st.dataframe(
            wallet_ranking[ranking_cols],
            use_container_width=True,
            hide_index=True,
            height=260,
            column_config={"profileUrl": st.column_config.LinkColumn("Profil", display_text="Otworz")},
        )

        rank_csv = wallet_ranking.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Pobierz CSV rankingu walletow",
            data=rank_csv,
            file_name="polymarket_wallet_copy_ranking.csv",
            mime="text/csv",
        )

    sim_cols = [
        "leader_time", "delay_s", "traderName", "profileUrl", "wallet", "side", "outcome", "leader_price",
        "leader_size", "leader_notional_est", "copy_price", "slippage_pct_points", "seconds_after_move", "result", "title"
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
    filtered.groupby(["watchedWallet", "walletShort", "traderName", "profileUrl"], dropna=False)
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
    summary[["traderName", "profileUrl", "walletShort", "trades", "unique_markets", "total_size", "avg_size", "avg_price", "est_notional", "activity_window", "watchedWallet"]],
    use_container_width=True,
    height=220,
    column_config={"profileUrl": st.column_config.LinkColumn("Profil", display_text="Otworz")},
)

st.subheader("Najwieksze ruchy")
biggest = filtered.sort_values("size_num", ascending=False).head(30)
preferred_cols_big = ["datetime_utc", "traderName", "profileUrl", "walletShort", "side", "outcome", "price", "size", "notional_est", "title"]
st.dataframe(
    biggest[[c for c in preferred_cols_big if c in biggest.columns]],
    use_container_width=True,
    height=320,
    column_config={"profileUrl": st.column_config.LinkColumn("Profil", display_text="Otworz")},
)

st.subheader("Ostatnie ruchy po filtrach")
preferred_cols = [
    "datetime_utc", "traderName", "profileUrl", "walletShort", "side", "outcome", "price", "size", "notional_est", "title", "eventSlug",
    "conditionId", "asset", "transactionHash", "watchedWallet", "source"
]
cols = [c for c in preferred_cols if c in filtered.columns] + [c for c in filtered.columns if c not in preferred_cols]
st.dataframe(
    filtered[cols],
    use_container_width=True,
    height=520,
    column_config={"profileUrl": st.column_config.LinkColumn("Profil", display_text="Otworz")},
)

csv = filtered[cols].to_csv(index=False).encode("utf-8")
st.download_button("Pobierz filtrowany CSV", data=csv, file_name="polymarket_wallet_filtered.csv", mime="text/csv")

raw_csv = df.to_csv(index=False).encode("utf-8")
st.download_button("Pobierz pelny RAW CSV", data=raw_csv, file_name="polymarket_wallet_raw.csv", mime="text/csv")

st.info("Etap 3: symulator pokazuje, czy historycznie po 1s/2s/5s nadal pojawiala sie podobna cena. To nadal tylko research, bez handlu.")
st.caption(f"Ostatnie odswiezenie: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

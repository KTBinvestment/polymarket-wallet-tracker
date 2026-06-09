from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from polymarket_api import get_user_activity, get_user_trades

SPORT_KEYWORDS = [
    " vs ", " v ", "spread:", "o/u", "over", "under", "will ",
    "nba", "wnba", "nfl", "nhl", "mlb", "atp", "wta", "ufc", "fifa",
    "premier league", "champions league", "roland garros", "tennis", "baseball",
    "soccer", "football", "basketball", "hockey", "marlins", "red sox",
]

st.set_page_config(page_title="Polymarket Copy Research", layout="wide")
st.title("Polymarket Wallet Tracker — etap 2")
st.caption("Obserwacja portfeli + pierwsza selekcja pod copy-trading. Nadal zero handlu i zero kluczy prywatnych.")

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
    limit = st.slider("Ile rekordów pobierać na portfel", 10, 500, 500, step=10)
    only_sports = st.checkbox("Pokaż tylko prawdopodobne rynki sportowe", value=True)
    min_size = st.number_input("Minimalny size", min_value=0.0, value=0.0, step=10.0)
    save = st.button("Zapisz portfele")
    if save:
        wallets_file.write_text(wallets_text.strip() + "\n", encoding="utf-8")
        st.success("Zapisano wallets.txt")

wallets = [w.strip() for w in wallets_text.splitlines() if w.strip() and not w.strip().startswith("#")]

if not wallets:
    st.info("Wklej pierwszy adres portfela Polymarket po lewej stronie i kliknij 'Zapisz portfele'.")
    st.stop()

all_rows = []
errors = []

for wallet in wallets:
    try:
        trades = get_user_trades(wallet, limit=limit)
        for item in trades:
            row = dict(item)
            row["watchedWallet"] = wallet
            row["walletShort"] = short_wallet(wallet)
            row["source"] = "trades"
            all_rows.append(row)
    except Exception as e:
        errors.append(f"{wallet}: trades error: {e}")
        try:
            activity = get_user_activity(wallet, limit=limit)
            for item in activity:
                row = dict(item)
                row["watchedWallet"] = wallet
                row["walletShort"] = short_wallet(wallet)
                row["source"] = "activity"
                all_rows.append(row)
        except Exception as e2:
            errors.append(f"{wallet}: activity error: {e2}")

if errors:
    with st.expander("Błędy / ostrzeżenia"):
        for err in errors:
            st.write(err)

if not all_rows:
    st.warning("Nie pobrano żadnych transakcji. Sprawdź, czy adres portfela jest poprawny.")
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
ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
df.to_csv(Path("data") / "latest_raw.csv", index=False)
filtered.to_csv(Path("data") / "latest_filtered.csv", index=False)

st.subheader("Szybki podgląd")
col1, col2, col3, col4 = st.columns(4)
col1.metric("Portfele", len(wallets))
col2.metric("Pobrane transakcje", len(df))
col3.metric("Po filtrach", len(filtered))
col4.metric("Sport guess", int(df["is_sport_guess"].sum()))

if len(filtered) == 0:
    st.warning("Po filtrach nie zostały żadne rekordy. Odznacz filtr sportowy albo zmniejsz minimalny size.")
    st.stop()

# Wallet ranking / research summary
st.subheader("Ranking obserwowanych portfeli — wersja research")
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
summary["activity_window"] = summary["first_seen"].astype(str) + " → " + summary["last_seen"].astype(str)
summary = summary.sort_values(["trades", "est_notional"], ascending=False)
st.dataframe(
    summary[["walletShort", "trades", "unique_markets", "total_size", "avg_size", "avg_price", "est_notional", "activity_window", "watchedWallet"]],
    use_container_width=True,
    height=220,
)

st.subheader("Największe ruchy")
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
st.download_button("Pobierz pełny RAW CSV", data=raw_csv, file_name="polymarket_wallet_raw.csv", mime="text/csv")

st.info("Następny etap: symulator kopiowania. Będziemy liczyć, czy po 1s/2s/5s od ruchu lidera dalej dałoby się wejść z sensownym poślizgiem.")
st.caption(f"Ostatnie odświeżenie: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

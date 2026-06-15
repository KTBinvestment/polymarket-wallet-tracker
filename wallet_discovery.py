import time
from functools import lru_cache
from typing import Dict, List

import pandas as pd
import requests

from polymarket_api import PolymarketAPIError, _request_json

SPORT_KEYWORDS = [
    " vs ", " v ", "spread:", "o/u", "over", "under",
    "nba", "wnba", "nfl", "nhl", "mlb", "atp", "wta", "ufc", "fifa",
    "premier league", "champions league", "tennis", "baseball", "soccer",
    "football", "basketball", "hockey", "counter-strike", "cs2", "dota",
    "league of legends", "valorant", "map 1", "map 2", "winner",
]

DELAYS = [1, 2, 5]
LB_API = "https://lb-api.polymarket.com"


def polymarket_profile_url(wallet: str) -> str:
    return f"https://polymarket.com/profile/{wallet}"


def short_wallet(wallet: str) -> str:
    return f"{wallet[:6]}...{wallet[-4:]}" if isinstance(wallet, str) and len(wallet) > 12 else str(wallet)


def trader_name(row, fallback_wallet: str) -> str:
    for key in ("name", "pseudonym"):
        value = row.get(key, "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return short_wallet(fallback_wallet)


def is_sport_title(title: str) -> bool:
    title = str(title or "").lower()
    return any(keyword in title for keyword in SPORT_KEYWORDS)



@lru_cache(maxsize=512)
def fetch_wallet_profit(wallet: str) -> Dict[str, object]:
    try:
        response = requests.get(
            f"{LB_API}/profit",
            params={"address": wallet},
            timeout=(5, 15),
        )
        response.raise_for_status()
        data = response.json()
    except (requests.exceptions.RequestException, ValueError):
        return {"profitAmount": None, "profitName": "", "profitPseudonym": ""}

    if not isinstance(data, list) or not data:
        return {"profitAmount": None, "profitName": "", "profitPseudonym": ""}

    first = data[0]
    return {
        "profitAmount": first.get("amount"),
        "profitName": first.get("name") or "",
        "profitPseudonym": first.get("pseudonym") or "",
    }


def enrich_with_profit(ranking: pd.DataFrame) -> pd.DataFrame:
    if ranking.empty:
        return ranking

    rows = []
    for wallet in ranking["wallet"].dropna().unique():
        profit = fetch_wallet_profit(str(wallet))
        rows.append({"wallet": wallet, **profit})
        time.sleep(0.05)

    profits = pd.DataFrame(rows)
    enriched = ranking.merge(profits, on="wallet", how="left")
    enriched["profitAmount"] = pd.to_numeric(enriched["profitAmount"], errors="coerce")
    enriched["traderName"] = enriched.apply(
        lambda row: row["profitName"] or row["profitPseudonym"] or row["traderName"],
        axis=1,
    )
    return enriched

def fetch_public_trades(total: int = 3000, page_size: int = 500) -> List[Dict]:
    rows = []
    total = min(max(int(total), 500), 3500)
    page_size = min(max(int(page_size), 100), 500)

    for offset in range(0, total, page_size):
        try:
            batch = _request_json("/trades", params={"limit": page_size, "offset": offset})
        except PolymarketAPIError:
            break
        if not batch:
            break
        rows.extend(batch)
        time.sleep(0.2)

    return rows


def slippage_for_side(side: str, leader_price: float, copy_price: float) -> float:
    side = str(side or "").upper()
    if side == "BUY":
        return copy_price - leader_price
    if side == "SELL":
        return leader_price - copy_price
    return abs(copy_price - leader_price)


def simulate_public_copyability(
    trades: pd.DataFrame,
    max_slippage_pct: float = 2.0,
    max_match_seconds: int = 30,
) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()

    frame = trades.copy()
    frame["price_num"] = pd.to_numeric(frame.get("price"), errors="coerce")
    frame["size_num"] = pd.to_numeric(frame.get("size"), errors="coerce")
    frame["datetime_utc"] = pd.to_datetime(frame.get("timestamp"), unit="s", errors="coerce", utc=True)
    frame = frame.dropna(subset=["proxyWallet", "conditionId", "asset", "price_num", "datetime_utc"])
    frame = frame.sort_values("datetime_utc").reset_index(drop=True)
    frame["_row_id"] = frame.index

    rows = []
    for _, leader in frame.iterrows():
        for delay in DELAYS:
            target_time = leader["datetime_utc"] + pd.Timedelta(seconds=delay)
            candidates = frame[
                (frame["datetime_utc"] >= target_time)
                & (frame["conditionId"] == leader["conditionId"])
                & (frame["asset"] == leader["asset"])
                & (frame["_row_id"] != leader["_row_id"])
            ].sort_values("datetime_utc")

            result = "brak ceny"
            seconds_after = None
            slippage = None
            if not candidates.empty:
                copied = candidates.iloc[0]
                seconds_after = (copied["datetime_utc"] - leader["datetime_utc"]).total_seconds()
                if seconds_after <= max_match_seconds:
                    slippage = slippage_for_side(
                        leader.get("side"),
                        float(leader["price_num"]),
                        float(copied["price_num"]),
                    ) * 100
                    result = "OK" if slippage <= max_slippage_pct else "za duzy poslizg"
                else:
                    result = "poza oknem"

            rows.append({
                "wallet": leader["proxyWallet"],
                "traderName": trader_name(leader, leader["proxyWallet"]),
                "profileUrl": polymarket_profile_url(leader["proxyWallet"]),
                "delay_s": delay,
                "result": result,
                "seconds_after": seconds_after,
                "slippage_pct_points": slippage,
                "notional": float(leader.get("size_num", 0) or 0) * float(leader.get("price_num", 0) or 0),
                "title": leader.get("title", ""),
            })

    return pd.DataFrame(rows)


def rank_wallets(simulated: pd.DataFrame, min_attempts: int = 5) -> pd.DataFrame:
    if simulated.empty:
        return pd.DataFrame()

    source = simulated.copy()
    source["ok"] = source["result"].eq("OK")
    source["in_window"] = source["result"].isin(["OK", "za duzy poslizg"])

    grouped = (
        source.groupby(["wallet", "traderName", "profileUrl", "delay_s"], dropna=False)
        .agg(
            proby=("result", "count"),
            ok=("ok", "sum"),
            w_oknie=("in_window", "sum"),
            mediana_poslizgu=("slippage_pct_points", "median"),
            mediana_czasu=("seconds_after", "median"),
            notional=("notional", "sum"),
        )
        .reset_index()
    )
    grouped["ok_pct"] = (grouped["ok"] / grouped["proby"] * 100).round(1)

    ranking = None
    for delay in DELAYS:
        part = grouped[grouped["delay_s"] == delay][[
            "wallet", "traderName", "profileUrl", "proby", "ok", "w_oknie", "ok_pct", "mediana_poslizgu", "mediana_czasu", "notional"
        ]].rename(columns={
            "proby": f"proby_{delay}s",
            "ok": f"ok_{delay}s",
            "w_oknie": f"w_oknie_{delay}s",
            "ok_pct": f"ok_pct_{delay}s",
            "mediana_poslizgu": f"mediana_poslizgu_{delay}s",
            "mediana_czasu": f"mediana_czasu_{delay}s",
            "notional": f"notional_{delay}s",
        })
        ranking = part if ranking is None else ranking.merge(part, on=["wallet", "traderName", "profileUrl"], how="outer")

    for col in ["ok_pct_1s", "ok_pct_2s", "ok_pct_5s", "proby_1s", "ok_1s", "w_oknie_1s"]:
        if col not in ranking.columns:
            ranking[col] = 0

    ranking = ranking[ranking["proby_1s"].fillna(0) >= min_attempts].copy()
    ranking["score"] = (
        ranking["ok_pct_1s"].fillna(0) * 0.5
        + ranking["ok_pct_2s"].fillna(0) * 0.3
        + ranking["ok_pct_5s"].fillna(0) * 0.2
    ).round(1)
    return ranking.sort_values(["score", "ok_1s", "w_oknie_1s"], ascending=False)


def discover_copy_wallets(
    total_trades: int,
    max_slippage_pct: float,
    max_match_seconds: int,
    min_attempts: int,
    min_profit: float = 1000,
    min_ok_1s_pct: float = 40,
) -> Dict[str, object]:
    rows = fetch_public_trades(total=total_trades)
    raw = pd.DataFrame(rows)
    if raw.empty:
        return {"raw": raw, "sports": raw, "simulated": raw, "ranking": raw}

    raw["is_sport_guess"] = raw.get("title", "").apply(is_sport_title)
    sports = raw[raw["is_sport_guess"]].copy()
    simulated = simulate_public_copyability(
        sports,
        max_slippage_pct=max_slippage_pct,
        max_match_seconds=max_match_seconds,
    )
    ranking = rank_wallets(simulated, min_attempts=min_attempts)
    ranking = enrich_with_profit(ranking)
    if not ranking.empty:
        ranking = ranking[
            (ranking["profitAmount"].fillna(-10**18) >= float(min_profit))
            & (ranking["ok_pct_1s"].fillna(0) >= float(min_ok_1s_pct))
            & (ranking["ok_1s"].fillna(0) >= 3)
        ].copy()
        ranking["qualityScore"] = (
            ranking["score"].fillna(0) * 0.65
            + ranking["profitAmount"].clip(lower=0).fillna(0).pow(0.25) * 3
            + ranking["proby_1s"].fillna(0).clip(upper=50) * 0.2
        ).round(1)
        ranking = ranking.sort_values(["qualityScore", "score", "ok_pct_1s", "profitAmount"], ascending=False)
    return {"raw": raw, "sports": sports, "simulated": simulated, "ranking": ranking}

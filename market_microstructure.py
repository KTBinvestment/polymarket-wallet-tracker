from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional


@dataclass
class FillEstimate:
    side: str
    requested_usdc: float
    requested_shares: float
    filled_usdc: float
    filled_shares: float
    average_price: Optional[float]
    best_price: Optional[float]
    worst_price: Optional[float]
    spread: Optional[float]
    slippage_pct_points: Optional[float]
    fee_usdc: float
    fill_ratio: float
    fully_filled: bool
    levels_used: int
    reason: str

    def to_dict(self) -> Dict:
        return asdict(self)


def taker_fee_usdc(shares: float, price: float, fee_rate: float = 0.03) -> float:
    """Polymarket fee curve: shares * rate * price * (1-price)."""
    if shares <= 0 or price <= 0 or price >= 1 or fee_rate <= 0:
        return 0.0
    return shares * fee_rate * price * (1 - price)


def _levels(book: Dict, key: str, reverse: bool) -> List[Dict[str, float]]:
    result = []
    for level in book.get(key, []) or []:
        try:
            price = float(level["price"])
            size = float(level["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if price > 0 and size > 0:
            result.append({"price": price, "size": size})
    return sorted(result, key=lambda row: row["price"], reverse=reverse)


def estimate_market_fill(
    order_book: Dict,
    side: str,
    amount: float,
    amount_type: str = "usdc",
    max_slippage_pct_points: float = 2.0,
    fee_rate: float = 0.03,
) -> FillEstimate:
    side = str(side).upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("side musi byc BUY albo SELL")
    if amount <= 0:
        raise ValueError("amount musi byc dodatni")
    if amount_type not in {"usdc", "shares"}:
        raise ValueError("amount_type musi byc usdc albo shares")

    asks = _levels(order_book, "asks", reverse=False)
    bids = _levels(order_book, "bids", reverse=True)
    levels = asks if side == "BUY" else bids
    best_bid = bids[0]["price"] if bids else None
    best_ask = asks[0]["price"] if asks else None
    spread = (
        best_ask - best_bid
        if best_ask is not None and best_bid is not None
        else None
    )
    best_price = best_ask if side == "BUY" else best_bid

    requested_usdc = amount if amount_type == "usdc" else 0.0
    requested_shares = amount if amount_type == "shares" else 0.0
    if not levels or best_price is None:
        return FillEstimate(
            side, requested_usdc, requested_shares, 0, 0, None, None, None,
            spread, None, 0, 0, False, 0, "pusty orderbook",
        )

    remaining = amount
    filled_usdc = 0.0
    filled_shares = 0.0
    levels_used = 0
    worst_price = None

    for level in levels:
        price = level["price"]
        slippage = (
            price - best_price if side == "BUY" else best_price - price
        ) * 100
        if slippage > max_slippage_pct_points:
            break

        if amount_type == "usdc":
            available_usdc = level["size"] * price
            take_usdc = min(remaining, available_usdc)
            take_shares = take_usdc / price
            remaining -= take_usdc
        else:
            take_shares = min(remaining, level["size"])
            take_usdc = take_shares * price
            remaining -= take_shares

        if take_shares <= 0:
            continue
        filled_usdc += take_usdc
        filled_shares += take_shares
        worst_price = price
        levels_used += 1
        if remaining <= 1e-9:
            break

    average_price = (
        filled_usdc / filled_shares if filled_shares > 0 else None
    )
    if amount_type == "usdc":
        requested_shares = amount / best_price
        fill_ratio = filled_usdc / amount
    else:
        requested_usdc = amount * best_price
        fill_ratio = filled_shares / amount

    slippage_pct_points = None
    if average_price is not None:
        slippage_pct_points = (
            average_price - best_price
            if side == "BUY"
            else best_price - average_price
        ) * 100
    fee = taker_fee_usdc(filled_shares, average_price or 0, fee_rate)
    fully_filled = fill_ratio >= 0.999999
    reason = "pelne wykonanie" if fully_filled else "czesciowe wykonanie"

    return FillEstimate(
        side=side,
        requested_usdc=requested_usdc,
        requested_shares=requested_shares,
        filled_usdc=filled_usdc,
        filled_shares=filled_shares,
        average_price=average_price,
        best_price=best_price,
        worst_price=worst_price,
        spread=spread,
        slippage_pct_points=slippage_pct_points,
        fee_usdc=fee,
        fill_ratio=min(max(fill_ratio, 0), 1),
        fully_filled=fully_filled,
        levels_used=levels_used,
        reason=reason,
    )

import re
from typing import Any, Dict, List

import requests

DATA_API = "https://data-api.polymarket.com"

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def validate_wallet(address: str) -> str:
    address = address.strip()
    if not ADDRESS_RE.match(address):
        raise ValueError(f"Nieprawidłowy adres portfela: {address}")
    return address


def get_user_activity(address: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Pobiera ostatnią aktywność użytkownika z publicznego Data API Polymarket."""
    address = validate_wallet(address)
    params = {"user": address, "limit": min(max(limit, 1), 500), "offset": 0}
    r = requests.get(f"{DATA_API}/activity", params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    if isinstance(data, list):
        return data
    return []


def get_user_trades(address: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Pobiera trade'y użytkownika z publicznego Data API Polymarket."""
    address = validate_wallet(address)
    params = {"user": address, "limit": min(max(limit, 1), 500), "offset": 0}
    r = requests.get(f"{DATA_API}/trades", params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    if isinstance(data, list):
        return data
    return []

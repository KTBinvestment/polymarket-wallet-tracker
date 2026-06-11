import contextlib
import re
import socket
import time
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DATA_API_HOST = "data-api.polymarket.com"
DATA_API = f"https://{DATA_API_HOST}"
PUBLIC_DNS_URL = "https://dns.google/resolve"
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
REQUEST_TIMEOUT: Tuple[int, int] = (4, 10)
TEST_TIMEOUT: Tuple[int, int] = (4, 15)
DNS_TIMEOUT: Tuple[int, int] = (3, 5)
RETRY_COUNT = 2
RETRY_BACKOFF = 0.75

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


class PolymarketAPIError(Exception):
    """Raised when the public Polymarket Data API cannot return usable data."""


def validate_wallet(address: str) -> str:
    address = address.strip()
    if not ADDRESS_RE.match(address):
        raise ValueError(f"Nieprawidlowy adres portfela: {address}")
    return address


def normalize_limit(limit: int) -> int:
    return min(max(int(limit), 1), MAX_LIMIT)


def _build_session() -> requests.Session:
    retry = Retry(
        total=RETRY_COUNT,
        connect=RETRY_COUNT,
        read=RETRY_COUNT,
        status=RETRY_COUNT,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "polymarket-wallet-tracker/0.2"})
    return session


_SESSION = _build_session()


@lru_cache(maxsize=8)
def _public_dns_ips(host: str) -> Tuple[str, ...]:
    try:
        response = requests.get(
            PUBLIC_DNS_URL,
            params={"name": host, "type": "A"},
            timeout=DNS_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.exceptions.RequestException, ValueError):
        return tuple()

    ips = []
    for answer in payload.get("Answer", []):
        ip = str(answer.get("data", ""))
        if answer.get("type") == 1 and IPV4_RE.match(ip):
            ips.append(ip)
    return tuple(dict.fromkeys(ips))


@contextlib.contextmanager
def _dns_override_for_data_api():
    ips = _public_dns_ips(DATA_API_HOST)
    if not ips:
        yield
        return

    original_getaddrinfo = socket.getaddrinfo

    def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if host != DATA_API_HOST:
            return original_getaddrinfo(host, port, family, type, proto, flags)

        results = []
        last_error = None
        for ip in ips:
            try:
                results.extend(original_getaddrinfo(ip, port, family, type, proto, flags))
            except OSError as exc:
                last_error = exc
        if results:
            return results
        if last_error:
            raise last_error
        return original_getaddrinfo(host, port, family, type, proto, flags)

    socket.getaddrinfo = patched_getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = original_getaddrinfo


def _extract_records(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("data", "activity", "trades", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _request_json(
    endpoint: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: Tuple[int, int] = REQUEST_TIMEOUT,
) -> Any:
    url = f"{DATA_API}{endpoint}"
    try:
        with _dns_override_for_data_api():
            response = _SESSION.get(url, params=params, timeout=timeout)
        response.raise_for_status()
    except requests.exceptions.Timeout as exc:
        raise PolymarketAPIError(
            f"Timeout przy {endpoint}. Data API nie odpowiedzialo w limicie {timeout[1]}s."
        ) from exc
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise PolymarketAPIError(f"Data API zwrocilo HTTP {status} dla {endpoint}.") from exc
    except requests.exceptions.RequestException as exc:
        raise PolymarketAPIError(f"Blad polaczenia z Data API dla {endpoint}: {exc}") from exc

    try:
        return response.json()
    except ValueError as exc:
        raise PolymarketAPIError(f"Data API zwrocilo niepoprawny JSON dla {endpoint}.") from exc


def get_user_activity(address: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
    """Fetch recent public activity for a wallet from Polymarket Data API."""
    address = validate_wallet(address)
    params = {"user": address, "limit": normalize_limit(limit), "offset": 0}
    return _extract_records(_request_json("/activity", params=params))


def get_user_trades(address: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
    """Fetch recent public trades for a wallet from Polymarket Data API."""
    address = validate_wallet(address)
    params = {"user": address, "limit": normalize_limit(limit), "offset": 0}
    return _extract_records(_request_json("/trades", params=params))


def test_data_api(address: Optional[str] = None) -> Dict[str, Any]:
    """Check whether data-api.polymarket.com responds with a small activity request."""
    params: Dict[str, Any] = {"limit": 1, "offset": 0}
    if address:
        params["user"] = validate_wallet(address)

    started = time.monotonic()
    records = _extract_records(_request_json("/activity", params=params, timeout=TEST_TIMEOUT))
    elapsed = time.monotonic() - started
    return {
        "ok": True,
        "endpoint": "/activity",
        "elapsed": elapsed,
        "records": len(records),
        "message": f"Polaczenie OK ({elapsed:.2f}s). /activity zwrocil {len(records)} rekordow testowych.",
    }

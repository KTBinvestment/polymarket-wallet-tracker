import contextlib
import re
import socket
import time
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DATA_API_HOST = "data-api.polymarket.com"
DATA_API = f"https://{DATA_API_HOST}"
CLOB_API = "https://clob.polymarket.com"
GEOBLOCK_URL = "https://polymarket.com/api/geoblock"
PUBLIC_DNS_URL = "https://dns.google/resolve"
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
MAX_HISTORY_RECORDS = 10_000
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
def _dns_override(hostname: str):
    ips = _public_dns_ips(hostname)
    if not ips:
        yield
        return

    original_getaddrinfo = socket.getaddrinfo

    def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if host != hostname:
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
        with _dns_override(DATA_API_HOST):
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


def _request_absolute_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: Tuple[int, int] = REQUEST_TIMEOUT,
) -> Any:
    try:
        hostname = urlparse(url).hostname
        with _dns_override(hostname) if hostname else contextlib.nullcontext():
            response = _SESSION.get(url, params=params, timeout=timeout)
        response.raise_for_status()
    except requests.exceptions.Timeout as exc:
        raise PolymarketAPIError(f"Timeout przy {url}.") from exc
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise PolymarketAPIError(f"HTTP {status} dla {url}.") from exc
    except requests.exceptions.RequestException as exc:
        raise PolymarketAPIError(f"Blad polaczenia dla {url}: {exc}") from exc

    try:
        return response.json()
    except ValueError as exc:
        raise PolymarketAPIError(f"Niepoprawny JSON z {url}.") from exc


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


def get_user_trades_history(
    address: str,
    max_records: int = 2_000,
    page_size: int = 500,
) -> List[Dict[str, Any]]:
    """Fetch a deterministic paginated trade history for one public wallet."""
    address = validate_wallet(address)
    max_records = min(max(int(max_records), 1), MAX_HISTORY_RECORDS)
    page_size = min(max(int(page_size), 1), MAX_LIMIT)
    records: List[Dict[str, Any]] = []
    offset = 0

    while len(records) < max_records and offset <= 10_000:
        limit = min(page_size, max_records - len(records))
        page = _extract_records(_request_json(
            "/trades",
            params={
                "user": address,
                "limit": limit,
                "offset": offset,
                "takerOnly": "false",
            },
        ))
        if not page:
            break
        records.extend(page)
        if len(page) < limit:
            break
        offset += len(page)

    unique = {}
    for record in records:
        key = (
            record.get("transactionHash"),
            record.get("asset"),
            record.get("timestamp"),
            record.get("side"),
            record.get("price"),
            record.get("size"),
        )
        unique[key] = record
    return list(unique.values())[:max_records]


def _get_paginated_profile_records(
    endpoint: str,
    address: str,
    max_records: int,
    page_size: int,
) -> List[Dict[str, Any]]:
    address = validate_wallet(address)
    max_records = min(max(int(max_records), 1), MAX_HISTORY_RECORDS)
    page_size = min(max(int(page_size), 1), 500)
    records: List[Dict[str, Any]] = []
    offset = 0

    while len(records) < max_records and offset <= 100_000:
        limit = min(page_size, max_records - len(records))
        page = None
        for attempt in range(4):
            try:
                page = _extract_records(_request_json(
                    endpoint,
                    params={"user": address, "limit": limit, "offset": offset},
                ))
                break
            except PolymarketAPIError as exc:
                if "HTTP 429" not in str(exc) or attempt == 3:
                    raise
                time.sleep(5 * (attempt + 1))
        page = page or []
        if not page:
            break
        records.extend(page)
        if len(page) < limit:
            break
        offset += len(page)
        time.sleep(0.35)
    return records


def get_current_positions(
    address: str,
    max_records: int = 2_000,
) -> List[Dict[str, Any]]:
    return _get_paginated_profile_records(
        "/positions", address, max_records=max_records, page_size=500
    )


def get_closed_positions(
    address: str,
    max_records: int = 2_000,
) -> List[Dict[str, Any]]:
    return _get_paginated_profile_records(
        "/closed-positions", address, max_records=max_records, page_size=50
    )


def get_order_book(token_id: str) -> Dict[str, Any]:
    if not str(token_id).strip():
        raise ValueError("Brak token_id dla orderbooka.")
    data = _request_absolute_json(
        f"{CLOB_API}/book",
        params={"token_id": str(token_id).strip()},
    )
    if not isinstance(data, dict):
        raise PolymarketAPIError("CLOB zwrocil niepoprawny orderbook.")
    return data


def get_fee_rate_bps(token_id: str) -> int:
    if not str(token_id).strip():
        return 0
    data = _request_absolute_json(
        f"{CLOB_API}/fee-rate",
        params={"token_id": str(token_id).strip()},
    )
    if isinstance(data, dict):
        for key in ("base_fee", "fee_rate_bps", "feeRateBps"):
            if key in data:
                try:
                    return int(float(data[key]))
                except (TypeError, ValueError):
                    pass
    return 0


def check_geoblock() -> Dict[str, Any]:
    data = _request_absolute_json(GEOBLOCK_URL, timeout=TEST_TIMEOUT)
    if not isinstance(data, dict):
        raise PolymarketAPIError("Niepoprawna odpowiedz geoblock.")
    return {
        "blocked": bool(data.get("blocked", True)),
        "country": str(data.get("country", "")),
        "region": str(data.get("region", "")),
        "ip": str(data.get("ip", "")),
    }


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

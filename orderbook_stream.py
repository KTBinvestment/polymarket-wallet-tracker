import json
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional

import websocket

from polymarket_api import _public_dns_ips


MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class OrderBookStream:
    def __init__(self, output_path: Path):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.books: Dict[str, Dict] = {}
        self.last_error = ""
        self.connected = False
        self._app: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._assets = []

    def start(self, asset_ids: Iterable[str]):
        assets = list(dict.fromkeys(str(asset) for asset in asset_ids if asset))
        if not assets:
            raise ValueError("Brak asset_ids do subskrypcji.")
        self._assets = assets
        self._app = websocket.WebSocketApp(
            MARKET_WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
        )
        self._thread.start()

    def _run(self):
        hostname = "ws-subscriptions-clob.polymarket.com"
        ips = _public_dns_ips(hostname)
        original_getaddrinfo = socket.getaddrinfo

        def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            if host != hostname or not ips:
                return original_getaddrinfo(
                    host, port, family, type, proto, flags
                )
            results = []
            for ip in ips:
                results.extend(original_getaddrinfo(
                    ip, port, family, type, proto, flags
                ))
            return results

        socket.getaddrinfo = patched_getaddrinfo
        try:
            self._app.run_forever(ping_interval=10, ping_timeout=5)
        finally:
            socket.getaddrinfo = original_getaddrinfo

    def stop(self):
        if self._app:
            self._app.close()

    def snapshot(self, asset_id: str) -> Optional[Dict]:
        return self.books.get(str(asset_id))

    def _on_open(self, ws):
        self.connected = True
        ws.send(json.dumps({
            "assets_ids": self._assets,
            "type": "market",
            "custom_feature_enabled": True,
        }))

    def _on_message(self, _ws, message):
        try:
            payload = json.loads(message)
        except ValueError:
            return
        events = payload if isinstance(payload, list) else [payload]
        for event in events:
            if not isinstance(event, dict):
                continue
            asset_id = str(event.get("asset_id", ""))
            event_type = event.get("event_type")
            if event_type == "book" and asset_id:
                self.books[asset_id] = {
                    "asset_id": asset_id,
                    "market": event.get("market"),
                    "bids": event.get("bids", []),
                    "asks": event.get("asks", []),
                    "timestamp": event.get("timestamp"),
                    "received_at": datetime.now(timezone.utc).isoformat(),
                }
            self._append_event(event)

    def _append_event(self, event: Dict):
        row = {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        with self.output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    def _on_error(self, _ws, error):
        self.last_error = str(error)

    def _on_close(self, _ws, _status, _message):
        self.connected = False

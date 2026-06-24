import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

import pandas as pd

from polymarket_api import (
    get_closed_positions,
    get_current_positions,
    get_user_trades_history,
)


class SnapshotStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.trades_path = self.data_dir / "latest_raw.csv"
        self.open_positions_path = self.data_dir / "open_positions.csv"
        self.closed_positions_path = self.data_dir / "closed_positions.csv"
        self.meta_path = self.data_dir / "snapshot_meta.json"

    def exists(self) -> bool:
        return self.trades_path.exists()

    def metadata(self) -> Dict:
        if not self.meta_path.exists():
            return {}
        try:
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def load_trades(self) -> pd.DataFrame:
        return pd.read_csv(self.trades_path)

    def load_open_positions(self) -> pd.DataFrame:
        if not self.open_positions_path.exists():
            return pd.DataFrame()
        return pd.read_csv(self.open_positions_path)

    def load_closed_positions(self) -> pd.DataFrame:
        if not self.closed_positions_path.exists():
            return pd.DataFrame()
        return pd.read_csv(self.closed_positions_path)

    def save(
        self,
        trades: pd.DataFrame,
        open_positions: pd.DataFrame,
        closed_positions: pd.DataFrame,
        metadata: Dict,
    ) -> None:
        self._atomic_csv(trades, self.trades_path)
        self._atomic_csv(open_positions, self.open_positions_path)
        self._atomic_csv(closed_positions, self.closed_positions_path)
        payload = {
            **metadata,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "trade_rows": len(trades),
            "open_position_rows": len(open_positions),
            "closed_position_rows": len(closed_positions),
        }
        self._atomic_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            self.meta_path,
        )

    @staticmethod
    def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        frame.to_csv(temporary, index=False)
        os.replace(temporary, path)

    @staticmethod
    def _atomic_text(text: str, path: Path) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)


def _with_wallet_metadata(
    records: Iterable[Dict],
    wallet: str,
    source: str,
) -> List[Dict]:
    rows = []
    for item in records:
        row = dict(item)
        row["watchedWallet"] = wallet
        row["walletShort"] = f"{wallet[:6]}...{wallet[-4:]}"
        row["source"] = source
        rows.append(row)
    return rows


def build_full_snapshot(
    wallets: Iterable[str],
    trades_per_wallet: int,
    positions_per_wallet: int = 2_000,
) -> Dict[str, pd.DataFrame]:
    trade_rows: List[Dict] = []
    open_rows: List[Dict] = []
    closed_rows: List[Dict] = []
    status_rows: List[Dict] = []

    for wallet in wallets:
        status = {
            "wallet": wallet,
            "trades": 0,
            "open_positions": 0,
            "closed_positions": 0,
            "error": "",
        }
        try:
            trades = get_user_trades_history(wallet, max_records=trades_per_wallet)
            opens = get_current_positions(wallet, max_records=positions_per_wallet)
            closed = get_closed_positions(wallet, max_records=positions_per_wallet)
            trade_rows.extend(_with_wallet_metadata(trades, wallet, "trades"))
            open_rows.extend(_with_wallet_metadata(opens, wallet, "positions"))
            closed_rows.extend(_with_wallet_metadata(closed, wallet, "closed-positions"))
            status.update({
                "trades": len(trades),
                "open_positions": len(opens),
                "closed_positions": len(closed),
            })
        except Exception as exc:
            status["error"] = str(exc)
        status_rows.append(status)

    return {
        "trades": pd.DataFrame(trade_rows),
        "open_positions": pd.DataFrame(open_rows),
        "closed_positions": pd.DataFrame(closed_rows),
        "status": pd.DataFrame(status_rows),
    }

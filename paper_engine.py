import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from market_microstructure import FillEstimate


@dataclass
class RiskLimits:
    starting_bankroll: float = 1_000.0
    risk_per_trade_pct: float = 1.0
    max_stake_usdc: float = 25.0
    max_daily_loss_usdc: float = 30.0
    max_market_exposure_usdc: float = 75.0
    max_total_exposure_usdc: float = 200.0
    max_open_positions: int = 8
    min_fill_ratio: float = 0.8
    max_spread_pct_points: float = 3.0
    max_slippage_pct_points: float = 2.0


class PaperLedger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self):
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS signals (
                    signal_key TEXT PRIMARY KEY,
                    detected_at TEXT NOT NULL,
                    leader_wallet TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    condition_id TEXT,
                    title TEXT,
                    side TEXT NOT NULL,
                    leader_price REAL,
                    requested_stake REAL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    fill_json TEXT
                );
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_key TEXT UNIQUE NOT NULL,
                    opened_at TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    condition_id TEXT,
                    title TEXT,
                    shares REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_cost REAL NOT NULL,
                    entry_fee REAL NOT NULL,
                    mark_price REAL,
                    unrealized_pnl REAL DEFAULT 0,
                    realized_pnl REAL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'OPEN'
                );
                """
            )
            columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(positions)").fetchall()
            }
            migrations = {
                "leader_wallet": "TEXT",
                "closed_at": "TEXT",
                "exit_price": "REAL",
                "exit_fee": "REAL DEFAULT 0",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    db.execute(
                        f"ALTER TABLE positions ADD COLUMN {name} {definition}"
                    )
            db.execute(
                """
                UPDATE positions
                SET leader_wallet=(
                    SELECT leader_wallet FROM signals
                    WHERE signals.signal_key=positions.signal_key
                )
                WHERE leader_wallet IS NULL
                """
            )
            db.execute(
                "INSERT OR IGNORE INTO state(key, value) VALUES('emergency_stop', '0')"
            )

    def emergency_stop(self) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT value FROM state WHERE key='emergency_stop'"
            ).fetchone()
            return bool(row and row["value"] == "1")

    def set_emergency_stop(self, enabled: bool):
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO state(key, value) VALUES('emergency_stop', ?)",
                ("1" if enabled else "0",),
            )

    def has_signal(self, signal_key: str) -> bool:
        with self._connect() as db:
            return db.execute(
                "SELECT 1 FROM signals WHERE signal_key=?", (signal_key,)
            ).fetchone() is not None

    def totals(self) -> Dict:
        today = datetime.now(timezone.utc).date().isoformat()
        with self._connect() as db:
            open_row = db.execute(
                """
                SELECT COUNT(*) AS positions,
                       COALESCE(SUM(entry_cost), 0) AS exposure,
                       COALESCE(SUM(unrealized_pnl), 0) AS unrealized
                FROM positions WHERE status='OPEN'
                """
            ).fetchone()
            daily = db.execute(
                """
                SELECT COALESCE(SUM(realized_pnl), 0) AS pnl
                FROM positions
                WHERE substr(opened_at, 1, 10)=?
                """,
                (today,),
            ).fetchone()
        return {
            "open_positions": int(open_row["positions"]),
            "total_exposure": float(open_row["exposure"]),
            "unrealized_pnl": float(open_row["unrealized"]),
            "daily_realized_pnl": float(daily["pnl"]),
        }

    def market_exposure(self, condition_id: str) -> float:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT COALESCE(SUM(entry_cost), 0) AS exposure
                FROM positions
                WHERE status='OPEN' AND condition_id=?
                """,
                (condition_id,),
            ).fetchone()
            return float(row["exposure"])

    def record_rejection(self, signal: Dict, reason: str):
        self._record_signal(signal, "REJECTED", reason, None, 0)

    def record_fill(self, signal: Dict, stake: float, fill: FillEstimate):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO signals(
                    signal_key, detected_at, leader_wallet, token_id,
                    condition_id, title, side, leader_price, requested_stake,
                    status, reason, fill_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'FILLED', ?, ?)
                """,
                (
                    signal["signal_key"], now, signal["leader_wallet"],
                    signal["token_id"], signal.get("condition_id"),
                    signal.get("title"), signal["side"],
                    signal.get("leader_price"), stake, fill.reason,
                    json.dumps(fill.to_dict()),
                ),
            )
            db.execute(
                """
                INSERT INTO positions(
                    signal_key, opened_at, leader_wallet, token_id,
                    condition_id, title,
                    shares, entry_price, entry_cost, entry_fee, mark_price
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal["signal_key"], now, signal["leader_wallet"],
                    signal["token_id"],
                    signal.get("condition_id"), signal.get("title"),
                    fill.filled_shares, fill.average_price, fill.filled_usdc,
                    fill.fee_usdc, fill.average_price,
                ),
            )

    def open_positions_for(
        self,
        leader_wallet: str,
        token_id: str,
    ):
        with self._connect() as db:
            return db.execute(
                """
                SELECT * FROM positions
                WHERE status='OPEN' AND leader_wallet=? AND token_id=?
                ORDER BY opened_at ASC
                """,
                (leader_wallet, token_id),
            ).fetchall()

    def record_exit(self, signal: Dict, fill: FillEstimate):
        positions = self.open_positions_for(
            signal["leader_wallet"], signal["token_id"]
        )
        if not positions or fill.filled_shares <= 0:
            self.record_rejection(signal, "brak pozycji do zamkniecia")
            return

        now = datetime.now(timezone.utc).isoformat()
        remaining_shares = fill.filled_shares
        total_exit_shares = fill.filled_shares
        exit_fee_remaining = fill.fee_usdc
        with self._connect() as db:
            self._record_signal_with_connection(
                db, signal, "EXIT", fill.reason, fill, fill.filled_usdc
            )
            for position in positions:
                if remaining_shares <= 1e-9:
                    break
                original_shares = float(position["shares"])
                sold_shares = min(original_shares, remaining_shares)
                sold_fraction = sold_shares / original_shares
                allocated_entry_cost = float(position["entry_cost"]) * sold_fraction
                allocated_entry_fee = float(position["entry_fee"]) * sold_fraction
                allocated_exit_fee = (
                    fill.fee_usdc * sold_shares / total_exit_shares
                    if total_exit_shares
                    else 0
                )
                proceeds = sold_shares * float(fill.average_price)
                realized = (
                    proceeds - allocated_entry_cost
                    - allocated_entry_fee - allocated_exit_fee
                )
                remaining_position_shares = original_shares - sold_shares
                if remaining_position_shares <= 1e-9:
                    db.execute(
                        """
                        UPDATE positions
                        SET shares=0, entry_cost=0, entry_fee=0,
                            mark_price=?, unrealized_pnl=0,
                            realized_pnl=realized_pnl+?,
                            status='CLOSED', closed_at=?,
                            exit_price=?, exit_fee=exit_fee+?
                        WHERE id=?
                        """,
                        (
                            fill.average_price, realized, now,
                            fill.average_price, allocated_exit_fee,
                            position["id"],
                        ),
                    )
                else:
                    db.execute(
                        """
                        UPDATE positions
                        SET shares=?, entry_cost=?, entry_fee=?,
                            mark_price=?, unrealized_pnl=0,
                            realized_pnl=realized_pnl+?,
                            exit_price=?, exit_fee=exit_fee+?
                        WHERE id=?
                        """,
                        (
                            remaining_position_shares,
                            float(position["entry_cost"]) - allocated_entry_cost,
                            float(position["entry_fee"]) - allocated_entry_fee,
                            fill.average_price, realized, fill.average_price,
                            allocated_exit_fee, position["id"],
                        ),
                    )
                remaining_shares -= sold_shares
                exit_fee_remaining -= allocated_exit_fee

    def _record_signal(
        self,
        signal: Dict,
        status: str,
        reason: str,
        fill: Optional[FillEstimate],
        stake: float,
    ):
        with self._connect() as db:
            self._record_signal_with_connection(
                db, signal, status, reason, fill, stake
            )

    @staticmethod
    def _record_signal_with_connection(
        db,
        signal: Dict,
        status: str,
        reason: str,
        fill: Optional[FillEstimate],
        stake: float,
    ):
        db.execute(
            """
            INSERT OR IGNORE INTO signals(
                signal_key, detected_at, leader_wallet, token_id,
                condition_id, title, side, leader_price, requested_stake,
                status, reason, fill_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal["signal_key"],
                datetime.now(timezone.utc).isoformat(),
                signal["leader_wallet"],
                signal["token_id"],
                signal.get("condition_id"),
                signal.get("title"),
                signal["side"],
                signal.get("leader_price"),
                stake,
                status,
                reason,
                json.dumps(fill.to_dict()) if fill else None,
            ),
        )

    def mark_positions(self, marks: Dict[str, float]):
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, token_id, shares, entry_cost, entry_fee FROM positions WHERE status='OPEN'"
            ).fetchall()
            for row in rows:
                mark = marks.get(row["token_id"])
                if mark is None:
                    continue
                pnl = row["shares"] * mark - row["entry_cost"] - row["entry_fee"]
                db.execute(
                    "UPDATE positions SET mark_price=?, unrealized_pnl=? WHERE id=?",
                    (mark, pnl, row["id"]),
                )

    def frames(self):
        import pandas as pd

        with self._connect() as db:
            signals = pd.read_sql_query(
                "SELECT * FROM signals ORDER BY detected_at DESC", db
            )
            positions = pd.read_sql_query(
                "SELECT * FROM positions ORDER BY opened_at DESC", db
            )
        return signals, positions


def evaluate_risk(
    ledger: PaperLedger,
    limits: RiskLimits,
    condition_id: str,
    requested_stake: float,
    fill: FillEstimate,
) -> Tuple[bool, str]:
    if ledger.emergency_stop():
        return False, "awaryjny STOP jest wlaczony"
    totals = ledger.totals()
    if totals["daily_realized_pnl"] <= -abs(limits.max_daily_loss_usdc):
        return False, "osiagnieto limit straty dziennej"
    if totals["open_positions"] >= limits.max_open_positions:
        return False, "osiagnieto limit otwartych pozycji"
    if totals["total_exposure"] + fill.filled_usdc > limits.max_total_exposure_usdc:
        return False, "przekroczono calkowita ekspozycje"
    if (
        ledger.market_exposure(condition_id) + fill.filled_usdc
        > limits.max_market_exposure_usdc
    ):
        return False, "przekroczono ekspozycje na jeden rynek"
    if fill.fill_ratio < limits.min_fill_ratio:
        return False, "za mala czesc zlecenia moglaby zostac wykonana"
    if fill.spread is not None and fill.spread * 100 > limits.max_spread_pct_points:
        return False, "spread jest za szeroki"
    if (
        fill.slippage_pct_points is not None
        and fill.slippage_pct_points > limits.max_slippage_pct_points
    ):
        return False, "poslizg jest za duzy"
    if fill.filled_usdc <= 0 or requested_stake <= 0:
        return False, "brak dostepnej plynnosci"
    return True, "OK"

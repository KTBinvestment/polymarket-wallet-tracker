import tempfile
import unittest
from pathlib import Path

from market_microstructure import FillEstimate
from paper_engine import PaperLedger, RiskLimits, evaluate_risk


def good_fill():
    return FillEstimate(
        side="BUY",
        requested_usdc=10,
        requested_shares=20,
        filled_usdc=10,
        filled_shares=20,
        average_price=0.5,
        best_price=0.5,
        worst_price=0.5,
        spread=0.01,
        slippage_pct_points=0,
        fee_usdc=0.15,
        fill_ratio=1,
        fully_filled=True,
        levels_used=1,
        reason="pelne wykonanie",
    )


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = PaperLedger(Path(self.temp.name) / "paper.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_emergency_stop_rejects_signal(self):
        self.ledger.set_emergency_stop(True)
        allowed, reason = evaluate_risk(
            self.ledger, RiskLimits(), "market", 10, good_fill()
        )
        self.assertFalse(allowed)
        self.assertIn("STOP", reason)

    def test_good_fill_is_allowed(self):
        allowed, reason = evaluate_risk(
            self.ledger, RiskLimits(), "market", 10, good_fill()
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "OK")

    def test_sell_closes_paper_position(self):
        entry = good_fill()
        signal = {
            "signal_key": "buy-1",
            "leader_wallet": "0xleader",
            "token_id": "token",
            "condition_id": "market",
            "title": "Test",
            "side": "BUY",
            "leader_price": 0.5,
        }
        self.ledger.record_fill(signal, 10, entry)
        exit_fill = good_fill()
        exit_fill.side = "SELL"
        exit_fill.average_price = 0.6
        exit_signal = {**signal, "signal_key": "sell-1", "side": "SELL"}
        self.ledger.record_exit(exit_signal, exit_fill)
        with self.ledger._connect() as db:
            position = db.execute("SELECT * FROM positions").fetchone()
        self.assertEqual(position["status"], "CLOSED")
        self.assertGreater(position["realized_pnl"], 0)


if __name__ == "__main__":
    unittest.main()

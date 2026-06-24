import unittest

from market_microstructure import estimate_market_fill, taker_fee_usdc


class MarketMicrostructureTests(unittest.TestCase):
    def test_buy_walks_multiple_ask_levels(self):
        book = {
            "bids": [{"price": "0.49", "size": "100"}],
            "asks": [
                {"price": "0.50", "size": "10"},
                {"price": "0.51", "size": "20"},
            ],
        }
        fill = estimate_market_fill(
            book, "BUY", 10.1, max_slippage_pct_points=2
        )
        self.assertTrue(fill.fully_filled)
        self.assertEqual(fill.levels_used, 2)
        self.assertGreater(fill.average_price, 0.50)
        self.assertAlmostEqual(fill.spread, 0.01)

    def test_fill_is_partial_when_depth_is_insufficient(self):
        book = {
            "bids": [],
            "asks": [{"price": "0.50", "size": "2"}],
        }
        fill = estimate_market_fill(book, "BUY", 10)
        self.assertFalse(fill.fully_filled)
        self.assertAlmostEqual(fill.filled_usdc, 1)
        self.assertAlmostEqual(fill.fill_ratio, 0.1)

    def test_sports_fee_curve(self):
        self.assertAlmostEqual(taker_fee_usdc(100, 0.5, 0.03), 0.75)


if __name__ == "__main__":
    unittest.main()

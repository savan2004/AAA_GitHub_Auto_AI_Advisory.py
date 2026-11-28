"""Synthetic contract checks; no live prices, network, Telegram or Neon."""
import unittest
from msk_central_data_v4 import (
    BATCH_LIMIT, Dataset, build_universe, normalize_symbol, plan_missing,
)


class CentralDataV4Tests(unittest.TestCase):
    def setUp(self):
        self.core = [f"CORE{i}" for i in range(500)]

    def test_core_and_overlapping_requests(self):
        u = build_universe(self.core, ["core0.NS", " extra1 ", "EXTRA1.NS"])
        self.assertEqual(len(u.core), 500)
        self.assertEqual(u.extras, ("EXTRA1",))

    def test_maximum_and_overflow(self):
        extras = [f"EXTRA{i}" for i in range(200)]
        self.assertEqual(len(build_universe(self.core, extras).symbols), 700)
        with self.assertRaises(ValueError):
            build_universe(self.core, extras + ["EXTRA200"])

    def test_invalid_core_never_silently_replaced(self):
        for core in (self.core[:-1], self.core + ["CORE500"],
                     self.core[:-1] + ["core0.NS"]):
            with self.subTest(size=len(core)), self.assertRaises(ValueError):
                build_universe(core)

    def test_bad_symbol(self):
        for symbol in ("", "../BAD", "A B", None):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                normalize_symbol(symbol)

    def test_bounded_deduplicated_batches(self):
        u = build_universe(self.core)
        plan = plan_missing(u, {Dataset.DAILY_OHLCV: self.core + ["core0.NS"]})
        self.assertEqual(len(plan), 10)
        self.assertTrue(all(len(b.symbols) <= BATCH_LIMIT for b in plan))
        self.assertEqual(tuple(s for b in plan for s in b.symbols), u.core)

    def test_extras_on_demand_and_dataset_separation(self):
        u = build_universe(self.core, ["EXTRA1"])
        plan = plan_missing(u, {Dataset.RESULTS: ["EXTRA1"], Dataset.FUNDAMENTAL: ["EXTRA1"]})
        self.assertEqual(len(plan), 2)
        self.assertEqual([b.symbols for b in plan], [("EXTRA1",), ("EXTRA1",)])
        self.assertEqual(plan_missing(u, {}), ())

    def test_outside_universe_and_invalid_dataset(self):
        u = build_universe(self.core)
        for data in ({Dataset.RESULTS: ["UNKNOWN"]}, {"unapproved": ["CORE0"]}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                plan_missing(u, data)

    def test_invalid_batch_size(self):
        u = build_universe(self.core)
        for size in (0, 51, True, 2.5):
            with self.subTest(size=size), self.assertRaises(ValueError):
                plan_missing(u, {}, batch_size=size)


if __name__ == "__main__":
    unittest.main()

import unittest
from msk_ai_builder_profile import ResearchProfile

class ProfileTests(unittest.TestCase):
    def test_capital_boundaries(self):
        for capital, count in [(1,5),(1000000,5),(1000001,10),
                               (5000000,10),(5000001,20),(10000000,20)]:
            with self.subTest(capital=capital):
                self.assertEqual(ResearchProfile(capital,"medium",18).max_stocks,count)

    def test_invalid_inputs(self):
        for args in [(0,"medium",18),(10000001,"medium",18),
                     (True,"medium",18),(100.5,"medium",18),
                     (100,"unknown",18),(100,"low",12),(100,"low",True)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                ResearchProfile(*args)

    def test_short_horizon(self):
        for months in (6,18,36,60):
            self.assertEqual(ResearchProfile(1000000,"medium",months).short_horizon,
                             months <= 18)

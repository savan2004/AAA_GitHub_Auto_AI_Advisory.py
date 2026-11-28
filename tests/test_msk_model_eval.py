import json
import unittest
from scripts.msk_model_eval import CASES,score

class ModelEvalTests(unittest.TestCase):
    def test_exact_fixture_scoring(self):
        baseline={c["id"]:json.dumps(c["expected"]) for c in CASES}
        self.assertEqual(score(baseline)["score"],100)
        for wrong in ('{"price":65,"rsi":100,"roe":15.5}', '{"price":100,"rsi":65,"roe":true}', '{"price":100,"price":100,"rsi":65,"roe":15.5}'):
            self.assertEqual(score(dict(baseline,**{"metric-binding":wrong}))["score"],80)
        self.assertEqual(score(dict(baseline,**{"missing-target":'{"price":100,"target":0}'}))["score"],80)
        self.assertEqual(score({})["score"],0)

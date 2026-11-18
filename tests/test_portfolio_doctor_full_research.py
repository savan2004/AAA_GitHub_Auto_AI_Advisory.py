"""Full-equity-report handoff from private Doctor: opt-in, no holding leakage."""
import ast
import copy
import html
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from portfolio_doctor_research_links import full_research_choices


SOURCE = Path(__file__).resolve().parents[1] / "main.py"


def callback_function(env):
    source = ast.parse(SOURCE.read_text(encoding="utf-8"))
    item = next(node for node in source.body
                if isinstance(node, ast.FunctionDef) and
                node.name == "handle_analysis_callback")
    item = copy.deepcopy(item)
    item.decorator_list = []
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[item], type_ignores=[])), str(SOURCE), "exec"), env)
    return env["handle_analysis_callback"]


class ResearchHandoffTests(unittest.TestCase):
    def test_one_button_per_nse_holding_no_quantity_or_cost(self):
        data = b"Symbol,Qty,Buy Price\nSAMPLEA,7,120\nSAMPLEB,800,32.5"
        choices = full_research_choices("input.csv", data)
        self.assertEqual(choices, (
            ("SAMPLEA", "doctorpdf|SAMPLEA"),
            ("SAMPLEB", "doctorpdf|SAMPLEB")))
        self.assertNotIn("800", str(choices))
        self.assertNotIn("32.5", str(choices))

    def test_bse_only_and_malformed_inputs_cannot_be_mislinked(self):
        self.assertEqual(
            full_research_choices("typed.csv",
                b"BSE:SAMPLEA 7 120\nSAMPLEB.NS 3 100"),
            (("SAMPLEB", "doctorpdf|SAMPLEB"),))
        self.assertEqual(full_research_choices("input.csv", b"invalid holdings"), ())
        self.assertEqual(full_research_choices("input.pdf", b"SAMPLEA 1 2"), ())

    def test_private_explicit_tap_reuses_full_report_generator(self):
        delivered, notices, acknowledgements = [], [], []
        env = {
            "state": SimpleNamespace(clear=lambda uid: None),
            "re": re, "html": html,
            "bot": SimpleNamespace(answer_callback_query=lambda *a, **kw:
                                   acknowledgements.append((a, kw))),
            "API_RATE_LIMITER": SimpleNamespace(is_allowed=lambda uid: True),
            "resolve_symbol": lambda sym: (sym + ".NS", sym),
            "safe_send": lambda uid, msg, **kw: notices.append(msg),
            "executor": SimpleNamespace(submit=lambda fn, *args:
                                        delivered.append((fn, args))),
            "run_report": lambda *args: None,
        }
        fn = callback_function(env)
        call = SimpleNamespace(
            data="doctorpdf|SAMPLEA", id="query",
            message=SimpleNamespace(chat=SimpleNamespace(id=101, type="private")),
            from_user=SimpleNamespace(id=101))
        fn(call)
        self.assertEqual(delivered, [(env["run_report"], (101, "SAMPLEA"))])
        self.assertTrue(acknowledgements)
        self.assertIn("separate full research PDF", notices[-1])

    def test_private_tap_rejects_wrong_identity_and_bse(self):
        for user_id, kind, ticker in (
            (102, "private", "SAMPLEA.NS"),
            (101, "group", "SAMPLEA.NS"),
            (101, "private", "SAMPLEA.BO"),
            (101, "private", None),
        ):
            with self.subTest(actor=user_id, chat_type=kind, ticker=ticker):
                delivered, notices = [], []
                env = {
                    "state": SimpleNamespace(clear=lambda uid: None),
                    "re": re, "html": html,
                    "bot": SimpleNamespace(answer_callback_query=lambda *a, **kw: None),
                    "API_RATE_LIMITER": SimpleNamespace(is_allowed=lambda uid: True),
                    "resolve_symbol": lambda sym: (ticker, sym),
                    "safe_send": lambda uid, msg, **kw: notices.append(msg),
                    "executor": SimpleNamespace(submit=lambda *args:
                                                delivered.append(args)),
                    "run_report": lambda *args: None,
                }
                fn = callback_function(env)
                call = SimpleNamespace(
                    data="doctorpdf|SAMPLEA", id="query",
                    message=SimpleNamespace(chat=SimpleNamespace(id=101, type=kind)),
                    from_user=SimpleNamespace(id=user_id))
                fn(call)
                self.assertFalse(delivered)
                self.assertTrue(notices)


if __name__ == "__main__":
    unittest.main()

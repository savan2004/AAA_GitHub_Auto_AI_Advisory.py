"""Real production handler AST, mocked providers: three inputs, one PDF attempt."""
import ast
import copy
import logging
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_portfolio_builder_form import TREE, UID, harness
from msk_ai_builder_automatic import ResearchUnavailable


def with_automatic_handlers():
    ns, sent, submitted, answers, msg, click, clock = harness()
    node = next(x for x in TREE.body
                if isinstance(x, ast.FunctionDef) and x.name == "send_automatic_builder_pdf")
    node = copy.deepcopy(node)
    node.decorator_list = []
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 "<real-auto-handler>", "exec"), ns)
    ns["_portfolio_doctor_slots"] = threading.BoundedSemaphore(1)
    ns["executor"] = SimpleNamespace(submit=lambda fn: fn())
    ns["logger"] = logging.getLogger("builder-auto-wizard-fixture")
    return ns, sent, submitted, answers, msg, click, clock


class AutomaticWizardTests(unittest.TestCase):
    def test_three_input_automatic_pdf_single_delivery(self):
        ns, sent, submitted, answers, msg, click, clock = with_automatic_handlers()
        receipt = SimpleNamespace(message_id=501, document=SimpleNamespace(file_id="dummy"))
        with patch("msk_ai_builder_automatic.automatic_available", return_value=True), \
             patch("msk_ai_builder_telegram.deliver_automatic_pdf",
                   return_value=receipt) as deliver:
            ns["portfolio_builder_button"](msg("🏗️ Portfolio Builder"))
            self.assertIn("Step 1/3", sent[-1][1])
            self.assertEqual(ns["_builder_form"][UID]["step"], "amount")
            self.assertEqual(ns["_builder_form"][UID]["mode"], "automatic")

            ns["portfolio_builder_form_text"](msg("16000000"), "16000000")
            self.assertEqual(ns["_builder_form"][UID]["step"], "amount")
            ns["portfolio_builder_form_text"](msg("1000000"), "1000000")
            self.assertIn("Step 2/3", sent[-1][1])
            ns["portfolio_builder_option"](click("builder|risk|medium"), "risk|medium")
            self.assertIn("Step 3/3", sent[-1][1])
            ns["portfolio_builder_option"](click("builder|horizon|36"), "horizon|36")
            ns["portfolio_builder_option"](click("builder|horizon|36"), "horizon|36")
            deliver.assert_called_once_with(ns["bot"], UID, 1_000_000, "medium", 36)

        self.assertFalse(ns["_builder_form"])
        self.assertFalse(ns["_portfolio_doctor_running"])
        self.assertTrue(ns["_portfolio_doctor_slots"].acquire(blocking=False))
        self.assertEqual(submitted, [])
        self.assertTrue(all("SBIN 25" not in t for _, t, _ in sent))

    def test_disabled_or_missing_approved_research_never_collects_user_input(self):
        ns, sent, submitted, _, msg, _, _ = with_automatic_handlers()
        with patch("msk_ai_builder_automatic.automatic_available", return_value=False):
            ns["portfolio_builder_button"](msg("🏗️ Portfolio Builder"))
        self.assertIn("temporarily unavailable", sent[-1][1])
        self.assertEqual(ns["_builder_form"], {})
        self.assertEqual(submitted, [])

    def test_mid_wizard_approval_revoked_does_not_select_or_send(self):
        ns, sent, submitted, _, msg, click, _ = with_automatic_handlers()
        with patch("msk_ai_builder_automatic.automatic_available", side_effect=[True, False]), \
             patch("msk_ai_builder_telegram.deliver_automatic_pdf") as deliver:
            ns["portfolio_builder_button"](msg("🏗️ Portfolio Builder"))
            ns["portfolio_builder_form_text"](msg("1000000"), "1000000")
            self.assertEqual(ns["_builder_form"], {})
            self.assertIn("NO VERIFIED PORTFOLIO AVAILABLE", sent[-1][1])
            deliver.assert_not_called()

    def test_failed_pdf_is_withheld_no_duplicate_retry(self):
        ns, sent, submitted, _, msg, click, _ = with_automatic_handlers()
        with patch("msk_ai_builder_automatic.automatic_available", return_value=True), \
             patch("msk_ai_builder_telegram.deliver_automatic_pdf",
                   side_effect=ResearchUnavailable("NO_VERIFIED_PORTFOLIO_AVAILABLE")) as deliver:
            ns["portfolio_builder_button"](msg("🏗️ Portfolio Builder"))
            ns["portfolio_builder_form_text"](msg("5000000"), "5000000")
            ns["portfolio_builder_option"](click("builder|risk|low"), "risk|low")
            ns["portfolio_builder_option"](click("builder|horizon|6"), "horizon|6")
            self.assertIn("NO VERIFIED PORTFOLIO AVAILABLE", sent[-1][1])
            ns["portfolio_builder_option"](click("builder|horizon|6"), "horizon|6")
            deliver.assert_called_once()
        self.assertEqual(ns["_builder_form"], {})


if __name__ == "__main__":
    unittest.main()

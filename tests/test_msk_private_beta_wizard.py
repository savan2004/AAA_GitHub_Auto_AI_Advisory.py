"""Exercise actual Telegram form handlers without a network connection."""
import ast
import copy
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from test_portfolio_builder_form import TREE, UID, harness

ENV={"MSK_BUILDER_V2_PREVIEW_ENABLED":"1","MSK_BUILDER_V2_PREVIEW_USER_ID":str(UID)}


class BetaWizardTests(unittest.TestCase):
    def test_owner_three_steps_and_duplicate_callback(self):
        ns,sent,submitted,answers,msg,click,clock=harness()
        for name in ("portfolio_builder_private_beta","send_private_builder_beta"):
            node=copy.deepcopy(next(x for x in TREE.body if isinstance(x,ast.FunctionDef) and x.name==name))
            node.decorator_list=[]
            exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),"<beta>","exec"),ns)
        ns["_portfolio_doctor_slots"]=threading.BoundedSemaphore(1)
        ns["executor"]=SimpleNamespace(submit=lambda fn:fn())
        with patch.dict(os.environ,ENV),patch("msk_ai_builder_telegram.deliver_preview",
                    return_value=SimpleNamespace(message_id=1)) as send:
            ns["portfolio_builder_private_beta"](msg("/builder_v2_preview"))
            self.assertIn("Step 1/3",sent[-1][1])
            ns["portfolio_builder_form_text"](msg("1000000"),"1000000")
            self.assertIn("Step 2/3",sent[-1][1])
            ns["portfolio_builder_option"](click("builder|risk|medium"),"risk|medium")
            self.assertIn("Step 3/3",sent[-1][1])
            ns["portfolio_builder_option"](click("builder|horizon|36"),"horizon|36")
            ns["portfolio_builder_option"](click("builder|horizon|36"),"horizon|36")
            send.assert_called_once_with(ns["bot"],UID,1000000,"medium",36)
        self.assertFalse(submitted)
        self.assertFalse(ns["_builder_form"])
        self.assertFalse(ns["_portfolio_doctor_running"])

    def test_preview_cancels_doctor_and_audit_sessions(self):
        ns, sent, submitted, _, msg, _, clock = harness()
        node = copy.deepcopy(next(x for x in TREE.body
                                  if isinstance(x, ast.FunctionDef)
                                  and x.name == "portfolio_builder_private_beta"))
        node.decorator_list = []
        exec(compile(ast.fix_missing_locations(
            ast.Module(body=[node], type_ignores=[])), "<beta>", "exec"), ns)
        ns["_portfolio_doctor_waiting"][UID] = clock.t + 180
        ns["_portfolio_audit_mode"][UID] = clock.t + 180
        with patch.dict(os.environ, ENV):
            ns["portfolio_builder_private_beta"](msg("/builder_v2_preview"))
        self.assertEqual(ns["_portfolio_doctor_waiting"], {})
        self.assertEqual(ns["_portfolio_audit_mode"], {})
        self.assertEqual(ns["_builder_form"][UID]["mode"], "private_beta")
        self.assertEqual(ns["_builder_form"][UID]["step"], "amount")
        self.assertIn("Step 1/3", sent[-1][1])
        self.assertEqual(submitted, [])


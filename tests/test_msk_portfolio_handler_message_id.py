"""Run actual /buy and /sell handler function bodies without Telegram or customer data.

This is handler-contract QA only. Real PostgreSQL transaction tests are separate.
"""
import ast
import copy
import logging
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "main.py"


class SyntheticStore:
    def __init__(self):
        self.holdings = {}
        self.operations = {}
        self.calls = []
        self.fail = False
        self.invalid_outcome = False

    def get(self, uid):
        return {symbol: data.copy() for symbol, data in self.holdings.get(uid, {}).items()}

    def add(self, uid, symbol, qty, price, *, telegram_message_id):
        self.calls.append(("buy", uid, symbol, qty, price, telegram_message_id))
        if self.fail:
            raise RuntimeError("synthetic database unavailable")
        if self.invalid_outcome:
            return None
        key = (uid, telegram_message_id)
        operation = ("buy", symbol, qty, price)
        if key in self.operations:
            if self.operations[key][0] != operation:
                raise RuntimeError("conflicting synthetic message identifier")
            return {"applied": False, "duplicate": True}
        current = self.holdings.setdefault(uid, {}).get(symbol, {"qty": 0, "avg": price})
        self.holdings[uid][symbol] = {"qty": current["qty"] + qty, "avg": price}
        self.operations[key] = (operation, True)
        return {"applied": True, "duplicate": False}

    def remove(self, uid, symbol, *, telegram_message_id):
        self.calls.append(("sell", uid, symbol, telegram_message_id))
        if self.fail:
            raise RuntimeError("synthetic database unavailable")
        if self.invalid_outcome:
            return None
        key = (uid, telegram_message_id)
        operation = ("sell", symbol)
        if key in self.operations:
            if self.operations[key][0] != operation:
                raise RuntimeError("conflicting synthetic message identifier")
            return {"removed": self.operations[key][1], "duplicate": True}
        removed = symbol in self.holdings.get(uid, {})
        self.holdings.setdefault(uid, {}).pop(symbol, None)
        self.operations[key] = (operation, removed)
        return {"removed": removed, "duplicate": False}


class LegacyStore:
    def __init__(self):
        self.holdings = {}
        self.calls = []

    def get(self, uid):
        return {symbol: data.copy() for symbol, data in self.holdings.get(uid, {}).items()}

    def add(self, uid, symbol, qty, price):
        self.calls.append(("buy", uid, symbol, qty, price))
        current = self.holdings.setdefault(uid, {}).get(symbol, {"qty": 0, "avg": price})
        self.holdings[uid][symbol] = {"qty": current["qty"] + qty, "avg": price}

    def remove(self, uid, symbol):
        self.calls.append(("sell", uid, symbol))
        return self.holdings.setdefault(uid, {}).pop(symbol, None) is not None


def real_handler_harness(store):
    """Load unchanged actual handler AST nodes, stripping Telegram decorators only."""
    source = SOURCE.read_text(encoding="utf-8")
    nodes = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in (
                "cmd_buy", "cmd_sell", "_require_private_portfolio"):
            clean = copy.deepcopy(node)
            clean.decorator_list = []
            nodes.append(clean)
    if set(n.name for n in nodes) != {
            "cmd_buy", "cmd_sell", "_require_private_portfolio"}:
        raise AssertionError("Required real portfolio handlers were not found")
    sent = []
    context = {
        "os": os,
        "portfolio": store,
        "logger": logging.getLogger("synthetic-portfolio-handler-qa"),
        "safe_send": lambda uid, message, *args, **kwargs: sent.append(message),
        "_allow_heavy": lambda m: True,
        "resolve_symbol": lambda symbol: (symbol.upper() + ".NS", None),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), context)
    return context, sent


def message(text, message_id=101, user=100, *, chat_type="private", actor=None):
    return SimpleNamespace(
        text=text, message_id=message_id,
        chat=SimpleNamespace(id=user, type=chat_type),
        from_user=SimpleNamespace(id=user if actor is None else actor))


class PortfolioCommandHandlerTests(unittest.TestCase):
    def test_buy_uses_genuine_message_id(self):
        store = SyntheticStore()
        ns, messages = real_handler_harness(store)
        with patch.dict(os.environ, {"PORTFOLIO_BACKEND": "postgres"}):
            ns["cmd_buy"](message("/buy SBIN 2 500", 202))
        self.assertEqual(store.calls[0], ("buy", 100, "SBIN", 2, 500.0, 202))
        self.assertEqual(store.holdings[100]["SBIN"]["qty"], 2)
        self.assertTrue(any("saved and read back" in msg for msg in messages))

    def test_sell_uses_genuine_message_id_and_reports_first_removal(self):
        store = SyntheticStore()
        store.holdings[100] = {"SBIN": {"qty": 2, "avg": 500}}
        ns, messages = real_handler_harness(store)
        with patch.dict(os.environ, {"PORTFOLIO_BACKEND": "postgres"}):
            ns["cmd_sell"](message("/sell SBIN", 302))
        self.assertEqual(store.calls, [("sell", 100, "SBIN", 302)])
        self.assertNotIn("SBIN", store.get(100))
        self.assertTrue(any("removed" in msg for msg in messages))

    def test_missing_invalid_message_ids_reject_before_any_write(self):
        for bad in (None, 0, -1, True, "101", 1.0, 9223372036854775808):
            for cmd in ("/buy SBIN 2 500", "/sell SBIN"):
                with self.subTest(bad=bad, cmd=cmd):
                    store = SyntheticStore()
                    ns, sent = real_handler_harness(store)
                    key = "cmd_buy" if cmd.startswith("/buy") else "cmd_sell"
                    with patch.dict(os.environ, {"PORTFOLIO_BACKEND": "postgres"}):
                        ns[key](message(cmd, bad))
                    self.assertEqual(store.calls, [])
                    self.assertEqual(store.holdings, {})
                    self.assertTrue(any("Invalid Telegram message ID" in text for text in sent))

    def test_duplicate_buy_does_not_add_holdings_twice(self):
        store = SyntheticStore()
        ns, sent = real_handler_harness(store)
        with patch.dict(os.environ, {"PORTFOLIO_BACKEND": "postgres"}):
            ns["cmd_buy"](message("/buy SBIN 2 500", 401))
            ns["cmd_buy"](message("/buy SBIN 2 500", 401))
        self.assertEqual(store.get(100)["SBIN"]["qty"], 2)
        self.assertEqual(len(store.calls), 2)
        self.assertTrue(any("already processed" in text for text in sent))
        self.assertEqual(sum("saved and read back" in text for text in sent), 1)

    def test_duplicate_sell_originally_not_found_does_not_claim_success(self):
        store = SyntheticStore()
        ns, sent = real_handler_harness(store)
        with patch.dict(os.environ, {"PORTFOLIO_BACKEND": "postgres"}):
            ns["cmd_sell"](message("/sell SBIN", 502))
            ns["cmd_buy"](message("/buy SBIN 1 510", 503))
            ns["cmd_sell"](message("/sell SBIN", 502))
        self.assertIn("SBIN", store.get(100))
        self.assertTrue(any("not found" in text for text in sent))
        self.assertTrue(any("already processed" in text for text in sent))
        self.assertFalse(any("removed" in text for text in sent))

    def test_duplicate_sell_that_removed_once_does_not_remove_later_rebuy(self):
        store = SyntheticStore()
        store.holdings[100] = {"SBIN": {"qty": 2, "avg": 500}}
        ns, sent = real_handler_harness(store)
        with patch.dict(os.environ, {"PORTFOLIO_BACKEND": "postgres"}):
            ns["cmd_sell"](message("/sell SBIN", 601))
            ns["cmd_buy"](message("/buy SBIN 1 510", 602))
            ns["cmd_sell"](message("/sell SBIN", 601))
        self.assertEqual(store.get(100)["SBIN"]["qty"], 1)
        self.assertEqual(sum("removed" in text for text in sent), 1)
        self.assertTrue(any("already processed" in text for text in sent))

    def test_unheld_symbol_returns_not_found(self):
        store = SyntheticStore()
        ns, sent = real_handler_harness(store)
        with patch.dict(os.environ, {"PORTFOLIO_BACKEND": "postgres"}):
            ns["cmd_sell"](message("/sell SBIN", 701))
        self.assertEqual(len(store.calls), 1)
        self.assertTrue(any("not found" in text for text in sent))
        self.assertFalse(any("removed" in text for text in sent))

    def test_legacy_json_never_receives_message_id_keyword(self):
        store = LegacyStore()
        ns, sent = real_handler_harness(store)
        with patch.dict(os.environ, {"PORTFOLIO_BACKEND": "legacy_json"}):
            ns["cmd_buy"](message("/buy SBIN 2 500", None))
            ns["cmd_sell"](message("/sell SBIN", None))
        self.assertEqual([row[0] for row in store.calls], ["buy", "sell"])
        self.assertFalse(any("Invalid Telegram message ID" in text for text in sent))
        self.assertTrue(any("removed" in text for text in sent))

    def test_postgres_unexpected_result_cannot_claim_success(self):
        for operation in ("buy", "sell"):
            with self.subTest(operation=operation):
                store = SyntheticStore()
                store.invalid_outcome = True
                ns, sent = real_handler_harness(store)
                with patch.dict(os.environ, {"PORTFOLIO_BACKEND": "postgres"}):
                    if operation == "buy":
                        ns["cmd_buy"](message("/buy SBIN 2 500"))
                    else:
                        ns["cmd_sell"](message("/sell SBIN"))
                self.assertTrue(any("could not be verified" in text for text in sent))
                self.assertFalse(any("removed" in text or "saved and read back" in text
                                     for text in sent))

    def test_database_outage_does_not_claim_holdings_unchanged_or_retry_safe(self):
        store = SyntheticStore()
        store.fail = True
        ns, sent = real_handler_harness(store)
        with patch.dict(os.environ, {"PORTFOLIO_BACKEND": "postgres"}):
            ns["cmd_buy"](message("/buy SBIN 1 500"))
            ns["cmd_sell"](message("/sell SBIN"))
        self.assertEqual(len(store.calls), 2)
        self.assertEqual(sum("Do not resend" in text for text in sent), 2)
        self.assertFalse(any("saved and read back" in text for text in sent))

    def test_nonprivate_portfolio_command_never_writes(self):
        store = SyntheticStore()
        ns, _ = real_handler_harness(store)
        with patch.dict(os.environ, {"PORTFOLIO_BACKEND": "postgres"}):
            ns["cmd_buy"](message("/buy SBIN 1 500", chat_type="group"))
            ns["cmd_sell"](message("/sell SBIN", actor=999))
        self.assertEqual(store.calls, [])


if __name__ == "__main__":
    unittest.main()

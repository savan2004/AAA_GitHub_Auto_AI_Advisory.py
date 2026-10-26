"""Offline runtime regression tests. No Telegram token or network connection needed."""
import ast
import json
import logging
import os
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace

TREE = ast.parse(Path(__file__).with_name("main.py").read_text(encoding="utf-8"))

def extract(kind, name, scope):
    node = next(n for n in TREE.body if isinstance(n, kind) and n.name == name)
    if isinstance(node, ast.FunctionDef):
        node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), scope)
    return scope[name]

class RuntimeRegressionTests(unittest.TestCase):
    def test_portfolio_remove_does_not_deadlock_and_persists(self):
        with tempfile.TemporaryDirectory() as folder:
            scope = dict(os=os, threading=threading, json=json, tempfile=tempfile,
                         logger=logging.getLogger("runtime-tests"))
            manager = extract(ast.ClassDef, "PortfolioManager", scope)
            store = manager(os.path.join(folder, "portfolio.json"))
            store.add(1, "INFY", 2, 100)
            results = []
            t = threading.Thread(target=lambda: results.append(store.remove(1, "INFY")), daemon=True)
            t.start()
            t.join(2)
            self.assertFalse(t.is_alive(), "Portfolio lock deadlock")
            self.assertEqual(results, [True])
            self.assertEqual(manager(os.path.join(folder, "portfolio.json")).get(1), {})

    def _webhook_fixture(self):
        class FakeBot:
            fail = False
            calls = 0
            def process_new_updates(self, updates):
                self.calls += 1
                if self.fail:
                    raise RuntimeError("simulated processing failure")
        bot = FakeBot()
        request = SimpleNamespace(
            headers={"X-Telegram-Bot-Api-Secret-Token": "0123456789abcdef"},
            content_length=100,
            get_json=lambda silent=True: {"update_id": 77},
        )
        scope = dict(bot=bot, request=request, WEBHOOK_SECRET="0123456789abcdef",
                     _webhook_slots=threading.BoundedSemaphore(40),
                     _webhook_lock=threading.Lock(),
                     _processing_updates=set(), _processed_updates=deque(maxlen=1000),
                     _processed_updates_set=set(), json=json,
                     hmac=__import__("hmac"), logger=logging.getLogger("runtime-tests"),
                     telebot=SimpleNamespace(types=SimpleNamespace(Update=SimpleNamespace(de_json=json.loads))))
        return bot, request, scope, extract(ast.FunctionDef, "webhook", scope)

    def test_webhook_acknowledges_only_after_success(self):
        bot, request, scope, webhook = self._webhook_fixture()
        bot.fail = True
        self.assertEqual(webhook()[1], 503)
        self.assertNotIn(77, scope["_processed_updates_set"])
        bot.fail = False
        self.assertEqual(webhook(), ("ok", 200))
        self.assertEqual(webhook(), ("ok", 200))
        self.assertEqual(bot.calls, 2)

    def test_inflight_update_returns_retryable_error(self):
        bot, request, scope, webhook = self._webhook_fixture()
        scope["_processing_updates"].add(77)
        self.assertEqual(webhook()[1], 503)
        self.assertEqual(bot.calls, 0)

    def test_rejects_bad_auth_and_boolean_update_id(self):
        bot, request, scope, webhook = self._webhook_fixture()
        request.headers = {}
        self.assertEqual(webhook()[1], 403)
        request.headers = {"X-Telegram-Bot-Api-Secret-Token": "0123456789abcdef"}
        request.get_json = lambda silent=True: {"update_id": True}
        self.assertEqual(webhook()[1], 400)

if __name__ == "__main__":
    unittest.main()

"""Fast offline probes for MSK bot liveness and webhook readiness.

Validates actual route bodies without importing live Telegram credentials,
loading financial providers, or making network calls.
"""
import ast
from pathlib import Path
import types
import unittest

MAIN = Path(__file__).resolve().parents[1] / "main.py"


def load_health_endpoints(*, shutdown=False, url="https://bot.example", secret="mock-secret"):
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    names = {"health_live", "health_ready"}
    funcs = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            funcs.append(node)
    if {node.name for node in funcs} != names:
        raise AssertionError("missing liveness/readiness endpoints")
    ns = {
        "jsonify": lambda payload: payload,
        "time": types.SimpleNamespace(monotonic=lambda: 180.0),
        "_PROCESS_START_MONOTONIC": 60.0,
        "executor": types.SimpleNamespace(_shutdown=shutdown),
        "WEBHOOK_URL": url,
        "WEBHOOK_SECRET": secret,
    }
    exec(compile(ast.Module(body=funcs, type_ignores=[]), str(MAIN), "exec"), ns)
    return ns


class HealthEndpointTests(unittest.TestCase):
    def test_liveness_reports_only_current_process_age(self):
        payload, status = load_health_endpoints()["health_live"]()
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "alive")
        self.assertEqual(payload["process_uptime_seconds"], 120)
        self.assertEqual(payload["scope"], "this_process_only")
        self.assertNotIn("telegram_delivery", payload)

    def test_readiness_does_not_claim_telegram_delivery(self):
        payload, status = load_health_endpoints()["health_ready"]()
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["telegram_delivery"], "not_verified")
        self.assertEqual(payload["scope"], "local_webhook_intake_only")
        self.assertNotIn("secret", str(payload).lower())

    def test_shutdown_executor_fails_closed(self):
        payload, status = load_health_endpoints(shutdown=True)["health_ready"]()
        self.assertEqual(status, 503)
        self.assertEqual(payload["status"], "degraded")

    def test_missing_webhook_secret_fails_closed(self):
        payload, status = load_health_endpoints(secret="")["health_ready"]()
        self.assertEqual(status, 503)
        self.assertEqual(payload["status"], "degraded")

    def test_polling_mode_is_not_rejected_for_missing_webhook_secret(self):
        payload, status = load_health_endpoints(url="", secret="")["health_ready"]()
        self.assertEqual(status, 200)
        self.assertEqual(payload["telegram_delivery"], "not_verified")


if __name__ == "__main__":
    unittest.main()

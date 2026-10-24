import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "scripts" / "msk_pick_task.py"
spec = importlib.util.spec_from_file_location("msk_pick_task", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

APPROVED = [{"number": 17, "labels": [{"name": "msk-codex-approved"}, {"name": "msk-codex-ready"}]}]
CLOCK = datetime(2026, 10, 26, 0, 2, tzinfo=timezone.utc)
ENV = {"ENABLED": "true", "ACK_COSTS": "true", "API_KEY_SET": "true", "EVENT": "schedule"}


class SelectorTests(unittest.TestCase):
    def test_off_by_default(self):
        self.assertIsNone(module.choose({**ENV, "ENABLED": ""}, CLOCK, APPROVED, []))

    def test_requires_explicit_cost_approval(self):
        self.assertIsNone(module.choose({**ENV, "ACK_COSTS": "false"}, CLOCK, APPROVED, []))

    def test_requires_secret(self):
        self.assertIsNone(module.choose({**ENV, "API_KEY_SET": "false"}, CLOCK, APPROVED, []))

    def test_only_eligible_time(self):
        self.assertIsNone(module.choose(ENV, datetime(2026, 10, 26, 1, 0, tzinfo=timezone.utc), APPROVED, []))

    def test_requires_both_labels(self):
        self.assertIsNone(module.choose(ENV, CLOCK, [{"number": 1, "labels": [{"name": "msk-codex-ready"}]}], []))

    def test_skip_existing_pr(self):
        self.assertIsNone(module.choose(ENV, CLOCK, APPROVED, [{"title": "Codex: Issue #17 implementation"}]))

    def test_approved_issue(self):
        self.assertEqual(module.choose(ENV, CLOCK, APPROVED, []), 17)

    def test_blocked_issue(self):
        blocked = [{"number": 17, "labels": APPROVED[0]["labels"] + [{"name": "msk-codex-blocked"}]}]
        self.assertIsNone(module.choose(ENV, CLOCK, blocked, []))


if __name__ == "__main__":
    unittest.main()

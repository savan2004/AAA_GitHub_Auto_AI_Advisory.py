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

    def test_existing_draft_pr_with_different_title_blocks_repeat(self):
        # Real-world case: Issue #11 is already represented by a draft PR.
        issues = [{"number": 11, "labels": APPROVED[0]["labels"]}]
        prs = [{"title": "test: Issue #11 deterministic task-selection regression",
                "body": "Test-only proposal for #11.", "state": "OPEN"}]
        self.assertIsNone(module.choose(ENV, CLOCK, issues, prs))

    def test_covered_issue_is_skipped_and_next_eligible_selected(self):
        issues = [
            {"number": 20, "labels": APPROVED[0]["labels"]},
            {"number": 11, "labels": APPROVED[0]["labels"]},
            {"number": 4, "labels": APPROVED[0]["labels"] + [{"name": "msk-codex-blocked"}]},
        ]
        prs = [{"title": "Draft: fix selection", "body": "Fixes #11", "state": "OPEN"}]
        self.assertEqual(module.choose(ENV, CLOCK, issues, prs), 20)

    def test_reference_to_similar_issue_number_does_not_block(self):
        issues = [{"number": 11, "labels": APPROVED[0]["labels"]}]
        prs = [{"title": "Issue #110 implementation", "body": "Fixes #110", "state": "OPEN"}]
        self.assertEqual(module.choose(ENV, CLOCK, issues, prs), 11)

    def test_closed_pr_does_not_block_reopened_issue(self):
        prs = [{"title": "Issue #17 implementation", "body": "", "state": "CLOSED"}]
        self.assertEqual(module.choose(ENV, CLOCK, APPROVED, prs), 17)

    def test_approved_issue(self):
        self.assertEqual(module.choose(ENV, CLOCK, APPROVED, []), 17)

    def test_blocked_issue(self):
        blocked = [{"number": 17, "labels": APPROVED[0]["labels"] + [{"name": "msk-codex-blocked"}]}]
        self.assertIsNone(module.choose(ENV, CLOCK, blocked, []))


    def test_selects_lowest_numbered_eligible_issue_skipping_blocked_and_covered(self):
        labels = APPROVED[0]["labels"]
        # Deliberately unsorted: selection must be by issue number, not API order.
        issues = [
            {"number": 25, "labels": labels},
            {"number": 3, "labels": labels + [{"name": "msk-codex-blocked"}]},
            {"number": 13, "labels": labels},
            {"number": 5, "labels": labels},
            {"number": 1, "labels": [{"name": "msk-codex-ready"}]},
        ]
        existing_prs = [{"title": "Codex: Issue #5 implementation"}]
        self.assertEqual(module.choose(ENV, CLOCK, issues, existing_prs), 13)

if __name__ == "__main__":
    unittest.main()

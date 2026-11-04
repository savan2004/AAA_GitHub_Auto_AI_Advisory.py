import unittest
from tools.msk_control_panel import render

class PanelTests(unittest.TestCase):
    def test_escapes_untrusted_log_content(self):
        page = render({"total_events": 1, "incidents": [
            {"id": "X", "priority": "P1", "category": "UNKNOWN", "count": 1,
             "action": "review", "sample": '<script>alert("x")</script>'}]})
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("READ ONLY", page)

    def test_empty_ledger(self):
        page = render({"total_events": 0, "incidents": []})
        self.assertIn("Events: 0", page)

if __name__ == "__main__":
    unittest.main()

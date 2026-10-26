import datetime as dt
import io
import json
import unittest
from unittest.mock import Mock
from channel_publisher import publish_once

class PublisherTests(unittest.TestCase):
    def test_disabled_by_default(self):
        self.assertEqual(publish_once({}), {"status": "disabled"})

    def test_missing_config(self):
        with self.assertRaises(ValueError):
            publish_once({"CHANNEL_PUBLISH_ENABLED": "true"})

    def test_confirmed_delivery(self):
        response = io.BytesIO(json.dumps({"ok": True, "result": {"message_id": 7}}).encode())
        response.__enter__ = lambda: response
        response.__exit__ = lambda *args: None
        opener = Mock(return_value=response)
        result = publish_once({"CHANNEL_PUBLISH_ENABLED": "true", "TELEGRAM_TOKEN": "test",
            "TELEGRAM_CHANNEL_ID": "@test", "CHANNEL_REPORT_TEXT": "Verified report"}, opener=opener,
            now=dt.datetime(2026, 10, 26, tzinfo=dt.timezone.utc))
        self.assertEqual(result["message_id"], 7)
        self.assertEqual(opener.call_count, 1)

    def test_refuses_empty_report(self):
        with self.assertRaises(ValueError):
            publish_once({"CHANNEL_PUBLISH_ENABLED": "true", "TELEGRAM_TOKEN": "test",
                "TELEGRAM_CHANNEL_ID": "@test"})

    def test_rejected_delivery(self):
        response = io.BytesIO(b'{"ok": false}')
        with self.assertRaises(RuntimeError):
            publish_once({"CHANNEL_PUBLISH_ENABLED": "true", "TELEGRAM_TOKEN": "test",
                "TELEGRAM_CHANNEL_ID": "@test"}, opener=lambda *a, **kw: response)

if __name__ == "__main__":
    unittest.main()

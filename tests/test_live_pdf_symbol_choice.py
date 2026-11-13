"""Only public allowlisted symbols may run owner-only real Report acceptance."""
import unittest
from unittest.mock import patch

from msk_live_acceptance import _owner_report_symbol


class DlfRealPdfOwnerAcceptanceTests(unittest.TestCase):
    def test_default_preserves_existing_sbin_behavior(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_owner_report_symbol(), "SBIN")

    def test_dlf_is_allowed_in_owner_report_only(self):
        with patch.dict("os.environ", {"MSK_LIVE_ACCEPTANCE_REPORT_SYMBOL": "dlf"}):
            self.assertEqual(_owner_report_symbol(), "DLF")

    def test_unapproved_symbol_is_refused(self):
        for symbol in ["", "HACK", "../../secrets", "CONCOR", "SBIN,DLF"]:
            with self.subTest(symbol=symbol):
                with patch.dict("os.environ", {"MSK_LIVE_ACCEPTANCE_REPORT_SYMBOL": symbol}):
                    with self.assertRaisesRegex(ValueError, "must be SBIN or DLF"):
                        _owner_report_symbol()


if __name__ == "__main__":
    unittest.main()

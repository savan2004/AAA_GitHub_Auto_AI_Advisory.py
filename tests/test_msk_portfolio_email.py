import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from msk_portfolio_email import send_portfolio_pdf, MailConfigurationError

class FakeSMTP:
    def __init__(self, *args, **kwargs): self.messages=[]
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def login(self, user, password): assert user == "bot@example.com"
    def send_message(self, message):
        assert message["To"] == "investor@example.com"
        assert len(list(message.iter_attachments())) == 1

class EmailTransportTests(unittest.TestCase):
    def test_pdf_sent_using_env_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.pdf"
            path.write_bytes(b"%PDF-1.4\\nmock")
            with patch.dict(os.environ, {"MSK_SMTP_USER":"bot@example.com", "MSK_SMTP_APP_PASSWORD":"dummy"}):
                self.assertTrue(send_portfolio_pdf("investor@example.com",path,"November 2026",smtp_factory=FakeSMTP))
    def test_missing_credentials_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"report.pdf"
            path.write_bytes(b"%PDF-1.4\\nmock")
            with patch.dict(os.environ, {"MSK_SMTP_USER":"","MSK_SMTP_APP_PASSWORD":""}):
                with self.assertRaises(MailConfigurationError):
                    send_portfolio_pdf("investor@example.com",path,"November 2026",smtp_factory=FakeSMTP)

if __name__ == "__main__": unittest.main()

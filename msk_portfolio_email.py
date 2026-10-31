"""MSK Portfolio PDF email transport (opt-in, free SMTP, no secrets in code).

Integration contract: call send_portfolio_pdf only after verified email consent,
validated report generation and a durable idempotency claim. The caller owns
monthly scheduling, retry ledger and recipient verification.
"""
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path


class MailConfigurationError(RuntimeError):
    pass


def send_portfolio_pdf(recipient, pdf_path, report_month, *, smtp_factory=None):
    """Send one existing PDF; return SMTP acceptance, not inbox delivery proof."""
    sender = os.environ.get("MSK_SMTP_USER", "").strip()
    password = os.environ.get("MSK_SMTP_APP_PASSWORD", "")
    host = os.environ.get("MSK_SMTP_HOST", "smtp.gmail.com").strip()
    port = int(os.environ.get("MSK_SMTP_PORT", "465"))
    if not sender or not password:
        raise MailConfigurationError("SMTP credentials are not configured")
    if not recipient or "\n" in recipient or "\r" in recipient or "@" not in recipient:
        raise ValueError("A verified recipient email is required")
    if not report_month or any(c in report_month for c in "\r\n"):
        raise ValueError("Invalid report month")
    path = Path(pdf_path)
    if path.suffix.lower() != ".pdf" or not path.is_file():
        raise FileNotFoundError("A generated PDF file is required")
    pdf = path.read_bytes()
    if not pdf.startswith(b"%PDF-"):
        raise ValueError("Invalid PDF attachment")
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient.strip()
    msg["Subject"] = f"MSK Portfolio Health Report — {report_month}"
    msg.set_content("Your requested MSK portfolio health report is attached. Educational research only; not personalized investment advice. To stop monthly emails, use the unsubscribe option in the bot.")
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename="MSK_Portfolio_Health.pdf")
    factory = smtp_factory or smtplib.SMTP_SSL
    with factory(host, port, context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)
    return True

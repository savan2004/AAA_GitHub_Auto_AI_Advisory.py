"""Private Telegram transport for separate fictional preview and verified Builder."""
import io
import os

from msk_ai_builder_profile import ResearchProfile


def preview_allowed(uid, environ=None):
    env = os.environ if environ is None else environ
    return (type(uid) is int and uid > 0
            and env.get("MSK_BUILDER_V2_PREVIEW_ENABLED") == "1"
            and env.get("MSK_BUILDER_V2_PREVIEW_USER_ID") == str(uid))


def deliver_preview(bot, uid, capital, risk, horizon):
    if not preview_allowed(uid):
        raise PermissionError("Builder V2 preview is disabled for this user")
    from msk_ai_builder_beta import beta_pdf

    profile = ResearchProfile(capital, risk, horizon)
    with io.BytesIO(beta_pdf(profile)) as document:
        document.name = "MSK_Builder_V2_Fictional_Preview.pdf"
        receipt = bot.send_document(
            uid, document, protect_content=True, timeout=30,
            caption="TEST MODE — fictional holdings. No real stocks or investment recommendations.",
        )
    message_id = getattr(receipt, "message_id", None)
    if (type(message_id) is not int or message_id <= 0
            or not getattr(getattr(receipt, "document", None), "file_id", None)):
        raise RuntimeError("Builder V2 preview delivery was not confirmed")
    return receipt



def deliver_automatic_pdf(bot, uid, capital, risk, months):
    """Exactly one protected send per explicitly submitted customer request.

    Caller must atomically consume the in-memory wizard before invoking this.
    An uncertain provider response is NOT automatically retried, preventing
    duplicate Telegram PDFs. The real research engine never uses demo fixtures.
    """
    if type(uid) is not int or uid <= 0:
        raise PermissionError("private chat required")
    from msk_ai_builder_automatic import build_automatic_pdf

    pdf = build_automatic_pdf(capital, risk, months)
    with io.BytesIO(pdf) as document:
        document.name = "MSK_Automatic_Portfolio_Research.pdf"
        receipt = bot.send_document(
            uid, document, protect_content=True, timeout=30,
            caption=("Dated NSE research model. Capital and share counts use "
                     "completed closing prices, not executable orders. "
                     "Risk preference is not a suitability assessment."),
        )
    message_id = getattr(receipt, "message_id", None)
    file_id = getattr(getattr(receipt, "document", None), "file_id", None)
    if type(message_id) is not int or message_id <= 0 or not file_id:
        raise RuntimeError("AUTOMATIC_PDF_RECEIPT_UNCONFIRMED")
    return receipt

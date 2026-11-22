"""Owner-only Telegram transport for the clearly labeled fictional V2 preview."""
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


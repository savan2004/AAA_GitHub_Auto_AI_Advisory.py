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
    from msk_ai_builder_demo_pdf import demo_pdf

    profile = ResearchProfile(capital, risk, horizon)
    with io.BytesIO(demo_pdf(profile)) as document:
        document.name = "MSK_Builder_V2_Fictional_Preview.pdf"
        receipt = bot.send_document(
            uid, document, protect_content=True, timeout=30,
            caption="Fictional Portfolio Builder V2 preview. No real stocks or investment recommendations.",
        )
    message_id = getattr(receipt, "message_id", None)
    if (type(message_id) is not int or message_id <= 0
            or not getattr(getattr(receipt, "document", None), "file_id", None)):
        raise RuntimeError("Builder V2 preview delivery was not confirmed")
    return receipt

def infographic_enabled(environ=None):
    env = os.environ if environ is None else environ
    return env.get("MSK_BUILDER_V2_INFOGRAPHIC_ENABLED") == "1"


def deliver_allocation(bot, uid, capital, risk, horizon):
    """Send one private allocation document, with no automatic retry."""
    from msk_ai_builder_automatic import load_approved_model, ResearchUnavailable
    from msk_ai_builder_infographic import allocation_pdf

    if type(uid) is not int or uid <= 0 or not infographic_enabled():
        raise ResearchUnavailable("Infographic delivery disabled")
    plan, artifact, optimized = load_approved_model(capital, risk, horizon)
    raw = allocation_pdf(plan, artifact, optimized=optimized)
    with io.BytesIO(raw) as document:
        document.name = "MSK_Portfolio_Builder_V2.pdf"
        receipt = bot.send_document(uid, document, protect_content=True, timeout=30,
                                    caption="MSK research model | Dated allocation and source references.")
    message_id = getattr(receipt, "message_id", None)
    if (type(message_id) is not int or message_id <= 0
            or not getattr(getattr(receipt, "document", None), "file_id", None)):
        raise RuntimeError("PDF delivery was not confirmed")
    return receipt

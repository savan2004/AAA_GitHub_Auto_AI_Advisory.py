"""Allow MSK group replies only for messages from real, non-anonymous members."""


def is_member_message(message):
    chat = getattr(message, "chat", None)
    if getattr(chat, "type", None) not in ("group", "supergroup"):
        return False
    sender = getattr(message, "from_user", None)
    if sender is None or getattr(sender, "is_bot", False):
        return False
    if getattr(message, "sender_chat", None) is not None:
        # Channel discussion forwards and anonymous admins carry sender_chat.
        return False
    if getattr(message, "is_automatic_forward", False):
        return False
    if getattr(message, "forward_from_chat", None) is not None:
        return False
    origin = getattr(message, "forward_origin", None)
    if origin is not None and getattr(origin, "type", None) in ("channel", "chat"):
        return False
    return True

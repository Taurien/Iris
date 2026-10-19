import os


def is_allowed(user_id: int) -> bool:
    allowed = os.environ.get("ALLOWED_TELEGRAM_USER_ID", "").strip()
    if not allowed:
        return False
    return str(user_id) == allowed

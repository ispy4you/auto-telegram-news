from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.prompt_settings import _get_setting


async def notify_operator(db: Session, text: str) -> bool:
    """Send a message to the operator's Telegram chat. Returns True on success."""
    settings = get_settings()
    if not settings.telegram_bot_token:
        return False
    chat_id = _get_setting(db, "operator_chat_id", "")
    if not chat_id.strip():
        return False
    from aiogram import Bot
    bot = Bot(settings.telegram_bot_token)
    try:
        await bot.send_message(chat_id=chat_id.strip(), text=text, parse_mode="HTML")
        return True
    except Exception:
        return False
    finally:
        await bot.session.close()

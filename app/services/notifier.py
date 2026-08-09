import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.prompt_settings import _get_setting

logger = logging.getLogger(__name__)


async def notify_operator(db: Session, text: str) -> bool:
    """Send a message to the operator's Telegram chat. Returns True on success."""
    settings = get_settings()
    bot_token = _get_setting(db, "telegram_bot_token", settings.telegram_bot_token or "")
    if not bot_token:
        return False
    chat_id = _get_setting(db, "operator_chat_id", "")
    if not chat_id.strip():
        return False
    from aiogram import Bot
    bot = Bot(bot_token)
    try:
        await bot.send_message(chat_id=chat_id.strip(), text=text, parse_mode="HTML")
        return True
    except Exception:
        logger.warning("Failed to notify operator", exc_info=True)
        return False
    finally:
        await bot.session.close()

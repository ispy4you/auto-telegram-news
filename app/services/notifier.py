import logging

from sqlalchemy.orm import Session

from app.services import settings_registry

logger = logging.getLogger(__name__)


async def notify_operator(db: Session, text: str) -> bool:
    """Send a message to the operator's Telegram chat. Returns True on success."""
    bot_token = settings_registry.get("telegram_bot_token", db)
    if not bot_token:
        return False
    chat_id = settings_registry.get("operator_chat_id", db)
    if not chat_id:
        return False
    from aiogram import Bot
    bot = Bot(bot_token)
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        return True
    except Exception:
        logger.warning("Failed to notify operator", exc_info=True)
        return False
    finally:
        await bot.session.close()

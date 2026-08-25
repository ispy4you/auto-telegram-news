import asyncio
import base64
import io
import logging

import qrcode
from telethon import TelegramClient
from telethon.errors import PhoneCodeExpiredError, PhoneCodeInvalidError, SessionPasswordNeededError

from app.config import get_settings
from app.services import telegram_session_store
from app.services.telegram_reader import _TELETHON_LOCK, TelegramReaderService

logger = logging.getLogger(__name__)


def _qr_to_data_uri(url: str) -> str:
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


class TelegramLoginService:
    """Drives an interactive Telethon login (QR or phone+code) from the web UI —
    the only way to create a user session.

    Only one login attempt runs at a time. While it's in progress, the background
    event listener is stopped and the shared Telethon lock (also used by the polling
    fetch path) is held, so nothing else touches the session file concurrently.
    """

    def __init__(self, event_listener, db_factory):
        self._event_listener = event_listener
        self._db_factory = db_factory
        self._reader = TelegramReaderService()
        self._client: TelegramClient | None = None
        self._qr_login = None
        self._qr_data_uri: str | None = None
        self._qr_task: asyncio.Task | None = None
        self._phone: str | None = None
        self._phone_code_hash: str | None = None
        self._state = "idle"  # idle | qr_pending | code_sent | password_needed | done | error
        self._error: str | None = None
        self._me: dict | None = None
        self._lock_acquired = False

    def status(self) -> dict:
        data = {
            "state": self._state,
            "error": self._error,
            "link": self._link_state(),
            "account": self._me or telegram_session_store.load_account(),
            "listener_active": self._event_listener.is_active,
            "listener_started": self._event_listener.is_started,
        }
        if self._state == "qr_pending":
            data["qr_data_uri"] = self._qr_data_uri
        return data

    def _link_state(self) -> str:
        """Одно понятное состояние вместо трёх флагов, которые надо совмещать в уме.

        Раньше карточка показывала «Переподключение…» и когда связь моргнула,
        и когда сессии просто нет, — то есть молчала именно тогда, когда от
        пользователя требовалось действие.
        """
        if self._state in ("qr_pending", "code_sent", "password_needed"):
            return "logging_in"
        if telegram_session_store.load_string() is None:
            return "none"
        if self._event_listener.is_active:
            return "ok"
        if self._event_listener.needs_login:
            return "revoked"
        return "connecting"

    async def logout(self) -> dict:
        """Отключить аккаунт, не начиная новый вход."""
        telegram_session_store.clear()
        telegram_session_store.clear_account()
        self._me = None
        self._state = "idle"
        self._error = None
        await self._reset(resume_listener=True)
        return self.status()

    async def _ensure_client(self) -> TelegramClient:
        settings = get_settings()
        if not settings.telegram_api_id or not settings.telegram_api_hash:
            raise RuntimeError("TELEGRAM_API_ID и TELEGRAM_API_HASH не заданы в .env")
        if self._client is None:
            if not self._lock_acquired:
                await _TELETHON_LOCK.acquire()
                self._lock_acquired = True
            await self._event_listener.stop()
            self._client = self._reader._client()
            await self._client.connect()
        return self._client

    async def _reset(self, resume_listener: bool) -> None:
        if self._qr_task and not self._qr_task.done():
            self._qr_task.cancel()
        if self._client:
            try:
                if self._client.is_connected():
                    await self._client.disconnect()
            except Exception:
                pass
        self._client = None
        self._qr_login = None
        self._qr_data_uri = None
        self._qr_task = None
        self._phone = None
        self._phone_code_hash = None
        if self._lock_acquired:
            _TELETHON_LOCK.release()
            self._lock_acquired = False
        if resume_listener:
            await self._event_listener.start(self._db_factory)

    async def cancel(self) -> dict:
        self._state = "idle"
        self._error = None
        self._me = None
        await self._reset(resume_listener=True)
        return self.status()

    async def start_qr(self) -> dict:
        if self._state == "qr_pending":
            return self.status()
        if self._state in ("code_sent", "password_needed"):
            raise RuntimeError("Уже выполняется вход по телефону. Сначала отмените его.")
        try:
            client = await self._ensure_client()
            if await client.is_user_authorized():
                await self._finish(client)
                return self.status()
            self._qr_login = await client.qr_login()
            self._qr_data_uri = _qr_to_data_uri(self._qr_login.url)
            self._state = "qr_pending"
            self._error = None
            self._qr_task = asyncio.create_task(self._watch_qr())
        except Exception as exc:
            logger.warning("Telegram QR login failed to start: %s", exc)
            self._state = "error"
            self._error = str(exc)
            await self._reset(resume_listener=True)
        return self.status()

    async def _watch_qr(self) -> None:
        try:
            while self._state == "qr_pending":
                try:
                    await self._qr_login.wait(30)
                except SessionPasswordNeededError:
                    self._state = "password_needed"
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await self._qr_login.recreate()
                    self._qr_data_uri = _qr_to_data_uri(self._qr_login.url)
                    continue
                else:
                    await self._finish(self._client)
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Telegram QR login failed: %s", exc)
            self._state = "error"
            self._error = str(exc)
            await self._reset(resume_listener=True)

    async def start_phone(self, phone: str) -> dict:
        if self._state in ("qr_pending", "password_needed"):
            raise RuntimeError("Уже выполняется вход другим способом. Сначала отмените его.")
        phone = phone.strip()
        if not phone.startswith("+"):
            phone = f"+{phone}"
        try:
            client = await self._ensure_client()
            if await client.is_user_authorized():
                await self._finish(client)
                return self.status()
            sent = await client.send_code_request(phone)
            self._phone = phone
            self._phone_code_hash = sent.phone_code_hash
            self._state = "code_sent"
            self._error = None
        except Exception as exc:
            logger.warning("Telegram phone login failed to start: %s", exc)
            self._state = "error"
            self._error = str(exc)
            await self._reset(resume_listener=True)
        return self.status()

    async def submit_code(self, code: str) -> dict:
        if self._state != "code_sent" or not self._client:
            raise RuntimeError("Нет активного запроса кода.")
        try:
            await self._client.sign_in(
                phone=self._phone, code=code.strip(), phone_code_hash=self._phone_code_hash,
            )
            await self._finish(self._client)
        except SessionPasswordNeededError:
            self._state = "password_needed"
            self._error = None
        except PhoneCodeInvalidError:
            self._error = "Неверный код, попробуйте ещё раз."
        except PhoneCodeExpiredError:
            sent = await self._client.send_code_request(self._phone)
            self._phone_code_hash = sent.phone_code_hash
            self._error = "Код истёк, отправлен новый."
        except Exception as exc:
            logger.warning("Telegram code sign-in failed: %s", exc)
            self._state = "error"
            self._error = str(exc)
            await self._reset(resume_listener=True)
        return self.status()

    async def submit_password(self, password: str) -> dict:
        if self._state != "password_needed" or not self._client:
            raise RuntimeError("Пароль сейчас не запрашивается.")
        try:
            await self._client.sign_in(password=password)
            await self._finish(self._client)
        except Exception as exc:
            logger.warning("Telegram 2FA sign-in failed: %s", exc)
            self._error = "Неверный пароль, попробуйте ещё раз."
        return self.status()

    async def _finish(self, client: TelegramClient) -> None:
        me = await client.get_me()
        if getattr(me, "bot", False):
            telegram_session_store.clear()
            telegram_session_store.clear_account()
            await self._reset(resume_listener=True)
            self._state = "error"
            self._error = (
                "Это оказался bot-токен, а не пользовательский аккаунт. "
                "Попробуйте снова и войдите как пользователь."
            )
            self._me = None
            return
        # Сессию сохраняем до _reset: он рвёт соединение с Telegram.
        telegram_session_store.save_from_client(client)
        telegram_session_store.save_account(me)
        self._me = telegram_session_store.load_account()
        self._state = "done"
        self._error = None
        await self._reset(resume_listener=True)

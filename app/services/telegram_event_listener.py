import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import ActionLog, SourceChannel

logger = logging.getLogger(__name__)

_RECONNECT_DELAY = 30  # секунд между попытками переподключения
_SOURCE_RELOAD_INTERVAL = 300  # секунд между обновлением списка каналов
# Как долго одно и то же состояние считается уже записанным в журнал.
_NOTE_QUIET_PERIOD = timedelta(hours=1)


_ACTIVE_LISTENER = None


def active_client():
    """Клиент работающего слушателя, если он есть."""
    return _ACTIVE_LISTENER.client if _ACTIVE_LISTENER is not None else None


class TelegramEventListenerService:
    """Держит постоянное Telethon-соединение и сохраняет новые посты в реальном времени.

    Когда сервис активен (`is_active == True`), планировщик пропускает шаг
    fetch_new_posts — event listener берёт его на себя.
    """

    def __init__(self):
        global _ACTIVE_LISTENER
        _ACTIVE_LISTENER = self
        self._client = None
        self._task: asyncio.Task | None = None
        self._reload_task: asyncio.Task | None = None
        self._active = False
        self._needs_login = False
        self._started = False  # True с момента start() — не сбрасывается при переподключении
        self._db_factory = None
        self._source_usernames: set[str] = set()  # lowercase usernames
        # Буфер для альбомов (grouped_id -> list of (username, msg))
        self._album_buffer: dict[int, list] = defaultdict(list)
        self._album_tasks: dict[int, asyncio.Task] = {}
        self._noted_at: dict[str, datetime] = {}  # сообщение -> когда записали

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def needs_login(self) -> bool:
        """True, когда сессия есть, но Telegram её не принимает.

        Отличает «ещё переподключаемся» от «нужен вход»: снаружи это выглядит
        одинаково — слушатель неактивен, — а делать надо разное.
        """
        return self._needs_login

    @property
    def client(self):
        """Живой Telethon-клиент слушателя или None.

        Нужен всем, кому иначе пришлось бы поднимать второй клиент на той же
        сессии: проект этого избегает намеренно — см. _TELETHON_LOCK, который
        слушатель держит всё время, пока подключён.
        """
        if self._client is not None and self._client.is_connected():
            return self._client
        return None

    @property
    def is_started(self) -> bool:
        """True когда listener запущен (включая паузы переподключения).

        Планировщик на это больше не смотрит: флаг поднимается при запуске и не
        опускается никогда, и опрос из-за него вставал насовсем. Осталось только
        для карточки входа в админке — там это честный признак «служба живёт».
        """
        return self._started

    async def start(self, db_factory) -> None:
        if self._task is not None and not self._task.done():
            # Повторный start() без stop() — например, два подряд выхода из
            # аккаунта в админке. Вторая петля подняла бы второй Telethon-клиент
            # на той же сессии, а с общим локом просто повисла бы навсегда,
            # дожидаясь своей очереди, и всплыла бы при следующей остановке.
            logger.warning("Event listener уже запущен — перезапускаем")
            await self.stop()
        self._db_factory = db_factory
        self._started = True
        self._task = asyncio.create_task(self._run_loop(), name="tg-event-listener")
        logger.info("TelegramEventListenerService запущен")

    async def stop(self) -> None:
        self._started = False
        self._active = False
        for t in [self._task, self._reload_task]:
            if t and not t.done():
                t.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(t), timeout=5)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        if self._client and self._client.is_connected():
            await self._client.disconnect()
        logger.info("TelegramEventListenerService остановлен")

    async def reload_sources(self) -> None:
        """Обновить список отслеживаемых каналов из БД (вызывается при добавлении/удалении источника)."""
        if not self._db_factory:
            return
        with self._db_factory() as db:
            sources = db.scalars(
                select(SourceChannel).where(
                    SourceChannel.enabled.is_(True),
                    SourceChannel.source_type == "telethon",
                )
            ).all()
            self._source_usernames = {s.username.lower() for s in sources if s.username}
        logger.debug("Event listener: %d каналов в мониторинге", len(self._source_usernames))

    # ------------------------------------------------------------------
    # Внутренние методы
    # ------------------------------------------------------------------

    def _note(self, action: str, message: str) -> None:
        """Записать смену состояния в журнал панели.

        Раньше слушатель сообщал о себе только в stdout контейнера, где эти
        строки тонут в логе health-чеков. Оператор видел «постов нет» и не мог
        отличить тишину в каналах от мёртвого соединения.

        Одно и то же состояние пишем не чаще раза в час. Проверять «отличается ли
        от предыдущего» недостаточно: соединение может мигать, и тогда «подключён»
        и «отключён» чередуются — каждое отличается от предыдущего, и за сутки
        получилось бы больше пяти тысяч строк.
        """
        if not self._db_factory:
            return
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        written_at = self._noted_at.get(message)
        if written_at is not None and now - written_at < _NOTE_QUIET_PERIOD:
            return
        try:
            with self._db_factory() as db:
                db.add(ActionLog(
                    action=action,
                    entity_type="EventListener",
                    entity_id="telethon",
                    message=message[:500],
                ))
                db.commit()
        except Exception:
            # Не помечаем состояние записанным: иначе одна неудачная вставка
            # навсегда спрятала бы эту строку из журнала.
            logger.warning("Не удалось записать состояние слушателя в журнал", exc_info=True)
            return
        if len(self._noted_at) > 50:
            # Текст ошибки попадает в ключ, так что словарь в теории может расти.
            self._noted_at.clear()
        self._noted_at[message] = now

    async def _run_loop(self) -> None:
        from app.services.telegram_reader import TelegramReaderService
        self._reader = TelegramReaderService()

        while True:
            try:
                await self._connect_and_listen()
                # run_until_disconnected() возвращается штатно, когда Telegram
                # закрыл соединение. Без паузы здесь цикл переподключался бы
                # вплотную и молча — обрыв выглядел бы как нормальная работа.
                self._note(
                    "listener_disconnected",
                    "Соединение с Telegram закрыто, переподключаемся.",
                )
                logger.warning("Event listener: соединение закрыто — переподключение через %ds", _RECONNECT_DELAY)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._active = False
                logger.warning(
                    "Event listener отключился: %s — переподключение через %ds",
                    exc, _RECONNECT_DELAY,
                )
                self._note(
                    "listener_needs_login" if self._needs_login else "listener_error",
                    f"Слушатель Telegram отключён: {exc}",
                )
            try:
                await asyncio.sleep(_RECONNECT_DELAY)
            except asyncio.CancelledError:
                raise

    async def _connect_and_listen(self) -> None:
        """Держит лок всё время, пока клиент подключён.

        Опрос по таймеру берёт тот же лок, поэтому два клиента на одной сессии
        не встретятся — и при этом опрос больше не выключен навсегда одним лишь
        фактом того, что слушатель когда-то запускался.
        """
        from app.services.telegram_reader import _TELETHON_LOCK

        async with _TELETHON_LOCK:
            await self._connect_and_listen_locked()

    async def _connect_and_listen_locked(self) -> None:
        from telethon import events

        from app.services import telegram_session_store

        client = self._reader._client()
        self._client = client
        await client.connect()
        if not await client.is_user_authorized():
            self._needs_login = True
            await client.disconnect()
            raise RuntimeError(
                "Telethon-сессия не авторизована. "
                "Войдите в Telegram в админке."
            )
        # Сбрасываем сразу: иначе после успешного входа флаг остался бы висеть,
        # и первый же обрыв связи выглядел бы как «сессию отозвали».
        self._needs_login = False

        me = await client.get_me()
        if getattr(me, "bot", False):
            await client.disconnect()
            raise RuntimeError(
                "Telethon-сессия создана на бот-токене. "
                "Нужен вход пользовательским аккаунтом — войдите заново в админке."
            )

        # Кто именно подключён, знает только тот, кто уже спросил Telegram.
        # Записываем здесь, иначе у сессий, созданных до появления этой карточки,
        # имя появлялось бы только после повторного входа.
        try:
            telegram_session_store.save_account(me)
        except Exception:
            logger.warning("Не удалось сохранить данные аккаунта", exc_info=True)

        # Активируем ДО catch-up — планировщик сразу переходит в режим skip_fetch
        await self.reload_sources()
        self._active = True
        logger.info("Event listener активен, слушает %d каналов", len(self._source_usernames))
        self._note(
            "listener_connected",
            f"Слушатель Telegram подключён, каналов в мониторинге: {len(self._source_usernames)}.",
        )

        await self._catchup(client)

        # Запустить периодическое обновление списка каналов
        if self._reload_task is None or self._reload_task.done():
            self._reload_task = asyncio.create_task(self._periodic_reload(), name="tg-sources-reload")

        @client.on(events.NewMessage)
        async def _on_new_message(event):
            try:
                chat = await event.get_chat()
                username = (getattr(chat, "username", None) or "").lower()
                if username not in self._source_usernames:
                    return
                msg = event.message
                if msg.grouped_id:
                    await self._buffer_album(username, msg, client)
                else:
                    await self._save_single(username, msg, client)
            except IntegrityError:
                # Тот же пост уже записал опрос (uq_source_message). Это гонка
                # двух здоровых путей сбора, а не потеря поста — тревожить
                # оператора нечем.
                logger.debug("Входящий пост уже записан опросом")
            except Exception as exc:
                # Было logger.debug — при уровне INFO эти строки не печатались
                # вовсе. Пост, который не удалось сохранить, пропадал бесследно.
                logger.warning("Ошибка при сохранении входящего поста: %s", exc, exc_info=True)
                self._note("listener_save_error", f"Не удалось сохранить входящий пост: {exc}")
        try:
            await client.run_until_disconnected()
        finally:
            self._active = False
            if client.is_connected():
                await client.disconnect()
            # Даём Telethon-задачам (keepalive и др.) время завершиться,
            # чтобы не конфликтовать с файлом сессии при следующем reconnect.
            await asyncio.sleep(3)

    async def _catchup(self, client) -> None:
        """Подтянуть пропущенные посты для всех источников используя уже подключённый клиент."""
        if not self._db_factory:
            return

        # Получаем список ID в короткой read-сессии
        with self._db_factory() as db:
            source_ids = [
                s.id for s in db.scalars(
                    select(SourceChannel).where(
                        SourceChannel.enabled.is_(True),
                        SourceChannel.source_type == "telethon",
                    )
                ).all() if s.username
            ]

        # Каждый источник в своей сессии — короткий write-лок вместо одного долгого
        for source_id in source_ids:
            with self._db_factory() as db:
                source = db.get(SourceChannel, source_id)
                if not source or not source.username:
                    continue
                try:
                    pending, last_msg_id, skipped_old = await self._reader._collect_pending(
                        client, db, source, limit=50
                    )
                    count = self._reader._flush_pending(
                        db, source, pending, last_msg_id, skipped_old
                    )
                    if count:
                        logger.info("Catch-up @%s: %d новых постов", source.username, count)
                except IntegrityError:
                    # Опрос по таймеру успел записать тот же пост. Ничего не
                    # потеряно, и оператору такое показывать незачем.
                    db.rollback()
                    logger.debug("Catch-up @%s: пост уже записан опросом", source.username)
                except Exception as exc:
                    logger.warning("Catch-up ошибка для @%s: %s", source.username, exc)
                    try:
                        db.rollback()
                        db.add(ActionLog(
                            action="event_listener_catchup_error",
                            entity_type="SourceChannel",
                            entity_id=str(source_id),
                            message=str(exc)[:400],
                        ))
                        db.commit()
                    except Exception:
                        pass

    async def _periodic_reload(self) -> None:
        while True:
            try:
                await asyncio.sleep(_SOURCE_RELOAD_INTERVAL)
                if self._active:
                    await self.reload_sources()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    async def _buffer_album(self, username: str, msg, client) -> None:
        """Буферизует сообщения альбома и сохраняет весь альбом через 1.5с после последнего."""
        gid = msg.grouped_id
        self._album_buffer[gid].append((username, msg))

        # Отменяем предыдущий таймер сброса для этого альбома
        existing = self._album_tasks.get(gid)
        if existing and not existing.done():
            existing.cancel()

        async def _flush():
            await asyncio.sleep(1.5)
            entries = self._album_buffer.pop(gid, [])
            self._album_tasks.pop(gid, None)
            if entries:
                await self._save_album(entries, client)

        self._album_tasks[gid] = asyncio.create_task(_flush())

    async def _save_single(self, username: str, msg, client) -> None:
        if not self._db_factory:
            return
        with self._db_factory() as db:
            source = self._get_source(db, username)
            if not source:
                return
            media_files = await self._reader._download_media_list(source, msg.id, [msg], client)
            post = self._reader._write_post(db, source, msg, None, media_files)
            if post:
                source.last_message_id = max(source.last_message_id or 0, msg.id)
                source.last_fetched_at = datetime.now(timezone.utc).replace(tzinfo=None)
                db.add(ActionLog(
                    action="fetch_post_telethon",
                    entity_type="RawPost",
                    entity_id=str(post.id),
                    message=f"Новый пост из @{username}",
                ))
                db.commit()
                logger.debug("Event: пост %d из @%s", msg.id, username)

    async def _save_album(self, entries: list, client) -> None:
        if not entries or not self._db_factory:
            return
        username = entries[0][0]
        msgs = sorted([m for _, m in entries], key=lambda m: m.id)
        anchor = msgs[0]

        with self._db_factory() as db:
            source = self._get_source(db, username)
            if not source:
                return
            # Проверка дубля для всего альбома
            from sqlalchemy import select as _select
            from app.models import RawPost
            if db.scalar(_select(RawPost).where(
                RawPost.source_id == source.id,
                RawPost.telegram_grouped_id == anchor.grouped_id,
            )):
                return
            media_files = await self._reader._download_media_list(source, anchor.id, msgs, client)
            post = self._reader._write_post(db, source, anchor, msgs, media_files)
            if post:
                source.last_message_id = max(source.last_message_id or 0, max(m.id for m in msgs))
                source.last_fetched_at = datetime.now(timezone.utc).replace(tzinfo=None)
                db.add(ActionLog(
                    action="fetch_post_telethon",
                    entity_type="RawPost",
                    entity_id=str(post.id),
                    message=f"Новый альбом из @{username} ({len(media_files)} медиа)",
                ))
                db.commit()
                logger.debug(
                    "Event: альбом %d из @%s (%d медиа)",
                    anchor.grouped_id, username, len(media_files),
                )

    @staticmethod
    def _get_source(db, username_lower: str):
        from sqlalchemy import func
        return db.scalar(
            select(SourceChannel).where(
                func.lower(SourceChannel.username) == username_lower,
                SourceChannel.enabled.is_(True),
                SourceChannel.source_type == "telethon",
            )
        )

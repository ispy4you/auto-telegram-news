"""Закрытый канал как источник: ссылка, заявка, чтение по id.

Заявка закрывает вступление, а не чтение: публичный канал читается без неё,
а закрытый — только после одобрения. Телеграм об одобрении не сообщает,
поэтому панель спрашивает сама.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from telethon.errors import InviteHashInvalidError, InviteRequestSentError
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import ChatInviteAlready

from app.models import SourceChannel
from app.services import telegram_invites
from app.services.telegram_invites import InviteError, parse_invite


class _FakeClient:
    """Телеграм в двух ответах: что говорит проверка ссылки и что — вступление."""

    def __init__(self, check, imported=None):
        self._check = check
        self._imported = imported
        self.calls: list[str] = []

    async def __call__(self, request):
        if isinstance(request, CheckChatInviteRequest):
            self.calls.append("check")
            if isinstance(self._check, Exception):
                raise self._check
            return self._check
        if isinstance(request, ImportChatInviteRequest):
            self.calls.append("import")
            if isinstance(self._imported, Exception):
                raise self._imported
            return self._imported
        raise AssertionError(f"неожиданный запрос: {request}")


def _channel(channel_id=777, title="Закрытый"):
    return SimpleNamespace(id=channel_id, title=title, username=None)


def _invite(title="Закрытый", request_needed=True):
    """Ответ Telegram о канале, в котором нас ещё нет."""
    return SimpleNamespace(title=title, request_needed=request_needed, chat=None)


@pytest.mark.parametrize("text, expected", [
    ("https://t.me/+AbCd-123", "AbCd-123"),
    ("t.me/joinchat/XyZ_987", "XyZ_987"),
    ("http://telegram.me/+QwErTy", "QwErTy"),
    ("@channel", None),
    ("https://t.me/channel", None),
    ("", None),
])
def test_an_invite_link_is_told_apart_from_a_name(text, expected):
    assert parse_invite(text) == expected


def test_a_channel_with_approval_leaves_us_waiting():
    client = _FakeClient(_invite(), InviteRequestSentError(None))

    outcome = asyncio.run(telegram_invites.join(client, "hash"))

    assert outcome.state == telegram_invites.REQUESTED
    assert outcome.channel_id is None, "канала у нас ещё нет"
    assert outcome.title == "Закрытый", "название видно и до одобрения"


def test_a_channel_without_approval_lets_us_in_at_once():
    client = _FakeClient(_invite(request_needed=False),
                         SimpleNamespace(chats=[_channel(555)]))

    outcome = asyncio.run(telegram_invites.join(client, "hash"))

    assert outcome.state == telegram_invites.JOINED
    assert outcome.channel_id == 555


def test_an_approved_request_is_seen_on_the_next_check():
    """После одобрения Telegram отдаёт по той же ссылке сам канал."""
    client = _FakeClient(ChatInviteAlready(chat=_channel(321)))

    outcome = asyncio.run(telegram_invites.check(client, "hash"))

    assert outcome is not None
    assert (outcome.state, outcome.channel_id) == (telegram_invites.JOINED, 321)
    assert client.calls == ["check"], "вступать повторно незачем"


def test_a_pending_request_is_not_mistaken_for_membership():
    client = _FakeClient(_invite())

    assert asyncio.run(telegram_invites.check(client, "hash")) is None


def test_a_dead_link_is_explained_in_words():
    client = _FakeClient(InviteHashInvalidError(None))

    with pytest.raises(InviteError) as exc:
        asyncio.run(telegram_invites.join(client, "hash"))

    assert "недействительна" in str(exc.value)


def test_the_listener_recognises_a_channel_without_a_name(db_session):
    """У закрытого канала имени нет — входящий пост узнаётся по id."""
    from app.services.telegram_event_listener import TelegramEventListenerService

    private = SourceChannel(
        title="Закрытый", username=None, url="https://t.me/+hash",
        invite_hash="hash", join_state="joined", telegram_channel_id=777,
    )
    db_session.add(private)
    db_session.commit()

    assert TelegramEventListenerService._get_source(db_session, 777) is private
    assert TelegramEventListenerService._get_source(db_session, 778) is None


def test_a_public_channel_is_still_found_by_name(db_session, source):
    from app.services.telegram_event_listener import TelegramEventListenerService

    assert TelegramEventListenerService._get_source(db_session, "testsource") is source


def test_the_reader_resolves_a_private_channel_by_id(db_session):
    """Единственный ключ к закрытому каналу — id, записанный при вступлении."""
    from app.services.telegram_reader import TelegramReaderService

    private = SourceChannel(
        title="Закрытый", username=None, url="https://t.me/+hash",
        invite_hash="hash", join_state="joined", telegram_channel_id=777,
    )
    entity = _channel(777)

    class _Client:
        async def get_entity(self, peer):
            assert getattr(peer, "channel_id", None) == 777
            return entity

    found = asyncio.run(TelegramReaderService()._resolve_entity(_Client(), private))
    assert found is entity


def test_a_channel_we_were_not_let_into_says_so(db_session):
    from app.services.telegram_reader import TelegramReaderService

    waiting = SourceChannel(
        title="Закрытый", username=None, url="https://t.me/+hash",
        invite_hash="hash", join_state="requested",
    )

    with pytest.raises(RuntimeError) as exc:
        asyncio.run(TelegramReaderService()._resolve_entity(object(), waiting))

    assert "заявку ещё не одобрили" in str(exc.value).lower()


def test_resolving_by_name_remembers_the_id(db_session, source):
    """Слушатель сверяет входящий пост по id, поэтому его надо однажды узнать."""
    from app.services.telegram_reader import TelegramReaderService

    class _Client:
        async def get_entity(self, name):
            return _channel(42, title="Публичный")

    asyncio.run(TelegramReaderService()._resolve_entity(_Client(), source))
    assert source.telegram_channel_id == 42


def test_waiting_for_approval_is_not_a_collection_error(db_session):
    """Опрос идёт каждые две минуты: заявка в ожидании не должна копить ошибки."""
    from app.services.telegram_reader import TelegramReaderService

    waiting = SourceChannel(
        title="Закрытый", username=None, url="https://t.me/+hash",
        invite_hash="hash", join_state="requested",
    )
    db_session.add(waiting)
    db_session.commit()

    assert asyncio.run(TelegramReaderService().fetch_source(db_session, waiting)) == 0


def test_the_same_link_is_not_added_twice(logged_in, db_session, csrf):
    db_session.add(SourceChannel(
        title="Закрытый", username=None, url="https://t.me/+hash",
        invite_hash="hash", join_state="requested", project_id=1,
    ))
    db_session.commit()

    answer = logged_in.post("/sources", data={
        "title": "Он же", "username_or_url": "https://t.me/+hash",
        "csrf_token": csrf(logged_in, "/sources"),
    })

    assert "error=" in answer.headers["location"], "оператору должны сказать, что канал уже есть"
    assert db_session.query(SourceChannel).filter_by(invite_hash="hash").count() == 1


def test_a_post_of_a_private_channel_keeps_a_link_to_its_source():
    """Участнику открывается t.me/c/<id>/<сообщение> — ссылка не пропадает."""
    from app.services.telegram_reader import _message_url

    private = SourceChannel(title="Закрытый", username=None, telegram_channel_id=777)
    public = SourceChannel(title="Публичный", username="news")
    waiting = SourceChannel(title="Ждём", username=None, join_state="requested")

    assert _message_url(private, 42) == "https://t.me/c/777/42"
    assert _message_url(public, 42) == "https://t.me/news/42"
    assert _message_url(waiting, 42) is None


def test_being_kicked_out_is_told_apart_from_a_cold_cache():
    """Выгнали — искать по диалогам бессмысленно, канала там уже нет."""
    from telethon.errors import ChannelPrivateError

    from app.services.telegram_reader import TelegramReaderService

    class _Client:
        async def get_entity(self, peer):
            raise ChannelPrivateError(None)

        def iter_dialogs(self):
            raise AssertionError("по диалогам тут искать нечего")

    with pytest.raises(RuntimeError) as exc:
        asyncio.run(TelegramReaderService()._entity_by_id(_Client(), 777))

    assert "исключили" in str(exc.value)


def test_checking_the_request_does_not_send_it_again(logged_in, db_session, csrf, monkeypatch):
    """Кнопка спрашивает «впустили ли», а не подаёт заявку заново."""
    from app.services.telegram_reader import TelegramReaderService

    waiting = SourceChannel(
        title="Закрытый", username=None, url="https://t.me/+hash",
        invite_hash="hash", join_state="requested", project_id=1,
    )
    db_session.add(waiting)
    db_session.commit()

    async def _no_joining(self, invite_hash):
        raise AssertionError("повторная заявка — это флуд, Telegram за него наказывает")

    async def _still_outside(self, invite_hash):
        return None

    monkeypatch.setattr(TelegramReaderService, "join_invite", _no_joining)
    monkeypatch.setattr(TelegramReaderService, "check_invite", _still_outside)

    answer = logged_in.post(f"/sources/{waiting.id}/join-check", data={
        "csrf_token": csrf(logged_in, "/sources"),
    })

    db_session.refresh(waiting)
    assert waiting.join_state == "requested", "состояние не меняется, пока не впустили"
    assert "ok=" in answer.headers["location"], "это не ошибка, а ответ «пока нет»"


def test_an_approved_request_opens_the_channel(logged_in, db_session, csrf, monkeypatch):
    from app.services.telegram_invites import InviteOutcome
    from app.services.telegram_reader import TelegramReaderService

    waiting = SourceChannel(
        title="Закрытый", username=None, url="https://t.me/+hash",
        invite_hash="hash", join_state="requested", project_id=1,
    )
    db_session.add(waiting)
    db_session.commit()

    async def _let_in(self, invite_hash):
        return InviteOutcome("joined", "Закрытый", 777)

    monkeypatch.setattr(TelegramReaderService, "check_invite", _let_in)

    logged_in.post(f"/sources/{waiting.id}/join-check", data={
        "csrf_token": csrf(logged_in, "/sources"),
    })

    db_session.refresh(waiting)
    assert (waiting.join_state, waiting.telegram_channel_id) == ("joined", 777)


def test_a_temporary_peek_is_not_membership():
    """Просмотр по ссылке выдаётся на срок: приняв его за вступление, панель
    собрала бы пару постов и замолчала."""
    from telethon.tl.types import ChatInvitePeek

    client = _FakeClient(ChatInvitePeek(chat=_channel(99), expires=datetime(2026, 1, 1)))

    assert asyncio.run(telegram_invites.check(client, "hash")) is None


def test_one_channel_does_not_become_two_sources(logged_in, db_session, csrf, monkeypatch):
    """Канал мог быть заведён раньше — по имени или по другой ссылке."""
    from app.services.telegram_invites import InviteOutcome
    from app.services.telegram_reader import TelegramReaderService

    db_session.add(SourceChannel(
        title="Он же, по имени", username="known", url="https://t.me/known",
        telegram_channel_id=777, project_id=1,
    ))
    db_session.commit()

    async def _let_in(self, invite_hash):
        return InviteOutcome("joined", "Закрытый", 777)

    monkeypatch.setattr(TelegramReaderService, "join_invite", _let_in)

    answer = logged_in.post("/sources", data={
        "title": "Закрытый", "username_or_url": "https://t.me/+hash",
        "csrf_token": csrf(logged_in, "/sources"),
    })

    assert "error=" in answer.headers["location"]
    assert db_session.query(SourceChannel).filter_by(telegram_channel_id=777).count() == 1


def test_a_message_link_is_not_mistaken_for_a_channel(logged_in, db_session, csrf):
    """t.me/c/<id>/<номер> разбиралось в имя «c»: источник выглядел рабочим и молчал."""
    answer = logged_in.post("/sources", data={
        "title": "Закрытый", "username_or_url": "https://t.me/c/1234567890/45",
        "csrf_token": csrf(logged_in, "/sources"),
    })

    assert "error=" in answer.headers["location"]
    assert db_session.query(SourceChannel).filter_by(username="c").count() == 0


def test_an_empty_address_is_refused(logged_in, db_session, csrf):
    answer = logged_in.post("/sources", data={
        "title": "Никакой", "username_or_url": "https://t.me/",
        "csrf_token": csrf(logged_in, "/sources"),
    })

    assert "error=" in answer.headers["location"]
    assert db_session.query(SourceChannel).filter_by(username="").count() == 0


def test_the_listener_counts_channels_not_keys(db_session):
    """У канала два ключа — имя и id. В счётчике для человека нужен канал."""
    from app.services.telegram_event_listener import TelegramEventListenerService

    db_session.add_all([
        SourceChannel(title="Публичный", username="news", url="https://t.me/news",
                      telegram_channel_id=11),
        SourceChannel(title="Закрытый", username=None, url="https://t.me/+hash",
                      invite_hash="hash", join_state="joined", telegram_channel_id=22),
        SourceChannel(title="Ждём", username=None, url="https://t.me/+other",
                      invite_hash="other", join_state="requested"),
    ])
    db_session.commit()

    listener = TelegramEventListenerService()
    listener._db_factory = lambda: _Session(db_session)
    asyncio.run(listener.reload_sources())

    assert listener._watched_count == 2, "ждущий одобрения канал слушать нечем"
    assert listener._source_keys == {"news", 11, 22}


class _Session:
    """Сессия из фикстуры, которую можно открыть как контекст."""

    def __init__(self, db):
        self._db = db

    def __enter__(self):
        return self._db

    def __exit__(self, *exc):
        return False

"""Закрытые каналы: ссылка-приглашение, заявка и её судьба.

Заявка закрывает вступление, а не чтение. У публичного канала посты видны
и без него — такой источник добавляется по имени, как раньше. Закрытый канал
(ссылка вида t.me/+XXXX) читать нельзя, пока администратор не одобрит заявку,
и обойти это нечем: Telegram просто не отдаёт историю чужому аккаунту.

Здесь только разговор с Telegram. Что записать в базу и что показать
оператору, решает вызывающий код.
"""

import re
from dataclasses import dataclass

from telethon.errors import (
    InviteHashExpiredError,
    InviteHashInvalidError,
    InviteRequestSentError,
    UserAlreadyParticipantError,
)
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import ChatInviteAlready

#: t.me/+XXXX и старый вид t.me/joinchat/XXXX. Голый хвост без адреса не
#: принимаем намеренно: он неотличим от обычного имени канала.
_INVITE_LINK = re.compile(
    r"(?:https?://)?(?:t(?:elegram)?\.me)/(?:joinchat/|\+)(?P<hash>[\w-]+)",
    re.IGNORECASE,
)

#: Состояния источника. «open» здесь не встречается: публичный канал сюда
#: не попадает.
REQUESTED = "requested"
JOINED = "joined"


class InviteError(RuntimeError):
    """Со ссылкой что-то не так. Текст написан для оператора и показывается как есть."""


@dataclass(frozen=True)
class InviteOutcome:
    """Чем кончилась попытка войти в закрытый канал."""

    state: str                 # REQUESTED или JOINED
    title: str                 # название канала — его Telegram отдаёт и до одобрения
    channel_id: int | None     # появляется только после одобрения


def parse_invite(text: str) -> str | None:
    """Хвост ссылки-приглашения, если это она. Иначе None — значит, обычное имя."""
    if not text:
        return None
    match = _INVITE_LINK.search(text.strip())
    return match.group("hash") if match else None


async def _ask(client, invite_hash: str):
    """Ответ Telegram про ссылку. Недействительную отличаем от всего прочего."""
    try:
        return await client(CheckChatInviteRequest(invite_hash))
    except (InviteHashInvalidError, InviteHashExpiredError) as exc:
        raise InviteError(
            "Ссылка-приглашение недействительна или отозвана. "
            "Возьмите в канале свежую."
        ) from exc


def _joined(info) -> InviteOutcome | None:
    """Канал из ответа — он есть только у того, кого уже впустили.

    Временный просмотр (ChatInvitePeek) членством не считаем: он выдаётся на
    срок и кончается сам. Приняв его за вступление, панель отчиталась бы «канал
    открыт», собрала несколько постов и замолчала без объяснения.
    """
    chat = getattr(info, "chat", None)
    if isinstance(info, ChatInviteAlready) and chat is not None:
        return InviteOutcome(JOINED, getattr(chat, "title", "") or "", chat.id)
    return None


async def check(client, invite_hash: str) -> InviteOutcome | None:
    """Впустили ли нас уже — без попытки вступить.

    None значит «мы снаружи». Висит ли при этом неодобренная заявка, Telegram
    в ответе не сообщает, поэтому помнит это база, а не Telegram.
    """
    return _joined(await _ask(client, invite_hash))


async def join(client, invite_hash: str) -> InviteOutcome:
    """Войти в канал или подать заявку.

    Канал с одобрением отвечает на попытку входа ошибкой «заявка отправлена» —
    это не сбой, а штатный ответ, и означает он «ждите одобрения».
    """
    info = await _ask(client, invite_hash)
    already = _joined(info)
    if already is not None:
        return already
    # Название Telegram отдаёт и снаружи: источник будет подписан по-человечески
    # ещё до того, как заявку одобрят.
    title = getattr(info, "title", "") or ""

    try:
        updates = await client(ImportChatInviteRequest(invite_hash))
    except InviteRequestSentError:
        return InviteOutcome(REQUESTED, title, None)
    except UserAlreadyParticipantError:
        # Между проверкой и входом нас успели впустить: спрашиваем ещё раз,
        # теперь в ответе будет сам канал.
        return await check(client, invite_hash) or InviteOutcome(JOINED, title, None)
    except (InviteHashInvalidError, InviteHashExpiredError) as exc:
        raise InviteError(
            "Ссылка-приглашение недействительна или отозвана. "
            "Возьмите в канале свежую."
        ) from exc

    chats = list(getattr(updates, "chats", None) or [])
    if not chats:
        return InviteOutcome(JOINED, title, None)
    chat = chats[0]
    return InviteOutcome(JOINED, getattr(chat, "title", "") or title, chat.id)

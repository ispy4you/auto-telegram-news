"""Что считать действием администратора и как его назвать по-русски.

Журнал пишется из middleware, а не из обработчиков: иначе на каждое новое
нажатие нужно не забыть добавить строчку в двенадцати файлах маршрутов —
и ровно то, что забудут, окажется потом нужным.

Middleware знает про запрос только метод и путь, поэтому здесь лежит таблица
соответствий: путь → событие и человеческая фраза о том, что произошло.
Незнакомый путь тоже попадает в журнал, просто без фразы: лучше строка
«POST /что-то-новое», чем молчание.

Тела запросов не пишутся никогда. В них ходят пароли, код подтверждения
Telegram и токен бота.
"""

import logging
import re
from dataclasses import dataclass

from app.models import AdminLog

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Rule:
    path: re.Pattern[str]
    action: str
    what: str


def _rule(pattern: str, action: str, what: str) -> _Rule:
    return _Rule(re.compile(pattern), action, what)


#: Запросы, которые браузер отправляет сам при открытии страницы. Человек их
#: не нажимает, а в журнале они дали бы по записи на каждый просмотр поста.
_AUTOMATIC = (re.compile(r"/posts/\d+/restore-media"),)

#: Путь → событие. Номер объекта из пути подставляется в фразу вместо «{}».
_RULES: tuple[_Rule, ...] = (
    _rule(r"/fetch-now", "fetch_now", "Запущен сбор новостей"),
    _rule(r"/posts/fetch-now", "fetch_now", "Запущен сбор новостей"),
    _rule(r"/posts/bulk", "post_bulk", "Массовое действие над постами"),
    _rule(r"/posts/(\d+)/generate", "post_generate", "Пост #{} отправлен в модель"),
    _rule(r"/posts/(\d+)/regenerate", "post_generate", "Пост #{} переписан заново"),
    _rule(r"/posts/(\d+)/edit-draft", "post_edit", "Черновик поста #{} отредактирован"),
    _rule(r"/posts/(\d+)/publish-raw", "post_publish", "Пост #{} опубликован вручную"),
    _rule(r"/posts/(\d+)/reject", "post_reject", "Пост #{} отклонён"),
    _rule(r"/posts/(\d+)/delete", "post_delete", "Пост #{} удалён"),
    _rule(r"/posts/(\d+)/media", "media_upload", "К посту #{} добавлены файлы"),
    _rule(r"/posts/(\d+)/media/(\d+)/delete", "media_delete", "У поста #{} удалён файл #{}"),
    _rule(r"/posts/(\d+)/media/(\d+)/move", "media_move", "У поста #{} переставлен файл #{}"),
    _rule(r"/generated/(\d+)/save", "draft_save", "Черновик #{} сохранён"),
    _rule(r"/generated/(\d+)/publish", "draft_publish", "Черновик #{} опубликован"),
    _rule(r"/generated/(\d+)/reject", "draft_reject", "Черновик #{} отклонён"),
    _rule(r"/compose", "compose", "Пост написан вручную"),
    _rule(r"/sources", "source_add", "Добавлен источник"),
    _rule(r"/sources/(\d+)/edit", "source_edit", "Источник #{} изменён"),
    _rule(r"/sources/(\d+)/toggle", "source_toggle", "Источник #{} включён или выключен"),
    _rule(r"/sources/(\d+)/delete", "source_delete", "Источник #{} удалён"),
    _rule(r"/sources/(\d+)/fetch", "source_fetch", "Источник #{} опрошен вручную"),
    _rule(r"/sources/(\d+)/join-check", "source_join", "Проверено вступление в источник #{}"),
    _rule(r"/targets", "target_add", "Добавлен канал публикации"),
    _rule(r"/targets/(\d+)/edit", "target_edit", "Канал #{} изменён"),
    _rule(r"/targets/(\d+)/prompt", "target_prompt", "У канала #{} изменён промпт"),
    _rule(r"/targets/(\d+)/schedule", "target_schedule", "У канала #{} изменено расписание"),
    _rule(r"/targets/(\d+)/toggle", "target_toggle", "Канал #{} включён или выключен"),
    _rule(r"/targets/(\d+)/toggle-mode", "target_mode", "У канала #{} переключён режим публикации"),
    _rule(r"/targets/(\d+)/delete", "target_delete", "Канал #{} удалён"),
    _rule(r"/targets/(\d+)/test", "target_test", "Канал #{} проверен тестовым сообщением"),
    _rule(r"/routes/(\d+)/sync", "routes_sync", "Пересобраны связки канала #{}"),
    _rule(r"/projects", "project_add", "Создан проект"),
    _rule(r"/projects/(\d+)/rename", "project_rename", "Проект #{} переименован"),
    _rule(r"/projects/(\d+)/delete", "project_delete", "Проект #{} удалён"),
    _rule(r"/switch-project", "project_switch", "Переключён проект"),
    _rule(r"/prompts", "prompt_add", "Создан промпт"),
    _rule(r"/prompts/(\d+)", "prompt_edit", "Промпт #{} изменён"),
    _rule(r"/prompts/(\d+)/default", "prompt_default", "Промпт #{} сделан основным"),
    _rule(r"/prompts/(\d+)/delete", "prompt_delete", "Промпт #{} удалён"),
    _rule(r"/settings", "settings_save", "Настройки сохранены"),
    _rule(r"/settings/reset", "settings_reset", "Настройка возвращена к умолчанию"),
    _rule(r"/settings/prompt-preview", "prompt_preview", "Промпт проверен на живой новости"),
    _rule(r"/settings/test-notify", "test_notify", "Отправлено тестовое уведомление"),
    _rule(r"/settings/cleanup-media", "media_cleanup", "Запущена чистка медиафайлов"),
    _rule(r"/settings/telegram-login/qr/start", "tg_login", "Начат вход в Telegram по QR-коду"),
    _rule(r"/settings/telegram-login/phone/start", "tg_login", "Начат вход в Telegram по телефону"),
    _rule(r"/settings/telegram-login/code", "tg_code", "Введён код подтверждения Telegram"),
    _rule(r"/settings/telegram-login/password", "tg_password", "Введён облачный пароль Telegram"),
    _rule(r"/settings/telegram-login/cancel", "tg_cancel", "Вход в Telegram отменён"),
    _rule(r"/settings/telegram-login/logout", "tg_logout", "Выход из Telegram"),
)

_LOGIN_OUTCOMES = {
    "login": "Вход в панель",
    "login_failed": "Неверный логин или пароль",
    "login_locked": "Вход заблокирован: слишком много попыток",
}

_OTHER = "other"

def _build_labels() -> dict[str, str]:
    """Название события для фильтра — та же фраза, но без номера объекта."""
    labels = dict(_LOGIN_OUTCOMES)
    for rule in _RULES:
        labels.setdefault(rule.action, re.sub(r"\s*#\{\}", "", rule.what))
    labels[_OTHER] = "Прочее"
    return labels


_LABELS = _build_labels()


def label(action: str) -> str:
    """Человеческое название события. Незнакомое возвращаем как есть."""
    return _LABELS.get(action, action)


def describe(method: str, path: str, location: str = "") -> tuple[str, str] | None:
    """Событие и фраза о нём — или None, если запрос не человеческий.

    Путь матчится целиком: иначе «/posts/1/media/2/delete» попало бы в правило
    загрузки файлов, а «/settings/reset» — в сохранение настроек.
    """
    if any(p.fullmatch(path) for p in _AUTOMATIC):
        return None
    if path == "/login":
        return _login_outcome(location)
    for rule in _RULES:
        found = rule.path.fullmatch(path)
        if found:
            return rule.action, rule.what.format(*found.groups())
    return _OTHER, f"{method} {path}"


def _login_outcome(location: str) -> tuple[str, str]:
    """Удачен ли вход, видно только по адресу, куда увело форму."""
    if "error=locked" in location:
        action = "login_locked"
    elif "error=" in location:
        action = "login_failed"
    else:
        action = "login"
    return action, _LOGIN_OUTCOMES[action]


def record(
    session_factory,
    *,
    method: str,
    path: str,
    actor: str,
    authenticated: bool,
    status: int,
    location: str = "",
) -> None:
    """Пишет действие в журнал. Своя сессия: транзакция запроса уже закрыта.

    Ошибка записи только логируется: журнал не должен ронять ответ, который
    человек уже получил, — тем более что причина обычно в недоступной базе.
    """
    described = describe(method, path, location)
    if described is None:
        return
    action, message = described
    # Путь приходит из запроса: длину строки задаёт тот, кто его отправил.
    message = message[:500]
    if status >= 400:
        message = f"{message} — отказ {status}"
    try:
        with session_factory() as db:
            db.add(AdminLog(
                action=action,
                actor=actor[:64],
                authenticated=authenticated,
                method=method[:8],
                path=path[:255],
                status=status,
                message=message,
            ))
            db.commit()
    except Exception:
        logger.warning("Действие %s %s не попало в журнал", method, path, exc_info=True)

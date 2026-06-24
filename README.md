# Telegram News Bot MVP (FastAPI + SQLite)

Production-ready MVP для мониторинга публичных Telegram-каналов через Telethon user session, генерации новостей через Timeweb AI Gateway и публикации в целевые Telegram-каналы через Telegram Bot API (aiogram).

## Стек

- Python 3.12+
- FastAPI + Jinja2 + Bootstrap 5
- SQLite + SQLAlchemy 2.x
- Telethon (чтение источников)
- RSS/Atom (опциональный fallback)
- aiogram 3.x (публикация)
- APScheduler (фоновый сбор)
- httpx (AI gateway)
- rapidfuzz (дедупликация)

## Установка на macOS

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Получение TELEGRAM_API_ID и TELEGRAM_API_HASH

1. Перейдите на [my.telegram.org](https://my.telegram.org).
2. Войдите в аккаунт.
3. Создайте приложение в разделе API development tools.
4. Скопируйте `api_id` и `api_hash` в `.env`.

## Создание user session (Telethon)

```bash
python -m app.cli.init_telegram_session
```

Команда интерактивно запросит phone/code/password (если включен 2FA) и сохранит сессию в `TELEGRAM_SESSION_PATH`.

## Создание бота через BotFather

1. Напишите `@BotFather`.
2. Выполните `/newbot`.
3. Получите токен и добавьте в `.env` как `TELEGRAM_BOT_TOKEN`.

## Добавление бота админом в целевой канал

1. Откройте настройки канала.
2. Добавьте бота в администраторы.
3. Выдайте права на публикацию.
4. В админке нажмите `Test` у target channel.

## Запуск локально

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

## Запуск на VPS Ubuntu

1. Установите Python 3.12+, venv, git.
2. Разверните проект и `.env`.
3. Создайте Telethon session через `python -m app.cli.init_telegram_session`.
4. Запускайте через systemd/pm2/supervisor.
5. Открывайте админку через SSH tunnel или reverse proxy с HTTPS и auth.

## Настройка Timeweb AI Gateway

Заполните в `.env`:

- `TIMEWEB_AI_GATEWAY_API_KEY`
- `TIMEWEB_AI_GATEWAY_BASE_URL`
- `TIMEWEB_AI_GATEWAY_MODEL`
- `AI_TEMPERATURE`
- `AI_MAX_TOKENS`
- `AI_TIMEOUT_SECONDS`

Клиент использует OpenAI-compatible endpoint `.../chat/completions`.

## Работа в админке

1. Добавьте source channels (`/sources`) по `@username` или `https://t.me/...` (тип `telethon`).
2. Добавьте target channels (`/targets`) с `chat_id`.
3. Нажмите `Запустить сбор сейчас` на dashboard или `/posts`.
4. Проверяйте посты в `/posts`, запускайте генерацию AI.
5. Редактируйте текст и публикуйте в нужный target.

RSS-источники по-прежнему поддерживаются как опциональный тип `rss` в `/sources`.

## Ограничения

- Telegram Bot API имеет лимиты на размер файлов.
- Caption media ограничен (~1024 символа), длинный текст отправляется отдельным сообщением.
- Источники читаются через user session Telethon; бот не админ в источниках.
- Публичные каналы должны быть доступны аккаунту user session.
- Автопубликацию включайте осторожно.

## Базовая проверка

```bash
pytest
```

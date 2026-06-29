# Telegram News Bot MVP

Production-ready MVP для мониторинга публичных Telegram-каналов, генерации новостных постов через AI и публикации в целевые каналы через Telegram Bot API.

---

## Архитектура

```mermaid
flowchart TD
    subgraph SRC["📡 Источники (Telegram)"]
        S1[Channel 1]
        S2[Channel 2]
        SN[Channel N ...]
    end

    subgraph COLLECT["🔄 Сбор контента"]
        EL["⚡ Event Listener\nTelethon · real-time\nNewMessage handler"]
        POLL["🕐 Scheduler\nAPScheduler · fallback\nпри потере соединения"]
    end

    subgraph DB["🗄️ PostgreSQL"]
        RAW[(raw_posts)]
        MEDIA[(media_items)]
        LOGS[(action_logs)]
    end

    subgraph PIPE["⚙️ Пайплайн обработки"]
        DEDUP["🔍 Дедупликация\nSHA-256 → rapidfuzz → fastembed\nсемантическое сравнение"]
        AI["🤖 AI-генерация\nTimeweb AI Gateway\nOpenAI-совместимый API"]
    end

    subgraph PUB["📤 Публикация"]
        BOT["🤖 Telegram Bot\naiogram 3.x"]
        SCHED_PUB["🗓️ Расписание\nвременны́е окна\nпо каналам"]
    end

    subgraph TGT["📢 Целевые каналы"]
        T1[Channel A]
        T2[Channel B]
    end

    ADMIN["🖥️ Admin UI\nFastAPI · Jinja2 · Bootstrap 5\nlocalhost:8000"]

    S1 & S2 & SN -->|"NewMessage event"| EL
    S1 & S2 & SN -->|"polling fallback"| POLL
    EL -->|"сохранить пост + медиа"| RAW
    POLL -->|"сохранить пост + медиа"| RAW
    EL & POLL --> MEDIA

    RAW --> DEDUP
    DEDUP -->|"статус READY"| AI
    AI -->|"черновик"| BOT
    BOT --> SCHED_PUB
    SCHED_PUB --> T1 & T2

    ADMIN <-->|"управление, просмотр, публикация"| DB
    ADMIN -->|"ручная публикация"| BOT
    DB --> LOGS
```

---

## Возможности

### Сбор контента
- Читает посты из любого числа публичных Telegram-каналов через **Telethon user session** — без необходимости быть администратором источника
- **Режим реального времени**: постоянное Telethon-соединение с обработчиком `NewMessage` — новый пост сохраняется мгновенно, без ожидания следующего тика планировщика
- **Автоматический catch-up**: при старте (или переподключении) подтягиваются пропущенные сообщения по каждому каналу
- **Polling как fallback**: планировщик продолжает работать при потере соединения; когда event listener активен — шаг fetch пропускается автоматически
- Автоматическое переподключение при разрыве (пауза 30 с, затем повторная попытка)
- Инкрементальный сбор: запоминает последнее прочитанное сообщение, не тянет одно и то же дважды
- Настраиваемый интервал фонового планировщика (30 с — 24 ч, по умолчанию 120 с)
- Скачивает и сохраняет вложения: фото, видео, документы; корректная обработка альбомов (grouped messages)

### Дедупликация
- **SHA-256** — мгновенное обнаружение точных копий
- **rapidfuzz** — нечёткий поиск дублей с настраиваемым порогом (50–100%, по умолчанию 88%)
- **fastembed** (`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`) — семантическая дедупликация: ловит смысловые дубликаты, которые rapidfuzz пропускает («Путин подписал закон» ≈ «Президент подписал документ»). Порог и включение настраиваются в UI; модель ~220 МБ, загружается один раз
- Нормализация текста перед сравнением: убирает ссылки, пунктуацию, лишние пробелы
- Окно проверки — 48 часов; эмбеддинги сохраняются в БД для повторного использования

### AI-генерация
- Интеграция с **Timeweb AI Gateway** (OpenAI-совместимый endpoint)
- Системный промпт и пользовательский шаблон хранятся в базе и редактируются прямо в админке
- Настраиваемые температура, лимит токенов, таймаут
- Автоматическая оценка поста: SUITABLE / REJECTED
- Корректная обработка обрезанного JSON-ответа

### Управление постами
- Статусная машина: `NEW → READY → GENERATED → PUBLISHED`
- Массовые операции: сгенерировать, отклонить, удалить несколько постов за раз
- Ручное редактирование AI-текста перед публикацией
- Публикация оригинального текста без AI-обработки
- Повторная генерация для уже обработанных постов
- **Предпросмотр в стиле Telegram** прямо на странице поста: пузырь сообщения, медиагрид, счётчик символов, горячая клавиша `Ctrl+P` — обновляется в реальном времени при редактировании

### Публикация
- Отправка в несколько целевых каналов через **aiogram 3.x**
- Умная работа с медиа:
  - одиночный файл — отправляется с подписью
  - несколько файлов — media group, текст на первом
  - длинный текст — отдельным сообщением после медиа
- Переключатель «отправить с медиа / без» при каждой публикации
- Маршруты: задаёте, какой источник идёт в какой канал; если маршрутов нет — посты рассылаются во все активные каналы
- Автопубликация: полностью автоматический пайплайн без участия оператора
- Отслеживание заданий публикации с повторными попытками (до 3)
- **Расписание публикаций**: для каждого целевого канала задаётся временно́е окно (`publish_from` / `publish_to`). Посты вне окна уходят в очередь и публикуются автоматически при открытии следующего окна
- `telegram_message_id` сохраняется после отправки — для будущего редактирования и аналитики

### Мультипроектность
- Неограниченное число **проектов** — изолированные пространства с собственными источниками, каналами и маршрутами
- Быстрое переключение через выпадающее меню в шапке; все счётчики и списки фильтруются по текущему проекту
- CRUD для проектов: создать, переименовать, удалить (с защитой от случайного удаления)
- Все старые данные при первом запуске автоматически переходят в проект «Default»

### Уведомления оператору
- Бот отправляет **Telegram-сообщения** оператору при накоплении черновиков сверх порога
- Уведомления при ошибках пайплайна (включается отдельно)
- Кнопка тестового сообщения прямо в настройках
- Антиспам: уведомление повторяется только при росте количества черновиков

### Веб-интерфейс и дашборд
- Сводка: активные источники, новые посты, дубли, черновики, опубликовано сегодня/за неделю
- Статус планировщика: интервал, следующий запуск, кнопка «запустить сейчас»
- Лог последних действий на главной странице

### Статистика публикаций
- Воронка: собрано → готово → сгенерировано → опубликовано
- График публикаций по дням за последние 30 дней (**Chart.js**)
- Топ источников по количеству публикаций
- Детальная сводка по каждому целевому каналу с прогресс-барами

### Логирование и аудит
- Полная история всех действий пользователя и системных событий
- Фильтрация по типу события и тексту
- Постраничный вывод (100 записей на страницу)

### Безопасность
- Сессионная аутентификация с HMAC-сравнением в constant time
- CSRF-защита на всех формах: double-submit token + сессионное хранилище
- Медиафайлы отдаются через авторизованный маршрут `/media/`, не как публичная статика
- В production-режиме запуск блокируется, если не заменены дефолтные секреты

---

## Стек

| Слой | Библиотеки |
|---|---|
| Web | FastAPI, Uvicorn, Jinja2, Bootstrap 5 |
| База данных | PostgreSQL + psycopg2-binary, SQLAlchemy 2.x (SQLite — только для локальной разработки) |
| Telegram (чтение) | Telethon |
| Telegram (публикация) | aiogram 3.x |
| AI | httpx + OpenAI-compatible endpoint |
| Дедупликация | rapidfuzz + fastembed (paraphrase-multilingual-MiniLM-L12-v2) |
| Планировщик | APScheduler |
| Безопасность | itsdangerous (CSRF) |
| Графики | Chart.js |
| Прокси | PySocks (SOCKS5 / HTTP / MTProxy) |

---

## Установка

### Ubuntu / Debian

```bash
# 1. Python 3.12+ и системные зависимости
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev git

# 2. PostgreSQL
sudo apt install -y postgresql postgresql-contrib libpq-dev

# 3. Клонировать проект
git clone <repo-url> tg-news-mvp
cd tg-news-mvp

# 4. Виртуальное окружение
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 5. Конфиг
cp .env.example .env
# отредактируйте .env
```

### macOS (локальная разработка)

```bash
brew install postgresql@17
echo 'export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
brew services start postgresql@17

git clone <repo-url> tg-news-mvp
cd tg-news-mvp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Создание базы данных PostgreSQL

**Ubuntu:**
```bash
sudo -u postgres psql -c "CREATE USER tgnews WITH PASSWORD 'yourpassword';"
sudo -u postgres psql -c "CREATE DATABASE tgnews OWNER tgnews;"
```

**macOS (Homebrew, пользователь без пароля):**
```bash
createdb tgnews
```

Пропишите в `.env`:
```dotenv
# Ubuntu:
DATABASE_URL=postgresql://tgnews:yourpassword@localhost/tgnews
# macOS (Homebrew, имя системного пользователя):
# DATABASE_URL=postgresql://your_macos_username@localhost/tgnews
```

Таблицы создаются автоматически при первом запуске (`Base.metadata.create_all`).

> SQLite (`sqlite:///./data/app.db`) поддерживается для локальной разработки без PostgreSQL,
> но не рекомендуется в production из-за ограничений конкурентной записи.

### Получение TELEGRAM_API_ID и TELEGRAM_API_HASH

1. Перейдите на [my.telegram.org](https://my.telegram.org).
2. Войдите в аккаунт.
3. Создайте приложение в разделе **API development tools**.
4. Скопируйте `api_id` и `api_hash` в `.env`.

### Создание user session (Telethon)

```bash
python -m app.cli.init_telegram_session
```

Команда интерактивно запросит phone/code/password (если включён 2FA) и сохранит сессию в `TELEGRAM_SESSION_PATH`.

### Создание бота через BotFather

1. Напишите `@BotFather`.
2. Выполните `/newbot`.
3. Получите токен и добавьте в `.env` как `TELEGRAM_BOT_TOKEN`.

### Добавление бота в целевой канал

1. Откройте настройки канала → **Администраторы**.
2. Добавьте бота, выдайте право на публикацию сообщений.
3. В админке нажмите `Test` у нужного target channel.

---

## Конфигурация `.env`

```dotenv
# Приложение
APP_ENV=local                        # local | production
APP_HOST=127.0.0.1
APP_PORT=8000
APP_SECRET_KEY=change-me             # обязательно сменить в production
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me             # обязательно сменить в production
ADMIN_AUTH_ENABLED=true

# База данных
DATABASE_URL=postgresql://tgnews:yourpassword@localhost/tgnews

# Telegram
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_SESSION_PATH=./data/telegram_session/user.session
TELEGRAM_BOT_TOKEN=

# Прокси (опционально)
TELEGRAM_PROXY_TYPE=                 # socks5 | http | mtproxy | ""
TELEGRAM_PROXY_HOST=
TELEGRAM_PROXY_PORT=
TELEGRAM_PROXY_USERNAME=
TELEGRAM_PROXY_PASSWORD=
TELEGRAM_PROXY_SECRET=               # только для MTProxy

# AI Gateway
TIMEWEB_AI_GATEWAY_API_KEY=
TIMEWEB_AI_GATEWAY_BASE_URL=
TIMEWEB_AI_GATEWAY_MODEL=
AI_TEMPERATURE=0.4
AI_MAX_TOKENS=1600
AI_TIMEOUT_SECONDS=60

# Пайплайн
FETCH_INTERVAL_SECONDS=120
DEFAULT_LOOKBACK_LIMIT=50
MAX_MEDIA_MB=50
AUTO_PUBLISH_ENABLED=false
DEFAULT_POST_MODE=manual
```

---

## Запуск

### Локально

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Или без активации окружения:

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Ubuntu / VPS (production через systemd)

```bash
# 1. Зависимости системы
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev git postgresql postgresql-contrib libpq-dev

# 2. База данных
sudo -u postgres psql -c "CREATE USER tgnews WITH PASSWORD 'yourpassword';"
sudo -u postgres psql -c "CREATE DATABASE tgnews OWNER tgnews;"

# 3. Проект
git clone <repo-url> /opt/tg-news-mvp
cd /opt/tg-news-mvp
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# отредактируйте .env: DATABASE_URL, APP_SECRET_KEY, ADMIN_PASSWORD, Telegram-ключи

# 4. Telethon-сессия (один раз, интерактивно)
python -m app.cli.init_telegram_session

# 5. systemd-сервис
sudo tee /etc/systemd/system/tgnews.service > /dev/null <<EOF
[Unit]
Description=Telegram News Bot
After=network.target postgresql.service

[Service]
User=$USER
WorkingDirectory=/opt/tg-news-mvp
ExecStart=/opt/tg-news-mvp/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable tgnews
sudo systemctl start tgnews
sudo systemctl status tgnews
```

### Управление сервисами

```bash
# Приложение
sudo systemctl start tgnews
sudo systemctl stop tgnews
sudo systemctl restart tgnews
sudo systemctl status tgnews
journalctl -u tgnews -f          # логи в реальном времени

# PostgreSQL
sudo systemctl start postgresql
sudo systemctl stop postgresql
sudo systemctl status postgresql
```

Откройте админку через SSH-туннель (`ssh -L 8000:127.0.0.1:8000 user@server`) или настройте nginx как reverse proxy с HTTPS.

---

## Работа в админке

1. **Projects** → при необходимости создайте отдельные проекты; переключайтесь через меню в шапке.
2. **Sources** → добавьте каналы-источники по `@username` или `https://t.me/...`.
3. **Targets** → добавьте целевые каналы с `chat_id`, проверьте кнопкой `Test`; задайте расписание публикаций.
4. **Routes** → настройте маршруты: какой источник идёт в какой канал.
5. **Dashboard** → нажмите «Запустить сбор сейчас» или дождитесь автоматического запуска.
6. **Posts** → просматривайте новые посты, запускайте AI-генерацию, редактируйте и публикуйте. Используйте `Ctrl+P` для предпросмотра в стиле Telegram.
7. **Stats** → смотрите воронку, график публикаций по дням, топ источников.
8. **Settings** → подстройте пороги дедупликации, промпты, интервал сбора, уведомления оператору.

---

## Миграции БД

Миграции применяются автоматически при каждом старте через `ALTER TABLE … ADD COLUMN` в try/except. Ничего запускать вручную не нужно.

Колонки, добавляемые автомиграцией:
- `target_channels.publish_from` / `publish_to` — временно́е окно публикаций
- `generated_posts.telegram_message_id` — ID сообщения после отправки
- `source_channels.project_id` / `target_channels.project_id` — мультипроектность
- `raw_posts.embedding` — векторный эмбеддинг для семантической дедупликации

Все Telegram ID-колонки (`telegram_message_id`, `telegram_grouped_id`, `last_message_id`, `telegram_channel_id`) автоматически расширяются до `BIGINT` — Telegram использует 64-битные числа, которые не помещаются в стандартный `INTEGER`.

---

## Ограничения

- Telegram Bot API ограничивает размер файлов при отправке.
- Caption к медиа ограничен ~1024 символами; длинный текст отправляется отдельным сообщением.
- Источники читаются через user session — бот не должен быть администратором источника.
- Публичные каналы должны быть доступны аккаунту user session.
- Автопубликацию включайте осторожно: нет дополнительного ревью перед отправкой.
- Семантическая дедупликация требует ~220 МБ под модель и заметно нагружает CPU. На слабых VPS рекомендуется держать выключенной.

---

## Тесты

```bash
pytest
```

FROM python:3.12-slim

WORKDIR /app

# Логи должны идти в stdout без буферизации — иначе они не видны в панели Timeweb.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FASTEMBED_CACHE_PATH=/opt/fastembed

# libgomp1 is required by onnxruntime (fastembed's backend) at runtime.
# curl is needed by the platform healthcheck: python:3.12-slim ships without any
# HTTP client, so a curl-based probe injected into the container silently fails.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Модель эмбеддингов (~220 МБ) кладётся в образ, чтобы не скачивать её при каждом
# старте контейнера: на App Platform диск не сохраняется между деплоями.
# Если загрузка недоступна — не роняем сборку, дедупликация откатится на rapidfuzz.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')" \
    || echo "WARN: fastembed model not pre-downloaded, will be fetched at runtime"

COPY . .
RUN mkdir -p data/media data/telegram_session

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# --proxy-headers: приложение работает за Nginx (App Platform, docker-compose с реверс-прокси).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]

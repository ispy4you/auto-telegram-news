"""Alembic-окружение.

URL базы берётся не из alembic.ini, а из настроек приложения — чтобы источник
правды был один и не приходилось держать строку подключения в двух местах.
"""

from alembic import context

from app.database import Base, engine
import app.models  # noqa: F401  — импорт регистрирует таблицы в Base.metadata

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=str(engine.url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

"""Счётчик projects.id сдвигается за вручную вставленный проект

Revision ID: 0013_projects_id_sequence
Revises: 0012_target_prompt
"""

from alembic import op

revision = "0013_projects_id_sequence"
down_revision = "0012_target_prompt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Миграция 0003 вставила проект «Default» с явным id = 1, а явный id счётчик
    # последовательности не двигает. Поэтому первый же проект, созданный из панели,
    # получал от базы тот же id = 1 и падал на дубликате ключа. Сдвигаем счётчик
    # за максимальный занятый id — один раз, дальше база ведёт его сама.
    # В SQLite последовательностей нет: там rowid выдаётся от максимума сам.
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('projects', 'id'),
            COALESCE((SELECT MAX(id) FROM projects), 1)
        )
        """
    )


def downgrade() -> None:
    # Откатывать нечего: счётчик нельзя «разсинхронизировать» осмысленно.
    pass

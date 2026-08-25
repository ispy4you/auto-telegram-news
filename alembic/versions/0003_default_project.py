"""Проект по умолчанию и привязка к нему осиротевших каналов.

Раньше это выполнялось сырым SQL при каждом старте приложения. Это разовая
правка данных, её место — в миграции.

Revision ID: 0003_default_project
Revises: 0002_publish_job_unique
"""
from alembic import op

revision = "0003_default_project"
down_revision = "0002_publish_job_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO projects (id, name, slug, enabled, created_at)
        SELECT 1, 'Default', 'default', TRUE, CURRENT_TIMESTAMP
        WHERE NOT EXISTS (SELECT 1 FROM projects WHERE id = 1)
        """
    )
    op.execute("UPDATE source_channels SET project_id = 1 WHERE project_id IS NULL")
    op.execute("UPDATE target_channels SET project_id = 1 WHERE project_id IS NULL")


def downgrade() -> None:
    # Проект по умолчанию не удаляем: на него ссылаются каналы.
    pass

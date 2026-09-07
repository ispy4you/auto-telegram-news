"""У канала назначения появляется промпт по умолчанию.

Промптов стало несколько, но выбирать их приходилось руками при каждой
генерации, а автопубликации выбирать было негде — она всегда брала основной.

Revision ID: 0012_target_prompt
Revises: 0011_prompts
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0012_target_prompt"
down_revision = "0011_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Внешний ключ ставим сразу: связь необязательная, но висячий prompt_id —
    # это молча сломанная генерация, а не мелочь. Перед удалением промпта код
    # снимает его с каналов сам.
    op.add_column(
        "target_channels",
        sa.Column("prompt_id", sa.Integer(), sa.ForeignKey("prompts.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("target_channels", "prompt_id")

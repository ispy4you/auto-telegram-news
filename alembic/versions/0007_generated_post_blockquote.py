"""Признак «оформить цитатой» на сгенерированном посте.

Revision ID: 0007_generated_post_blockquote
Revises: 0006_unescape_prompt_template
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_generated_post_blockquote"
down_revision = "0006_unescape_prompt_template"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generated_posts",
        sa.Column("as_blockquote", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("generated_posts", "as_blockquote")

"""Базовая схема на момент внедрения Alembic.

Повторяет то, что раньше создавал Base.metadata.create_all вместе с ручными
ALTER TABLE в main.py. На существующих базах эта ревизия не выполняется —
app/migrations.py помечает их как уже находящиеся на этой точке.

Revision ID: 0001_baseline
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )

    op.create_table(
        "source_channels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("rss_url", sa.String(length=1024), nullable=True),
        sa.Column("telegram_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_message_id", sa.BigInteger(), nullable=True),
        sa.Column("last_fetched_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index("ix_source_channels_project_id", "source_channels", ["project_id"])

    op.create_table(
        "target_channels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("chat_id", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("auto_publish_enabled", sa.Boolean(), nullable=False),
        sa.Column("default_mode", sa.String(length=16), nullable=False),
        sa.Column("publish_from", sa.String(length=5), nullable=True),
        sa.Column("publish_to", sa.String(length=5), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id"),
    )
    op.create_index("ix_target_channels_project_id", "target_channels", ["project_id"])

    op.create_table(
        "source_target_routes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("target_channel_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["source_channels.id"]),
        sa.ForeignKeyConstraint(["target_channel_id"], ["target_channels.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "raw_posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_grouped_id", sa.BigInteger(), nullable=True),
        sa.Column("source_url", sa.String(length=512), nullable=True),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.Column("published_at_source", sa.DateTime(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("has_media", sa.Boolean(), nullable=False),
        sa.Column("media_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("duplicate_of_id", sa.Integer(), nullable=True),
        sa.Column("dedupe_score", sa.Float(), nullable=True),
        sa.Column("ai_suitable", sa.Boolean(), nullable=True),
        sa.Column("ai_skip_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["source_channels.id"]),
        sa.ForeignKeyConstraint(["duplicate_of_id"], ["raw_posts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "telegram_message_id", name="uq_source_message"),
    )
    op.create_index("ix_raw_posts_text_hash", "raw_posts", ["text_hash"])

    op.create_table(
        "media_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("raw_post_id", sa.Integer(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["raw_post_id"], ["raw_posts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "generated_posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("raw_post_id", sa.Integer(), nullable=False),
        sa.Column("target_channel_id", sa.Integer(), nullable=True),
        sa.Column("generated_text", sa.Text(), nullable=False),
        sa.Column("edited_text", sa.Text(), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("generation_error", sa.Text(), nullable=True),
        sa.Column("publish_error", sa.Text(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["raw_post_id"], ["raw_posts.id"]),
        sa.ForeignKeyConstraint(["target_channel_id"], ["target_channels.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "publish_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("generated_post_id", sa.Integer(), nullable=False),
        sa.Column("target_channel_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["generated_post_id"], ["generated_posts.id"]),
        sa.ForeignKeyConstraint(["target_channel_id"], ["target_channels.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "action_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("action_logs")
    op.drop_table("app_settings")
    op.drop_table("publish_jobs")
    op.drop_table("generated_posts")
    op.drop_table("media_items")
    op.drop_index("ix_raw_posts_text_hash", table_name="raw_posts")
    op.drop_table("raw_posts")
    op.drop_table("source_target_routes")
    op.drop_index("ix_target_channels_project_id", table_name="target_channels")
    op.drop_table("target_channels")
    op.drop_index("ix_source_channels_project_id", table_name="source_channels")
    op.drop_table("source_channels")
    op.drop_table("projects")

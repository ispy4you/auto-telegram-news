from datetime import datetime, timezone
from enum import Enum


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RawPostStatus(str, Enum):
    NEW = "new"
    DUPLICATE = "duplicate"
    READY = "ready"
    GENERATED = "generated"
    REJECTED = "rejected"
    PUBLISHED = "published"
    FAILED = "failed"


class GeneratedPostStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    REJECTED = "rejected"
    FAILED = "failed"


class PublishJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class MediaType(str, Enum):
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"
    UNKNOWN = "unknown"


class SourceChannel(Base):
    __tablename__ = "source_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), default="telethon")
    rss_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    telegram_channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url: Mapped[str] = mapped_column(String(512))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    posts = relationship("RawPost", back_populates="source", cascade="all, delete-orphan")


class TargetChannel(Base):
    __tablename__ = "target_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    chat_id: Mapped[str] = mapped_column(String(255), unique=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_publish_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    default_mode: Mapped[str] = mapped_column(String(16), default="manual")
    publish_from: Mapped[str | None] = mapped_column(String(5), nullable=True)
    publish_to: Mapped[str | None] = mapped_column(String(5), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class SourceTargetRoute(Base):
    __tablename__ = "source_target_routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source_channels.id"))
    target_channel_id: Mapped[int] = mapped_column(ForeignKey("target_channels.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class RawPost(Base):
    __tablename__ = "raw_posts"
    __table_args__ = (
        UniqueConstraint("source_id", "telegram_message_id", name="uq_source_message"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source_channels.id"))
    telegram_message_id: Mapped[int] = mapped_column(Integer)
    telegram_grouped_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    original_text: Mapped[str] = mapped_column(Text, default="")
    normalized_text: Mapped[str] = mapped_column(Text, default="")
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    published_at_source: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    has_media: Mapped[bool] = mapped_column(Boolean, default=False)
    media_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default=RawPostStatus.NEW.value)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("raw_posts.id"), nullable=True)
    dedupe_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_suitable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ai_skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    source = relationship("SourceChannel", back_populates="posts")
    media_items = relationship("MediaItem", back_populates="raw_post", cascade="all, delete-orphan")
    generated_posts = relationship("GeneratedPost", back_populates="raw_post", cascade="all, delete-orphan")


class MediaItem(Base):
    __tablename__ = "media_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_post_id: Mapped[int] = mapped_column(ForeignKey("raw_posts.id"))
    telegram_message_id: Mapped[int] = mapped_column(Integer)
    media_type: Mapped[str] = mapped_column(String(16), default=MediaType.UNKNOWN.value)
    file_path: Mapped[str] = mapped_column(String(1024))
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    raw_post = relationship("RawPost", back_populates="media_items")


class GeneratedPost(Base):
    __tablename__ = "generated_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_post_id: Mapped[int] = mapped_column(ForeignKey("raw_posts.id"))
    target_channel_id: Mapped[int | None] = mapped_column(ForeignKey("target_channels.id"), nullable=True)
    generated_text: Mapped[str] = mapped_column(Text, default="")
    edited_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str] = mapped_column(String(255), default="")
    prompt_version: Mapped[str] = mapped_column(String(64), default="v1")
    status: Mapped[str] = mapped_column(String(16), default=GeneratedPostStatus.DRAFT.value)
    generation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    publish_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    raw_post = relationship("RawPost", back_populates="generated_posts")
    publish_jobs = relationship("PublishJob", back_populates="generated_post", cascade="all, delete-orphan")


class PublishJob(Base):
    __tablename__ = "publish_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generated_post_id: Mapped[int] = mapped_column(ForeignKey("generated_posts.id"))
    target_channel_id: Mapped[int] = mapped_column(ForeignKey("target_channels.id"))
    status: Mapped[str] = mapped_column(String(16), default=PublishJobStatus.PENDING.value)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    generated_post = relationship("GeneratedPost", back_populates="publish_jobs")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class ActionLog(Base):
    __tablename__ = "action_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(128))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

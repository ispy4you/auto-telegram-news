import hashlib
import re
from datetime import datetime, timedelta, timezone

from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import RawPost, RawPostStatus
from app.services.text_cleanup import clean_telegram_rss_text


class DeduplicationService:
    def __init__(self, threshold: int = 88):
        self.threshold = threshold

    @staticmethod
    def normalize_text(text: str) -> str:
        text = clean_telegram_rss_text(text or "")
        text = text.lower()
        text = re.sub(r"https?://\S+|www\.\S+", " ", text)
        text = re.sub(r"[^a-zа-я0-9\s]", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def text_hash(normalized_text: str) -> str:
        return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()

    def deduplicate_post(self, db: Session, post: RawPost) -> RawPost:
        normalized = self.normalize_text(post.original_text)
        post.normalized_text = normalized
        post.text_hash = self.text_hash(normalized)

        if not normalized:
            post.status = RawPostStatus.READY.value
            return post

        same_hash = db.scalar(
            select(RawPost).where(RawPost.id != post.id, RawPost.text_hash == post.text_hash).limit(1)
        )
        if same_hash:
            post.status = RawPostStatus.DUPLICATE.value
            post.duplicate_of_id = same_hash.id
            post.dedupe_score = 100.0
            return post

        boundary = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=48)
        text_len = len(post.normalized_text)
        candidates = db.scalars(
            select(RawPost).where(
                RawPost.id != post.id,
                RawPost.created_at >= boundary,
                RawPost.status != RawPostStatus.FAILED.value,
                # Pre-filter by approximate text length to avoid loading very short/long posts
                func.length(RawPost.normalized_text).between(
                    max(1, text_len // 2),
                    text_len * 2 + 1,
                ),
            ).limit(500)
        ).all()

        for candidate in candidates:
            score = fuzz.token_set_ratio(post.normalized_text, candidate.normalized_text or "")
            if score >= self.threshold:
                post.status = RawPostStatus.DUPLICATE.value
                post.duplicate_of_id = candidate.id
                post.dedupe_score = float(score)
                return post

        post.status = RawPostStatus.READY.value
        return post

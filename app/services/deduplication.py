import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import RawPost, RawPostStatus
from app.services import post_lifecycle, settings_registry
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

        # Phase 1: exact hash match
        same_hash = db.scalar(
            select(RawPost).where(RawPost.id != post.id, RawPost.text_hash == post.text_hash).limit(1)
        )
        if same_hash:
            post_lifecycle.mark_duplicate(post, same_hash, 100.0)
            return post

        boundary = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=48)
        text_len = len(post.normalized_text)
        candidates = db.scalars(
            select(RawPost).where(
                RawPost.id != post.id,
                RawPost.created_at >= boundary,
                RawPost.status != RawPostStatus.FAILED.value,
                func.length(RawPost.normalized_text).between(
                    max(1, text_len // 2),
                    text_len * 2 + 1,
                ),
            ).limit(500)
        ).all()

        # Phase 2: rapidfuzz lexical similarity
        threshold = settings_registry.get("duplicate_threshold", db)

        for candidate in candidates:
            score = fuzz.token_set_ratio(post.normalized_text, candidate.normalized_text or "")
            if score >= threshold:
                post_lifecycle.mark_duplicate(post, candidate, float(score))
                return post

        # Phase 3: semantic similarity (requires fastembed)
        semantic_threshold = settings_registry.get("semantic_threshold", db)

        from app.services import embedder as _emb

        # Вектор считаем всегда, а не только при включённом пороге: иначе после
        # включения семантики сравнивать оказывается не с чем — у постов в окне
        # эмбеддингов нет, и фаза молча не работает, пока окно не обновится.
        if post.embedding is None:
            vec_json = _emb.embed_text_json(normalized)
            if vec_json:
                post.embedding = vec_json

        if semantic_threshold > 0 and post.embedding:
            post_vec = json.loads(post.embedding)
            for candidate in candidates:
                if candidate.embedding:
                    sim = _emb.cosine_similarity(post_vec, json.loads(candidate.embedding))
                    if sim >= semantic_threshold:
                        post_lifecycle.mark_duplicate(post, candidate, round(sim * 100, 2))
                        return post

        post.status = RawPostStatus.READY.value
        return post

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import RawPost, RawPostStatus
from app.services.text_cleanup import clean_telegram_rss_text


# Косинус для paraphrase-multilingual-MiniLM: пересказ одной новости разными
# источниками обычно даёт 0.85–0.95, разные новости на одну тему — 0.75–0.85.
# 0.90 выбран консервативно: лучше пропустить повтор, чем склеить две разные новости.
DEFAULT_SEMANTIC_THRESHOLD = 0.90


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
                func.length(RawPost.normalized_text).between(
                    max(1, text_len // 2),
                    text_len * 2 + 1,
                ),
            ).limit(500)
        ).all()

        # Phase 2: rapidfuzz lexical similarity
        from app.services.prompt_settings import _get_setting
        try:
            threshold = int(_get_setting(db, "duplicate_threshold", str(self.threshold)))
        except (ValueError, TypeError):
            threshold = self.threshold

        for candidate in candidates:
            score = fuzz.token_set_ratio(post.normalized_text, candidate.normalized_text or "")
            if score >= threshold:
                post.status = RawPostStatus.DUPLICATE.value
                post.duplicate_of_id = candidate.id
                post.dedupe_score = float(score)
                return post

        # Phase 3: semantic similarity (requires fastembed)
        try:
            semantic_threshold = float(_get_setting(db, "semantic_threshold", str(DEFAULT_SEMANTIC_THRESHOLD)))
        except (ValueError, TypeError):
            semantic_threshold = DEFAULT_SEMANTIC_THRESHOLD

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
                        post.status = RawPostStatus.DUPLICATE.value
                        post.duplicate_of_id = candidate.id
                        post.dedupe_score = round(sim * 100, 2)
                        return post

        post.status = RawPostStatus.READY.value
        return post

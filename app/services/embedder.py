import json
import math
import logging

logger = logging.getLogger(__name__)

# paraphrase-multilingual-MiniLM-L12-v2: ~50 languages incl. Russian/English.
# ~220 MB downloaded once to ~/.cache/fastembed on first use.
_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_model = None
_load_attempted = False


def _load_model():
    global _model, _load_attempted
    if _load_attempted:
        return _model
    _load_attempted = True
    try:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name=_MODEL_NAME)
        logger.info("Embedder loaded: %s", _MODEL_NAME)
    except Exception as exc:
        logger.warning("Embedder unavailable (fastembed not installed or model error): %s", exc)
        _model = None
    return _model


def is_available() -> bool:
    return _load_model() is not None


def embed_text(text: str) -> list[float] | None:
    """Return embedding vector for text, or None if embedder unavailable."""
    model = _load_model()
    if model is None:
        return None
    try:
        vectors = list(model.embed([text[:2000]]))
        return [float(x) for x in vectors[0]]
    except Exception as exc:
        logger.debug("embed_text failed: %s", exc)
        return None


def embed_text_json(text: str) -> str | None:
    """Return JSON-serialised embedding, or None."""
    vec = embed_text(text)
    return json.dumps(vec) if vec is not None else None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def model_name() -> str:
    return _MODEL_NAME

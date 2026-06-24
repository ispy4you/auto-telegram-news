import re

TELEGRAM_RSS_BOILERPLATE_PATTERNS = [
    r"this media is not supported in your browser",
    r"view in telegram",
    r"watch in telegram",
    r"open in telegram",
    r"media is not supported",
]

MIN_USEFUL_TEXT_LEN = 25


def clean_telegram_rss_text(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    lowered = cleaned.lower()
    for pattern in TELEGRAM_RSS_BOILERPLATE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.strip("🫡").strip()
    return cleaned


def is_telegram_rss_garbage(text: str) -> bool:
    cleaned = clean_telegram_rss_text(text)
    if not cleaned:
        return True
    if len(cleaned) < MIN_USEFUL_TEXT_LEN:
        return True

    lowered = cleaned.lower()
    for pattern in TELEGRAM_RSS_BOILERPLATE_PATTERNS:
        if re.fullmatch(rf".*{pattern}.*", lowered):
            return True

    return False

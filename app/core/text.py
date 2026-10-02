import re
import unicodedata


def slugify(value: str) -> str:
    """Convert text to a URL-friendly slug: ``"Fotografía & Cine"`` -> ``"fotografia-cine"``."""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")

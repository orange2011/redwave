import re

# Strip any trailing (...) or [...] group that contains edition/remaster/deluxe keywords
_PAREN_RE = re.compile(
    r'\s*[\(\[][^\)\]]*\b(remaster(ed)?|deluxe|edition|bonus\s+tracks?|reissue|anniversary|expanded|special|collector|live)\b[^\)\]]*[\)\]]$',
    re.IGNORECASE,
)

# Strip bare trailing keywords after dash/space (e.g. "Dirt - Remastered")
_BARE_RE = re.compile(
    r'[\s\-–]+(\d{4}\s+)?(remaster(ed)?|deluxe|reissue)$',
    re.IGNORECASE,
)


def normalize_album(title: str) -> str:
    """Strip remaster/deluxe/edition suffixes for deduplication and search."""
    t = _PAREN_RE.sub("", title).strip()
    t = _BARE_RE.sub("", t).strip()
    return t


_PUNCT_RE = re.compile(r"[,.\-''\u2019]")

def normalize_artist(name: str) -> str:
    """Strip punctuation that varies between sources (comma, period, dash, apostrophe)."""
    return _PUNCT_RE.sub("", name).lower()

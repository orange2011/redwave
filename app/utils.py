import re
import unicodedata
from difflib import SequenceMatcher

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


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_UNICODE_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
_DISC_SUFFIX_RE = re.compile(r"\s*[\(\[]\s*disc\s+\d+\s*[\)\]]\s*$", re.IGNORECASE)
_GENERIC_ALBUM_TITLE_KEYS = {
    "album",
    "anthology",
    "best of",
    "collection",
    "complete collection",
    "deluxe",
    "ep",
    "greatest hits",
    "hits",
    "live",
    "single",
    "singles",
    "the best of",
    "vol 1",
    "vol 2",
    "volume 1",
    "volume 2",
}

def normalize_artist(name: str) -> str:
    """Normalize artist names across punctuation and common stylized symbols."""
    text = _fold_text(name).replace("&", " and ").replace("$", "s")
    tokens = _TOKEN_RE.findall(text)
    if tokens:
        return " ".join(tokens)
    unicode_text = unicodedata.normalize("NFKC", name or "").replace("&", " and ").casefold()
    return " ".join(_UNICODE_TOKEN_RE.findall(unicode_text))


def normalize_album_title(title: str) -> str:
    """Normalize album names for ownership checks without throwing away non-Latin text."""
    text = unicodedata.normalize("NFKC", normalize_album(title or ""))
    text = _DISC_SUFFIX_RE.sub("", text).replace("&", " and ").casefold()
    return " ".join(_UNICODE_TOKEN_RE.findall(text))


def collection_key(artist: str, album: str) -> str:
    return f"{normalize_artist(artist)}|{normalize_album_title(album)}"


def _specific_album_title(album_key: str) -> bool:
    compact = album_key.replace(" ", "")
    if album_key in _GENERIC_ALBUM_TITLE_KEYS:
        return False
    if album_key.startswith("greatest hits") or album_key.startswith("best of "):
        return False
    return len(album_key.split()) >= 2 and len(compact) >= 8


def build_collection_lookup(collection: list[dict]) -> dict:
    """Build exact, album-title, MBID, and cover lookup tables for ownership checks."""
    exact: dict[str, dict] = {}
    albums: dict[str, list[dict]] = {}
    mbids: dict[str, dict] = {}
    covers: dict[str, dict] = {}

    for item in collection:
        artist = item.get("artist", "")
        album = item.get("album", "")
        if not album:
            continue
        album_key = normalize_album_title(album)
        if artist:
            exact.setdefault(collection_key(artist, album), item)
        if album_key:
            albums.setdefault(album_key, []).append(item)
        mb_id = (item.get("mb_id") or "").strip()
        if mb_id:
            mbids.setdefault(mb_id, item)
        cover_url = (item.get("cover_url") or "").strip()
        if cover_url:
            covers.setdefault(cover_url, item)

    return {"exact": exact, "albums": albums, "mbids": mbids, "covers": covers}


def find_collection_album(
    artist: str,
    album: str,
    collection: list[dict] | None = None,
    *,
    lookup: dict | None = None,
    year: str | int | None = "",
    mb_id: str = "",
    cover_url: str = "",
) -> dict | None:
    """Return the owned album matching a metadata result.

    Exact artist+album wins first. If metadata uses a different script or romanization
    for the artist, fall back only when the album title is specific and unique, or when
    year/MBID/cover confirms the match.
    """
    if lookup is None:
        lookup = build_collection_lookup(collection or [])

    if artist and album:
        exact_match = lookup["exact"].get(collection_key(artist, album))
        if exact_match:
            wanted_year = str(year or "").strip()[:4]
            found_year = str(exact_match.get("year") or "").strip()[:4]
            if not wanted_year or (found_year and wanted_year == found_year):
                return exact_match

    mb_id = (mb_id or "").strip()
    if mb_id and mb_id in lookup["mbids"]:
        return lookup["mbids"][mb_id]

    cover_url = (cover_url or "").strip()
    if cover_url and cover_url in lookup["covers"]:
        return lookup["covers"][cover_url]

    album_key = normalize_album_title(album)
    candidates = lookup["albums"].get(album_key, [])
    if not candidates:
        return None

    wanted_year = str(year or "").strip()[:4]
    if wanted_year:
        for candidate in candidates:
            if str(candidate.get("year") or "").strip()[:4] == wanted_year:
                return candidate

    if len(candidates) == 1 and _specific_album_title(album_key):
        return candidates[0]

    return None


def _fold_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    return text.encode("ascii", "ignore").decode("ascii").lower()


def _search_variants(value: str) -> list[str]:
    folded = _fold_text(value)
    translated = folded.replace("&", " and ").replace("+", " plus ").replace("$", "s")
    stripped = folded.replace("&", " and ").replace("+", " plus ")
    variants = []
    for candidate in (translated, stripped):
        tokens = _TOKEN_RE.findall(candidate)
        if not tokens:
            continue
        variants.append(" ".join(tokens))
        variants.append("".join(tokens))

    seen = set()
    unique = []
    for variant in variants:
        if variant not in seen:
            seen.add(variant)
            unique.append(variant)
    return unique


def _partial_ratio(needle: str, haystack: str) -> float:
    if not needle or not haystack:
        return 0.0
    if len(needle) > len(haystack):
        needle, haystack = haystack, needle

    best = SequenceMatcher(None, needle, haystack).ratio()
    sizes = {len(needle)}
    if len(needle) + 1 <= len(haystack):
        sizes.add(len(needle) + 1)
    if len(needle) + 2 <= len(haystack):
        sizes.add(len(needle) + 2)

    for size in sizes:
        for start in range(0, len(haystack) - size + 1):
            best = max(best, SequenceMatcher(None, needle, haystack[start:start + size]).ratio())
    return best


def fuzzy_match_score(query: str, *values: str) -> int:
    """Return 0-100 score for forgiving local-library filtering."""
    query_variants = _search_variants(query)
    if not query_variants:
        return 0

    best = 0.0
    for value in values:
        symbol_boost = 8.0 if "$" in (query or "") and "$" in (value or "") else 0.0
        for q in query_variants:
            for candidate in _search_variants(value):
                if q == candidate:
                    best = max(best, 100.0)
                elif len(q) >= 3 and q in candidate:
                    best = max(best, min(100.0, 94.0 + symbol_boost))
                elif len(candidate) >= 3 and candidate in q:
                    best = max(best, min(100.0, 88.0 + symbol_boost))
                else:
                    best = max(best, min(100.0, (_partial_ratio(q, candidate) * 100.0) + symbol_boost))
    return int(round(best))


def fuzzy_match_threshold(query: str) -> int:
    compact = next((variant for variant in _search_variants(query) if " " not in variant), "")
    length = len(compact)
    if length <= 2:
        return 100
    if length <= 4:
        return 84
    if length <= 7:
        return 72
    return 66


def fuzzy_matches(query: str, *values: str) -> bool:
    if not (query or "").strip():
        return True
    return fuzzy_match_score(query, *values) >= fuzzy_match_threshold(query)

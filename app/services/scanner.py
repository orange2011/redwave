import os
import re
from pathlib import Path
from app.config import settings

MUSIC_DIR = Path(settings.music_dir) if settings.music_dir else Path(".")
AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aiff"}
COVER_NAMES = ["cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png"]

_YEAR_RE = re.compile(r"\((\d{4})\)|\[(\d{4})\]")
_LEADING_YEAR_RE = re.compile(r"^(\d{4})\s*[-–—]\s*")  # e.g. "1986 - Licensed To Ill"
_FORMAT_RE = re.compile(r"\s*[\[\(][^\]\)]*\b(flac|mp3|wav|24bit|hi-?res|lossless|320|kHz)\b[^\]\)]*[\]\)]\s*$", re.IGNORECASE)
_BRACKET_RE = re.compile(r"\s*\[[^\]]*\]\s*$")
_CURLY_RE = re.compile(r"\s*\{[^\}]*\}\s*$")  # Strip trailing {catalog} or {barcode} tags
_CATALOG_PAREN_RE = re.compile(r"\s*\([A-Z]{1,8}\s*[\dA-Z][\dA-Z\-]{3,}\)\s*$")  # Strip trailing (VINYL 0257743130), (CAT123), etc.

# Matches: " - ", " – ", " — ", "-", "- ", " -"
_DASH_RE = re.compile(r"\s*[\-–—]\s*")


def _has_audio(path: Path) -> bool:
    try:
        return any(f.suffix.lower() in AUDIO_EXTS for f in path.iterdir() if f.is_file())
    except PermissionError:
        return False


def _find_cover(path: Path) -> str | None:
    for name in COVER_NAMES:
        if (path / name).exists():
            return str(path / name)
    # Check subdirs (e.g. Disc 1)
    try:
        for sub in path.iterdir():
            if sub.is_dir():
                for name in COVER_NAMES:
                    if (sub / name).exists():
                        return str(sub / name)
    except PermissionError:
        pass
    return None


def _strip_format_tags(name: str) -> str:
    """Strip trailing [FLAC], (24bit), {catalog}, etc."""
    # Strip trailing {curly} tags (catalog numbers, barcodes) first
    for _ in range(3):
        m = _CURLY_RE.search(name)
        if m:
            name = name[:m.start()].strip()
        else:
            break
    # Strip trailing (VINYL xxxxx) / catalog number parens
    for _ in range(3):
        m = _CATALOG_PAREN_RE.search(name)
        if m:
            name = name[:m.start()].strip()
        else:
            break
    # Strip trailing [FORMAT] tags
    name = _FORMAT_RE.sub("", name).strip()
    # Strip remaining trailing [bracket] tags that don't contain useful info
    for _ in range(3):
        m = _BRACKET_RE.search(name)
        if m:
            name = name[:m.start()].strip()
        else:
            break
    return name


def _parse_folder(name: str) -> dict | None:
    """Parse folder name into artist/album/year."""
    clean = name.strip()

    # Extract year first
    year_match = _YEAR_RE.search(clean)
    year = next(g for g in (year_match.group(1), year_match.group(2)) if g) if year_match else ""
    if year_match:
        clean = (clean[:year_match.start()] + clean[year_match.end():]).strip()

    # Strip leading "1986 - " style year prefix
    lead_m = _LEADING_YEAR_RE.match(clean)
    if lead_m:
        if not year:
            year = lead_m.group(1)
        clean = clean[lead_m.end():]

    # Strip format tags
    clean = _strip_format_tags(clean)

    # Try splitting on " - " or " – " or " — " (spaced dashes)
    for sep in [" - ", " – ", " — "]:
        if sep in clean:
            artist, album = clean.split(sep, 1)
            album = album.strip().rstrip("-–— ")
            # Strip leading year from album part e.g. "Beastie Boys - 1986 - Licensed To Ill"
            lead_m2 = _LEADING_YEAR_RE.match(album)
            if lead_m2:
                if not year:
                    year = lead_m2.group(1)
                album = album[lead_m2.end():].strip().rstrip("-–— ")
            return {"artist": artist.strip(), "album": album, "year": year}

    # Try "Artist-Album" (no spaces, common in some releases)
    m = re.match(r"^(.+?)-(.+)$", clean)
    if m:
        artist, album = m.group(1).strip(), m.group(2).strip()
        # Sanity check: both parts should be at least 2 chars
        if len(artist) >= 2 and len(album) >= 2:
            return {"artist": artist, "album": album, "year": year}

    return None


def _scan_dir(root: Path, relative_to: Path) -> list[dict]:
    """Recursively scan a directory for album folders."""
    results = []
    try:
        entries = sorted(root.iterdir())
    except PermissionError:
        return results

    for entry in entries:
        if not entry.is_dir():
            continue

        rel = str(entry.relative_to(relative_to))

        if _has_audio(entry):
            # This folder contains audio directly
            mtime = entry.stat().st_mtime
            parsed = _parse_folder(entry.name)
            if parsed:
                cover = _find_cover(entry)
                results.append({**parsed, "folder": rel, "cover_file": cover, "added_at": mtime})
            elif root != MUSIC_DIR:
                # We're inside a subdirectory — treat parent as artist, this dir as album
                cover = _find_cover(entry)
                # Extract year and clean album name the same way _parse_folder does
                raw = entry.name
                year_m = _YEAR_RE.search(raw)
                year = next(g for g in (year_m.group(1), year_m.group(2)) if g) if year_m else ""
                if year_m:
                    raw = (raw[:year_m.start()] + raw[year_m.end():]).strip()
                # Strip leading "1986 - " style year prefix
                lead_m = _LEADING_YEAR_RE.match(raw)
                if lead_m:
                    if not year:
                        year = lead_m.group(1)
                    raw = raw[lead_m.end():]
                album_clean = _strip_format_tags(raw).strip().rstrip("-–— ")
                results.append({
                    "artist": root.name,
                    "album": album_clean,
                    "year": year,
                    "folder": rel,
                    "cover_file": cover,
                    "added_at": mtime,
                })
        else:
            # Check if it has audio in subdirs (multi-disc or nested artist)
            sub_audio = any(
                _has_audio(sub) for sub in entry.iterdir() if sub.is_dir()
            ) if entry.is_dir() else False

            parsed = _parse_folder(entry.name)
            if sub_audio and parsed:
                # Multi-disc album: treat parent folder as the album
                cover = _find_cover(entry)
                mtime = entry.stat().st_mtime
                results.append({**parsed, "folder": rel, "cover_file": cover, "added_at": mtime})
            elif not parsed:
                # Might be an artist folder — recurse one level
                results.extend(_scan_dir(entry, relative_to))

    return results


def scan_collection() -> list[dict]:
    if not MUSIC_DIR.exists():
        return []
    return _scan_dir(MUSIC_DIR, MUSIC_DIR)


_cache: list[dict] | None = None


def get_collection() -> list[dict]:
    global _cache
    if _cache is None:
        _cache = scan_collection()
    return _cache


def refresh_collection() -> list[dict]:
    global _cache
    _cache = scan_collection()
    return _cache

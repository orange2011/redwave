import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _empty_info(source: str = "unknown") -> dict:
    return {
        "version": "dev",
        "revision": "",
        "commit": "",
        "short_commit": "",
        "branch": "",
        "source": source,
    }


def _version_from_parts(revision: str = "", short_commit: str = "") -> str:
    revision = str(revision or "").strip()
    short_commit = str(short_commit or "").strip()
    if revision and short_commit:
        return f"r{revision}.{short_commit}"
    return short_commit or "dev"


def _git_value(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
    except Exception:
        return ""


def _file_value(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    revision = str(data.get("revision") or "").strip()
    short_commit = str(data.get("short_commit") or "").strip()
    version = str(data.get("version") or "").strip() or _version_from_parts(revision, short_commit)
    return {
        **_empty_info("file"),
        "version": version,
        "revision": revision,
        "commit": str(data.get("commit") or "").strip(),
        "short_commit": short_commit,
        "branch": str(data.get("branch") or "").strip(),
        "source": str(data.get("source") or "file").strip() or "file",
    }


@lru_cache(maxsize=1)
def get_app_version() -> dict:
    env_version = os.getenv("REDWAVE_VERSION", "").strip()
    if env_version:
        return {
            **_empty_info("env"),
            "version": env_version,
            "revision": os.getenv("REDWAVE_REVISION", "").strip(),
            "commit": os.getenv("REDWAVE_COMMIT", "").strip(),
            "short_commit": os.getenv("REDWAVE_SHORT_COMMIT", "").strip(),
            "branch": os.getenv("REDWAVE_BRANCH", "").strip(),
        }

    version_file = Path(os.getenv("REDWAVE_VERSION_FILE", "redwave-version.json"))
    if not version_file.is_absolute():
        version_file = ROOT / version_file
    file_info = _file_value(version_file)
    if file_info:
        return file_info

    revision = _git_value("rev-list", "--count", "HEAD")
    commit = _git_value("rev-parse", "HEAD")
    short_commit = _git_value("rev-parse", "--short=7", "HEAD")
    branch = _git_value("rev-parse", "--abbrev-ref", "HEAD")
    if short_commit:
        return {
            **_empty_info("git"),
            "version": _version_from_parts(revision, short_commit),
            "revision": revision,
            "commit": commit,
            "short_commit": short_commit,
            "branch": branch,
        }

    return _empty_info()

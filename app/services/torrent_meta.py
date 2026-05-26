from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any


class BencodeError(ValueError):
    pass


@dataclass(frozen=True)
class TorrentManifest:
    name: str
    files: tuple[tuple[str, int], ...]
    total_size: int
    piece_length: int
    pieces_sha1: str

    @property
    def file_count(self) -> int:
        return len(self.files)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "files": [{"path": path, "size": size} for path, size in self.files],
            "total_size": self.total_size,
            "piece_length": self.piece_length,
            "pieces_sha1": self.pieces_sha1,
            "file_count": self.file_count,
        }

    @classmethod
    def from_dict(cls, value: dict | None) -> "TorrentManifest | None":
        if not isinstance(value, dict):
            return None
        files = value.get("files") or []
        return cls(
            name=str(value.get("name") or ""),
            files=tuple(
                (str(item.get("path") or ""), int(item.get("size") or 0))
                for item in files
                if isinstance(item, dict)
            ),
            total_size=int(value.get("total_size") or 0),
            piece_length=int(value.get("piece_length") or 0),
            pieces_sha1=str(value.get("pieces_sha1") or ""),
        )


def _decode_bytes(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


def _bdecode(data: bytes, index: int = 0) -> tuple[Any, int]:
    if index >= len(data):
        raise BencodeError("unexpected end of bencode data")

    marker = data[index:index + 1]
    if marker == b"i":
        end = data.find(b"e", index)
        if end < 0:
            raise BencodeError("unterminated integer")
        return int(data[index + 1:end]), end + 1

    if marker == b"l":
        index += 1
        result = []
        while data[index:index + 1] != b"e":
            item, index = _bdecode(data, index)
            result.append(item)
        return result, index + 1

    if marker == b"d":
        index += 1
        result = {}
        while data[index:index + 1] != b"e":
            key, index = _bdecode(data, index)
            value, index = _bdecode(data, index)
            result[key] = value
        return result, index + 1

    if marker.isdigit():
        colon = data.find(b":", index)
        if colon < 0:
            raise BencodeError("unterminated byte string length")
        length = int(data[index:colon])
        start = colon + 1
        end = start + length
        if end > len(data):
            raise BencodeError("byte string length exceeds payload")
        return data[start:end], end

    raise BencodeError(f"unknown bencode marker {marker!r}")


def parse_torrent_manifest(content: bytes) -> TorrentManifest:
    data, index = _bdecode(content)
    if index != len(content):
        raise BencodeError("trailing bencode data")
    if not isinstance(data, dict) or b"info" not in data:
        raise BencodeError("missing torrent info dictionary")

    info = data[b"info"]
    if not isinstance(info, dict):
        raise BencodeError("invalid torrent info dictionary")

    name = _decode_bytes(info.get(b"name") or b"")
    piece_length = int(info.get(b"piece length") or 0)
    pieces = info.get(b"pieces") or b""
    if not isinstance(pieces, bytes):
        raise BencodeError("invalid torrent pieces")

    files: list[tuple[str, int]] = []
    if b"files" in info:
        for item in info.get(b"files") or []:
            if not isinstance(item, dict):
                continue
            path_parts = item.get(b"path") or []
            path = "/".join(_decode_bytes(part) for part in path_parts if isinstance(part, bytes))
            files.append((path, int(item.get(b"length") or 0)))
    else:
        files.append((name, int(info.get(b"length") or 0)))

    return TorrentManifest(
        name=name,
        files=tuple(files),
        total_size=sum(size for _, size in files),
        piece_length=piece_length,
        pieces_sha1=hashlib.sha1(pieces).hexdigest(),
    )


def manifests_payload_compatible(left: TorrentManifest | None, right: TorrentManifest | None) -> bool:
    if not left or not right:
        return False
    return (
        left.total_size == right.total_size
        and left.piece_length == right.piece_length
        and left.pieces_sha1 == right.pieces_sha1
        and left.files == right.files
    )

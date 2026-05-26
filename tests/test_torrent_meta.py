import unittest

from app.services.torrent_meta import manifests_payload_compatible, parse_torrent_manifest


def _bstr(value: str | bytes) -> bytes:
    data = value if isinstance(value, bytes) else value.encode("utf-8")
    return str(len(data)).encode("ascii") + b":" + data


def _torrent_bytes(name: str, files: list[tuple[str, int]], pieces: bytes = b"x" * 20) -> bytes:
    encoded_files = b"".join(
        b"d6:lengthi" + str(size).encode("ascii") + b"e4:pathl" + _bstr(path) + b"ee"
        for path, size in files
    )
    return (
        b"d4:infod5:filesl" + encoded_files + b"e"
        + b"4:name" + _bstr(name)
        + b"12:piece lengthi16384e"
        + b"6:pieces" + _bstr(pieces)
        + b"ee"
    )


class TorrentMetaTests(unittest.TestCase):
    def test_manifest_ignores_root_name_but_requires_same_files_and_pieces(self):
        left = parse_torrent_manifest(_torrent_bytes("RED Folder", [
            ("01 Track.flac", 100),
            ("02 Track.flac", 200),
        ], pieces=b"a" * 20))
        right = parse_torrent_manifest(_torrent_bytes("OPS Folder", [
            ("01 Track.flac", 100),
            ("02 Track.flac", 200),
        ], pieces=b"a" * 20))

        self.assertTrue(manifests_payload_compatible(left, right))

    def test_manifest_rejects_same_total_size_with_different_files(self):
        left = parse_torrent_manifest(_torrent_bytes("RED Folder", [
            ("01 Track.flac", 100),
            ("02 Track.flac", 200),
        ], pieces=b"a" * 20))
        right = parse_torrent_manifest(_torrent_bytes("OPS Folder", [
            ("01 Other.flac", 300),
        ], pieces=b"a" * 20))

        self.assertFalse(manifests_payload_compatible(left, right))


if __name__ == "__main__":
    unittest.main()

import unittest

from app.services.torrent_meta import compare_torrent_payloads, manifests_payload_compatible, parse_torrent_manifest


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

    def test_payload_match_allows_safe_path_mapping(self):
        left = parse_torrent_manifest(_torrent_bytes("RED Folder", [
            ("01 Song.flac", 100),
            ("02 Song.flac", 200),
        ], pieces=b"c" * 20))
        right = parse_torrent_manifest(_torrent_bytes("OPS Folder", [
            ("01 - Song.flac", 100),
            ("02 - Song.flac", 200),
        ], pieces=b"c" * 20))

        match = compare_torrent_payloads(left, right)

        self.assertTrue(match.compatible)
        self.assertEqual(match.match_mode, "mapped-paths")
        self.assertEqual(match.rename_map, {
            "01 - Song.flac": "01 Song.flac",
            "02 - Song.flac": "02 Song.flac",
        })

    def test_payload_match_rejects_different_file_boundaries(self):
        left = parse_torrent_manifest(_torrent_bytes("RED Folder", [
            ("01 Track.flac", 100),
            ("02 Track.flac", 200),
        ], pieces=b"d" * 20))
        right = parse_torrent_manifest(_torrent_bytes("OPS Folder", [
            ("01 Other.flac", 200),
            ("02 Other.flac", 100),
        ], pieces=b"d" * 20))

        match = compare_torrent_payloads(left, right)

        self.assertFalse(match.compatible)
        self.assertEqual(match.reason, "file boundaries differ")


if __name__ == "__main__":
    unittest.main()

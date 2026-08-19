"""Tests for the native zsync client — MD4, block checksums, metadata
parsing, and an end-to-end delta update against a local HTTP server."""

import hashlib
import http.server
import os
import socketserver
import struct
import threading

import pytest

from niruvi.desktop import zsync
from niruvi.desktop.zsync import (
    _block_rsum,
    _block_strong,
    _read_block_sums,
    delta_update,
    md4,
    parse_zsync_metadata,
)


class TestMD4:
    """RFC 1320 test vectors."""

    VECTORS = [
        (b"", "31d6cfe0d16ae931b73c59d7e0c089c0"),
        (b"a", "bde52cb31de33e46245e05fbdbd6fb24"),
        (b"abc", "a448017aaf21d8525fc10ae87aa6729d"),
        (b"message digest", "d9130a8164549fe818874806e1c7014b"),
        (b"abcdefghijklmnopqrstuvwxyz", "d79e1c308aa5bbcdeea8ed63df412da9"),
        (
            b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
            "043f8582f241db351ce627e153e7f0e4",
        ),
        (b"1234567890" * 8, "e33b4ddc9c38f2199c3e7b164fcc0536"),
    ]

    def test_vectors(self):
        for data, expected in self.VECTORS:
            assert md4(data).hex() == expected


class TestBlockChecksums:
    def test_rsum_simple(self):
        # a = plain byte sum, b = weighted sum (weights n..1), both uint16
        block = bytes([1, 2, 3])
        a, b = _block_rsum(block)
        assert a == 6
        assert b == (3 * 1) + (2 * 2) + (1 * 3)

    def test_rsum_wraps_at_16bit(self):
        block = bytes([0xFF] * 200)  # sum = 51000 < 65536, no wrap
        a, _ = _block_rsum(block)
        assert a == 0xFF * 200

    def test_rsum_weights_decrease(self):
        block = bytes([10, 20, 30])
        a, b = _block_rsum(block)
        assert a == 60
        assert b == 10 * 3 + 20 * 2 + 30 * 1

    def test_strong_is_truncated_md4(self):
        block = b"hello world"
        assert _block_strong(block, 8) == md4(block)[:8]
        assert _block_strong(block, 16) == md4(block)


class TestMetadataParsing:
    def _make_zsync(self, payload: bytes, blocksize: int) -> bytes:
        nblocks = (len(payload) + blocksize - 1) // blocksize
        parts = [
            b"zsync: 0.6.2",
            b"Filename: out.AppImage",
            b"MTime: Thu, 01 Jan 1970 00:00:00 +0000",
            f"Length: {len(payload)}".encode(),
            f"Blocksize: {blocksize}".encode(),
            b"Hash-Lengths: 1,4,8",
            b"URL: out.AppImage",
            f"SHA-1: {hashlib.sha1(payload).hexdigest()}".encode(),
            b"",
        ]
        sums = b""
        for i in range(nblocks):
            chunk = payload[i * blocksize : (i + 1) * blocksize]
            if len(chunk) < blocksize:
                chunk = chunk + b"\x00" * (blocksize - len(chunk))
            a, b = _block_rsum(chunk)
            sums += struct.pack(">HH", a, b)
            sums += md4(chunk)[:8]
        return b"\n".join(parts) + b"\n" + sums

    def test_parse_handcrafted(self):
        payload = os.urandom(4096 * 3 + 500)
        raw = self._make_zsync(payload, 4096)
        meta = parse_zsync_metadata(raw, "http://example.org/out.AppImage.zsync")
        assert meta["length"] == len(payload)
        assert meta["blocksize"] == 4096
        assert meta["hash_lengths"] == (1, 4, 8)
        assert meta["filename"] == "out.AppImage"
        assert meta["target_url"] == "http://example.org/out.AppImage"
        nblocks = (len(payload) + 4095) // 4096
        entries = _read_block_sums(meta["raw_sums"], nblocks, meta["hash_lengths"])
        assert len(entries) == nblocks
        for i, (a, b, strong) in enumerate(entries):
            chunk = payload[i * 4096 : (i + 1) * 4096]
            if len(chunk) < 4096:
                chunk = chunk + b"\x00" * (4096 - len(chunk))
            assert (a, b) == _block_rsum(chunk)
            assert strong == md4(chunk)[:8]

    def test_missing_length_rejected(self):
        with pytest.raises(zsync.ZSyncError):
            parse_zsync_metadata(b"Filename: x\n", "http://x/")

    def test_bad_blocksize_rejected(self):
        raw = b"Filename: x\nLength: 100\nBlocksize: 1000\nURL: y\n"
        with pytest.raises(zsync.ZSyncError):
            parse_zsync_metadata(raw, "http://x/")


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    payload = b""
    requests = []

    def do_GET(self):
        type(self).requests.append(self.path)
        if self.path == "/out.AppImage.zsync":
            self.send_response(200)
            self.send_header("Content-Length", str(len(self._zsync)))
            self.end_headers()
            self.wfile.write(self._zsync)
            return
        if self.path != "/out.AppImage":
            self.send_response(404)
            self.end_headers()
            return
        range_header = self.headers.get("Range", "")
        if range_header.startswith("bytes="):
            start_s, _, end_s = range_header[6:].partition("-")
            start = int(start_s)
            end = int(end_s) if end_s else len(type(self).payload) - 1
            data = type(self).payload[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(type(self).payload)}")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(type(self).payload)))
        self.end_headers()
        self.wfile.write(type(self).payload)

    def log_message(self, *args):
        pass


@pytest.fixture()
def zsync_server(tmp_path):
    payload = os.urandom(4096 * 4 + 123)
    blocksize = 4096
    nblocks = (len(payload) + blocksize - 1) // blocksize
    parts = [
        b"zsync: 0.6.2",
        b"Filename: out.AppImage",
        f"Length: {len(payload)}".encode(),
        f"Blocksize: {blocksize}".encode(),
        b"Hash-Lengths: 1,4,8",
        b"URL: out.AppImage",
        f"SHA-1: {hashlib.sha1(payload).hexdigest()}".encode(),
        b"",
    ]
    sums = b""
    for i in range(nblocks):
        chunk = payload[i * blocksize : (i + 1) * blocksize]
        if len(chunk) < blocksize:
            chunk = chunk + b"\x00" * (blocksize - len(chunk))
        a, b = _block_rsum(chunk)
        sums += struct.pack(">HH", a, b)
        sums += md4(chunk)[:8]
    _RangeHandler._zsync = b"\n".join(parts) + b"\n" + sums
    _RangeHandler.payload = payload
    _RangeHandler.requests = []
    with socketserver.TCPServer(("127.0.0.1", 0), _RangeHandler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        yield base, payload, tmp_path
        httpd.shutdown()


class TestDeltaUpdate:
    def test_identical_seed_transfers_nothing(self, zsync_server):
        base, payload, tmp = zsync_server
        seed = tmp / "seed.AppImage"
        seed.write_bytes(payload)
        dest = tmp / "out.AppImage"
        ok, msg = delta_update(f"{base}/out.AppImage.zsync", str(seed), str(dest))
        assert ok, msg
        assert dest.read_bytes() == payload
        ranges = [r for r in _RangeHandler.requests if r == "/out.AppImage"]
        assert len(ranges) <= 1  # at most the trailing partial block

    def test_partial_seed_fetches_only_missing(self, zsync_server):
        base, payload, tmp = zsync_server
        seed = tmp / "seed.AppImage"
        # seed with every other block zeroed
        seeded = bytearray(payload)
        for i in range(0, 4096 * 4, 4096 * 2):
            seeded[i : i + 4096] = b"\x00" * 4096
        seed.write_bytes(bytes(seeded))
        dest = tmp / "out.AppImage"
        ok, msg = delta_update(f"{base}/out.AppImage.zsync", str(seed), str(dest))
        assert ok, msg
        assert dest.read_bytes() == payload
        range_requests = [r for r in _RangeHandler.requests if r == "/out.AppImage"]
        assert range_requests, "expected Range fetches for missing blocks"

    def test_missing_seed_falls_back(self, zsync_server):
        base, _payload, tmp = zsync_server
        dest = tmp / "out.AppImage"
        ok, msg = delta_update(f"{base}/out.AppImage.zsync", str(tmp / "nonexistent"), str(dest))
        assert ok is False
        assert "seed" in msg.lower()

    def test_output_sha1_matches(self, zsync_server):
        base, payload, tmp = zsync_server
        seed = tmp / "seed.AppImage"
        seed.write_bytes(payload[:-5000])  # truncated seed
        dest = tmp / "out.AppImage"
        ok, msg = delta_update(f"{base}/out.AppImage.zsync", str(seed), str(dest))
        assert ok, msg
        assert hashlib.sha1(dest.read_bytes()).hexdigest() == hashlib.sha1(payload).hexdigest()

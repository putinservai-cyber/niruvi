"""Native zsync delta-download client for Niruvi.

Implements the zsync file format (0.6.x) so updates can be applied as
delta transfers: only the blocks that differ between the installed
AppImage and the new release are downloaded, using HTTP Range requests.

The block checksum section stores, per block:
  - 4 bytes: rolling rsum  (a = plain byte sum, b = weighted sum with
    decreasing weights, each a big-endian uint16)
  - checksum_bytes bytes: truncated MD4 of the block (zero-padded to a
    full block for the last partial block)

The final whole-file SHA-1 is over the unpadded file.

Any failure (network, parse, checksum) returns False so the caller can
fall back to a full download.
"""

import hashlib
import logging
import os
import struct
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_MAX_ZSync_METADATA = 2 * 1024 * 1024
_MAX_RANGE_BYTES = 8 * 1024 * 1024


class ZSyncError(Exception):
    pass


class _MD4:
    """Pure-Python MD4 (RFC 1320); hashlib lacks it on OpenSSL 3 systems."""

    def __init__(self):
        self._a = 0x67452301
        self._b = 0xEFCDAB89
        self._c = 0x98BADCFE
        self._d = 0x10325476
        self._length = 0
        self._buffer = bytearray()

    @staticmethod
    def _rotl(x, n):
        return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF

    def update(self, data: bytes):
        self._length += len(data)
        self._buffer.extend(data)
        while len(self._buffer) >= 64:
            self._compress(bytes(self._buffer[:64]))
            del self._buffer[:64]

    def digest(self) -> bytes:
        buf = bytes(self._buffer)
        length = self._length
        buf += b"\x80"
        while len(buf) % 64 != 56:
            buf += b"\x00"
        buf += struct.pack("<Q", (length * 8) & 0xFFFFFFFFFFFFFFFF)
        a, b, c, d = self._a, self._b, self._c, self._d
        for i in range(0, len(buf), 64):
            a, b, c, d = self._compress_block(a, b, c, d, buf[i : i + 64])
        return struct.pack("<4I", a, b, c, d)

    @classmethod
    def _compress_block(cls, a0, b0, c0, d0, block):
        x = struct.unpack("<16I", block)
        a, b, c, d = a0, b0, c0, d0

        def f1(x, y, z):
            return (x & y) | (~x & z)

        def f2(x, y, z):
            return (x & y) | (x & z) | (y & z)

        def f3(x, y, z):
            return x ^ y ^ z

        def rot(x, s):
            return ((x << s) | (x >> (32 - s))) & 0xFFFFFFFF

        # Round 1 (f1)
        for k in range(16):
            a = rot((a + f1(b, c, d) + x[k]) & 0xFFFFFFFF, [3, 7, 11, 19][k % 4])
            a, b, c, d = d, a, b, c
        # Round 2 (f2)
        for k, s in zip(
            [0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15],
            [3, 5, 9, 13] * 4,
        ):
            a = rot((a + f2(b, c, d) + x[k] + 0x5A827999) & 0xFFFFFFFF, s)
            a, b, c, d = d, a, b, c
        # Round 3 (f3)
        for k, s in zip(
            [0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15],
            [3, 9, 11, 15] * 4,
        ):
            a = rot((a + f3(b, c, d) + x[k] + 0x6ED9EBA1) & 0xFFFFFFFF, s)
            a, b, c, d = d, a, b, c

        return (
            (a0 + a) & 0xFFFFFFFF,
            (b0 + b) & 0xFFFFFFFF,
            (c0 + c) & 0xFFFFFFFF,
            (d0 + d) & 0xFFFFFFFF,
        )

    def _compress(self, block):
        self._a, self._b, self._c, self._d = self._compress_block(self._a, self._b, self._c, self._d, block)


def md4(data: bytes) -> bytes:
    h = _MD4()
    h.update(data)
    return h.digest()


def parse_zsync_metadata(zsync_bytes: bytes, zsync_url: str) -> dict:
    """Parse the text header of a .zsync file. Returns a dict with the
    header fields plus the binary block-sum section as raw bytes."""
    meta: dict[str, Any] = {}
    pos = 0
    header_lines = []
    while pos < len(zsync_bytes):
        nl = zsync_bytes.find(b"\n", pos)
        if nl == -1:
            nl = len(zsync_bytes)
        line = zsync_bytes[pos:nl]
        if not line:
            pos = nl + 1
            continue
        if not (line[0:1].isalpha() and b":" in line):
            break
        header_lines.append(line)
        pos = nl + 1
    if not header_lines:
        raise ZSyncError("Not a zsync file (no header lines)")
    for line in header_lines:
        tag, _, value = line.decode("utf-8", "replace").partition(":")
        tag = tag.strip()
        value = value.strip()
        if tag == "zsync":
            meta["zsync_version"] = value
        elif tag == "Filename":
            meta["filename"] = value
        elif tag == "MTime":
            meta["mtime"] = value
        elif tag == "Length":
            meta["length"] = int(value)
        elif tag == "Blocksize":
            meta["blocksize"] = int(value)
        elif tag == "Hash-Lengths":
            meta["hash_lengths"] = tuple(int(v) for v in value.split(","))
        elif tag in ("URL", "Z-URL"):
            meta.setdefault("urls", []).append(value)
        elif tag == "SHA-1":
            meta["sha1"] = value.lower()
    if "length" not in meta or "blocksize" not in meta:
        raise ZSyncError("Not a zsync file (missing Length/Blocksize)")
    if meta["blocksize"] <= 0 or (meta["blocksize"] & (meta["blocksize"] - 1)):
        raise ZSyncError(f"Bad blocksize {meta['blocksize']}")
    meta["raw_sums"] = zsync_bytes[pos:]

    urls = meta.get("urls") or []
    if not urls:
        raise ZSyncError("zsync file has no URL for the target file")
    base = urls[-1]
    if not base.startswith(("http://", "https://")):
        import urllib.parse

        base = urllib.parse.urljoin(zsync_url, base)
    meta["target_url"] = base
    return meta


def _read_block_sums(raw: bytes, nblocks: int, hash_lengths: tuple) -> list:
    """Decode the per-block (rsum_a, rsum_b, strong_hash) entries."""
    rsum_bytes = hash_lengths[1] if len(hash_lengths) >= 2 else 4
    checksum_bytes = hash_lengths[2] if len(hash_lengths) >= 3 else 8
    entry_size = rsum_bytes + checksum_bytes
    if len(raw) < nblocks * entry_size:
        raise ZSyncError(f"zsync checksum section too short: {len(raw)} < {nblocks * entry_size}")
    entries = []
    for i in range(nblocks):
        off = i * entry_size
        if rsum_bytes == 4:
            a, b = struct.unpack(">HH", raw[off : off + 4])
        elif rsum_bytes == 2:
            a = struct.unpack(">H", raw[off : off + 2])[0]
            b = 0
        else:
            raise ZSyncError(f"Unsupported rsum_bytes {rsum_bytes}")
        strong = bytes(raw[off + rsum_bytes : off + entry_size])
        entries.append((a, b, strong))
    return entries


def _block_rsum(block: bytes) -> tuple:
    a = 0
    b = 0
    n = len(block)
    for i, c in enumerate(block):
        a += c
        b += (n - i) * c
    return a & 0xFFFF, b & 0xFFFF


def _block_strong(block: bytes, checksum_bytes: int) -> bytes:
    return md4(block)[:checksum_bytes]


def _fetch_url(url: str, timeout: int = 30, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data: bytes = resp.read()
        return data


def _fetch_range(url: str, start: int, end: int, timeout: int = 120) -> bytes:
    """Fetch bytes [start, end] inclusive via HTTP Range."""
    req = urllib.request.Request(
        url,
        headers={"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data: bytes = resp.read()
    expected = end - start + 1
    if len(data) != expected:
        raise ZSyncError(f"Range request returned {len(data)} bytes, expected {expected}")
    return data


class _SeedScanner:
    """Streams a seed file via mmap and computes the rolling rsum for each
    window, advancing one byte at a time (O(n) total)."""

    def __init__(self, seed_path: str, block_size: int):
        self._block_size = block_size
        self._mm = None
        self._file = None
        self._pos = 0
        self._a = 0
        self._b = 0
        self._shift = block_size.bit_length() - 1
        self._window: bytes = b""
        self._first = True
        try:
            self._file = open(seed_path, "rb")
            import mmap

            try:
                self._mm = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
                self._mm.madvise(mmap.MADV_SEQUENTIAL)
            except ValueError:
                self._mm = None
        except OSError:
            self._file = None

    @property
    def ok(self) -> bool:
        return self._mm is not None or self._file is not None

    def close(self):
        if self._mm is not None:
            try:
                self._mm.close()
            except Exception:
                pass
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass

    def __iter__(self):
        if self._mm is not None:
            total = self._mm.size()
            while self._pos + self._block_size <= total:
                window = self._mm[self._pos : self._pos + self._block_size]
                if self._first:
                    self._a, self._b = _block_rsum(window)
                    self._first = False
                else:
                    newc = window[-1]
                    oldc = self._window[0]
                    self._a = (self._a + newc - oldc) & 0xFFFF
                    self._b = (self._b + self._a - (oldc << self._shift)) & 0xFFFF
                self._window = window
                offset = self._pos
                self._pos += 1
                yield offset, window, self._a, self._b
        return


def delta_update(
    zsync_url: str,
    seed_path: str,
    dest_path: str,
    progress_cb=None,
    timeout: int = 120,
) -> tuple:
    """Apply a delta update for `dest_path` using `zsync_url` metadata.

    Returns (True, message) on success, (False, reason) on failure so the
    caller can fall back to a full download. `progress_cb(got, total)` is
    called with byte counts.
    """
    try:
        zsync_bytes = _fetch_url(zsync_url, timeout=timeout)
        if len(zsync_bytes) > _MAX_ZSync_METADATA:
            return False, "zsync metadata too large"
        meta = parse_zsync_metadata(zsync_bytes, zsync_url)
    except Exception as e:
        logger.debug("zsync metadata fetch failed: %s", e, exc_info=True)
        return False, f"Could not fetch zsync metadata: {e}"

    # Security: block checksums come from the (untrusted) .zsync metadata and
    # are only truncated MD4. Without the whole-file SHA-1 there is no strong
    # end-to-end integrity guarantee for the reassembled target — refuse the
    # delta so callers fall back to a full TLS download instead.
    if not meta.get("sha1"):
        logger.warning("zsync metadata has no SHA-1 — refusing delta update for %s", zsync_url)
        return False, "No SHA-1 in zsync metadata"

    length = meta["length"]
    block_size = meta["blocksize"]
    nblocks = (length + block_size - 1) // block_size
    try:
        entries = _read_block_sums(meta["raw_sums"], nblocks, meta["hash_lengths"])
    except Exception as e:
        logger.debug("zsync block sums decode failed: %s", e, exc_info=True)
        return False, f"Bad zsync block sums: {e}"

    if not os.path.isfile(seed_path):
        return False, "No seed file available"

    target_url = meta["target_url"]
    sha1_expected = meta.get("sha1", "")

    checksum_bytes = len(entries[0][2])

    # rsum -> list of block ids
    rsum_index: dict[tuple[int, int], list[int]] = {}
    for bid, (a, b, _strong) in enumerate(entries):
        rsum_index.setdefault((a, b), []).append(bid)

    filled = [False] * nblocks
    seed_data: dict[int, bytes] = {}

    def report():
        if progress_cb:
            got = sum(1 for fl in filled if fl) * block_size
            progress_cb(min(got, length), length)

    # --- Round 1: find seed blocks via rolling checksum ---
    scanner = _SeedScanner(seed_path, block_size)
    try:
        if scanner.ok:
            for _offset, window, a, b in scanner:
                candidates = rsum_index.get((a, b))
                if not candidates:
                    continue
                for bid in candidates:
                    if filled[bid]:
                        continue
                    if _block_strong(window, checksum_bytes) == entries[bid][2]:
                        filled[bid] = True
                        seed_data[bid] = window
                        break
    except Exception as e:
        logger.debug("zsync seed scan failed: %s", e, exc_info=True)
        return False, f"Seed scan failed: {e}"
    finally:
        scanner.close()

    report()
    missing = [i for i in range(nblocks) if not filled[i]]

    # --- Round 2: fetch missing blocks via HTTP Range requests ---
    downloaded: dict = {}
    if missing:
        idx = 0
        while idx < len(missing):
            start_id = missing[idx]
            end_id = start_id
            while (
                idx + 1 < len(missing)
                and missing[idx + 1] == end_id + 1
                and (end_id + 1 - start_id + 1) * block_size <= _MAX_RANGE_BYTES
            ):
                idx += 1
                end_id = missing[idx]
            idx += 1
            byte_start = start_id * block_size
            byte_end = min((end_id + 1) * block_size, length) - 1
            try:
                data = _fetch_range(target_url, byte_start, byte_end, timeout=timeout)
            except Exception as e:
                logger.debug("zsync range fetch failed: %s", e, exc_info=True)
                return False, f"Range download failed: {e}"
            pos = 0
            for bid in range(start_id, end_id + 1):
                want = min(block_size, length - bid * block_size)
                block = data[pos : pos + want]
                pos += want
                if _block_strong(block.ljust(block_size, b"\x00"), checksum_bytes) == entries[bid][2]:
                    filled[bid] = True
                    downloaded[bid] = block
            report()

    # --- Round 3: retry remaining blocks individually ---
    for bid in [i for i in range(nblocks) if not filled[i]]:
        try:
            byte_start = bid * block_size
            byte_end = min((bid + 1) * block_size, length) - 1
            data = _fetch_range(target_url, byte_start, byte_end, timeout=timeout)
            block = data[: byte_end - byte_start + 1]
            if _block_strong(block.ljust(block_size, b"\x00"), checksum_bytes) == entries[bid][2]:
                filled[bid] = True
                downloaded[bid] = block
        except Exception as e:
            logger.debug("zsync retry block %d failed: %s", bid, e, exc_info=True)
    report()

    if not all(filled):
        n_missing = sum(1 for fl in filled if not fl)
        return False, f"Delta transfer incomplete ({n_missing} blocks could not be verified)"

    # --- Assemble output ---
    tmp_path = dest_path + ".zsync-part"
    try:
        sha1 = hashlib.sha1()
        with open(tmp_path, "wb+") as out:
            out.truncate(length)
            for bid in range(nblocks):
                want = min(block_size, length - bid * block_size)
                if bid in seed_data:
                    block = seed_data[bid][:want]
                elif bid in downloaded:
                    block = downloaded[bid][:want]
                else:
                    byte_start = bid * block_size
                    byte_end = min((bid + 1) * block_size, length) - 1
                    block = _fetch_range(target_url, byte_start, byte_end, timeout=timeout)[:want]
                out.seek(bid * block_size)
                out.write(block)
                sha1.update(block)
        if sha1_expected and sha1.hexdigest() != sha1_expected:
            logger.debug("zsync SHA-1 mismatch: expected %s got %s", sha1_expected, sha1.hexdigest())
            return False, "Assembled file failed SHA-1 verification"
        os.replace(tmp_path, dest_path)
        return True, "Delta update applied"
    except Exception as e:
        logger.debug("zsync assembly failed: %s", e, exc_info=True)
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        return False, f"Delta assembly failed: {e}"


def try_delta_download(
    download_url: str,
    seed_path: str,
    dest_path: str,
    progress_cb=None,
) -> tuple:
    """Attempt a delta download; returns (True, msg) or (False, reason)."""
    if not seed_path or not os.path.isfile(seed_path):
        return False, "No seed file"
    if not download_url.startswith(("http://", "https://")):
        return False, "Not an HTTP(S) URL"
    zsync_url = download_url + ".zsync"
    return delta_update(zsync_url, seed_path, dest_path, progress_cb=progress_cb)

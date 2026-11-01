"""Minimal QR Code encoder (QR Model 2, byte mode) — no third-party dependency.

Renders a short payload (a UPI intent link) as a boolean module matrix that
``QPainter`` can draw. Implements ISO/IEC 18004: Reed-Solomon error correction
over GF(256), the standard version/level block layout, data masking with the
four penalty rules, and the BCH-protected format information.

Scope is deliberately narrow — byte mode, versions 1-10, level M by default —
which covers any realistic UPI payload with room to spare and keeps the encoder
small enough to audit. The block table is validated by
``tests/test_qr.py``, which also checks Reed-Solomon syndromes are zero.
"""

from __future__ import annotations

from urllib.parse import quote

# ── Error-correction levels ──────────────────────────────────────────────────

ECC_LOW = "L"
ECC_MEDIUM = "M"
ECC_QUARTILE = "Q"
ECC_HIGH = "H"

_MAX_VERSION = 10

# (version, level) -> (EC codewords per block, [block data-codeword counts]).
# Every row sums to the version's fixed total codeword count; ``test_qr.py``
# asserts that invariant.
_BLOCK_TABLE: dict[tuple[int, str], tuple[int, list[int]]] = {
    (1, ECC_LOW): (7, [19]),
    (1, ECC_MEDIUM): (10, [16]),
    (1, ECC_QUARTILE): (13, [13]),
    (1, ECC_HIGH): (17, [9]),
    (2, ECC_LOW): (10, [34]),
    (2, ECC_MEDIUM): (16, [28]),
    (2, ECC_QUARTILE): (22, [22]),
    (2, ECC_HIGH): (28, [16]),
    (3, ECC_LOW): (15, [55]),
    (3, ECC_MEDIUM): (26, [44]),
    (3, ECC_QUARTILE): (18, [17, 17]),
    (3, ECC_HIGH): (22, [13, 13]),
    (4, ECC_LOW): (20, [80]),
    (4, ECC_MEDIUM): (18, [32, 32]),
    (4, ECC_QUARTILE): (26, [24, 24]),
    (4, ECC_HIGH): (16, [9, 9, 9, 9]),
    (5, ECC_LOW): (26, [108]),
    (5, ECC_MEDIUM): (24, [43, 43]),
    (5, ECC_QUARTILE): (18, [15, 15, 16, 16]),
    (5, ECC_HIGH): (22, [11, 11, 12, 12]),
    (6, ECC_LOW): (18, [68, 68]),
    (6, ECC_MEDIUM): (16, [27, 27, 27, 27]),
    (6, ECC_QUARTILE): (24, [19, 19, 19, 19]),
    (6, ECC_HIGH): (28, [15, 15, 15, 15]),
    (7, ECC_LOW): (20, [78, 78]),
    (7, ECC_MEDIUM): (18, [31, 31, 31, 31]),
    (7, ECC_QUARTILE): (18, [14, 14, 15, 15, 15, 15]),
    (7, ECC_HIGH): (26, [13, 13, 13, 13, 14]),
    (8, ECC_LOW): (24, [97, 97]),
    (8, ECC_MEDIUM): (22, [38, 38, 39, 39]),
    (8, ECC_QUARTILE): (22, [18, 18, 18, 18, 19, 19]),
    (8, ECC_HIGH): (26, [14, 14, 14, 14, 15, 15]),
    (9, ECC_LOW): (30, [116, 116]),
    (9, ECC_MEDIUM): (22, [36, 36, 36, 37, 37]),
    (9, ECC_QUARTILE): (20, [16, 16, 16, 16, 17, 17, 17, 17]),
    (9, ECC_HIGH): (24, [12, 12, 12, 12, 13, 13, 13, 13]),
    (10, ECC_LOW): (18, [68, 68, 69, 69]),
    (10, ECC_MEDIUM): (26, [43, 43, 43, 43, 44]),
    (10, ECC_QUARTILE): (24, [19, 19, 19, 19, 19, 19, 20, 20]),
    (10, ECC_HIGH): (28, [15, 15, 15, 15, 15, 15, 16, 16]),
}

# Total codewords per version (data + error correction), ISO/IEC 18004 table 1.
_TOTAL_CODEWORDS: dict[int, int] = {
    1: 26,
    2: 44,
    3: 70,
    4: 100,
    5: 134,
    6: 172,
    7: 196,
    8: 242,
    9: 292,
    10: 346,
}

_ALIGNMENT_CENTERS: dict[int, list[int]] = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
    6: [6, 34],
    7: [6, 22, 38],
    8: [6, 24, 42],
    9: [6, 26, 46],
    10: [6, 28, 50],
}

# Format-information field for each error-correction level.
_ECC_FORMAT_BITS: dict[str, int] = {ECC_LOW: 1, ECC_MEDIUM: 0, ECC_QUARTILE: 3, ECC_HIGH: 2}

_BYTE_MODE_INDICATOR = 0b0100

# GF(256) with primitive polynomial 0x11D and generator 2.
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x = (_x << 1) ^ (0x11D if _x & 0x80 else 0)
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


class QrCodeError(ValueError):
    """Raised when a payload cannot be encoded by this encoder."""


def upi_uri(payee_vpa: str, name: str, amount: str | None = None, note: str | None = None) -> str:
    """Build the UPI intent link that a UPI app opens when scanned.

    ``upi://pay?pa=<vpa>&pn=<name>[&am=<amount>&cu=INR][&tn=<note>]``
    """
    vpa = (payee_vpa or "").strip()
    if not vpa:
        raise QrCodeError("payee VPA (UPI ID) must not be empty")
    parts = [f"pa={quote(vpa, safe='')}", f"pn={quote((name or '').strip(), safe='')}"]
    if amount:
        parts.append(f"am={quote(str(amount), safe='')}")
        parts.append("cu=INR")
    if note:
        parts.append(f"tn={quote(note, safe='')}")
    return "upi://pay?" + "&".join(parts)


def encode(text: str, ecc: str = ECC_MEDIUM) -> list[list[bool]]:
    """Encode *text*; return the module matrix (``True`` = dark module).

    The returned matrix excludes the quiet zone — callers drawing it are
    expected to add their own margin (see :func:`to_image`).
    """
    version = pick_version(text, ecc)
    stream = build_codeword_stream(text, version, ecc)

    modules, reserved = _blank(version)
    _draw_function_patterns(modules, reserved, version)

    mask = _choose_mask(modules, reserved, version)
    _apply_data(modules, reserved, stream, mask)
    _write_format(modules, reserved, ecc, mask)

    return [[bool(modules[row][col]) for col in range(len(modules))] for row in range(len(modules))]


def pick_version(text: str, ecc: str = ECC_MEDIUM) -> int:
    """Return the smallest version that fits *text* at *ecc*."""
    _check_level(ecc)
    payload = text.encode("utf-8")
    for version in range(1, _MAX_VERSION + 1):
        if len(payload) <= _max_payload_bytes(version, ecc):
            return version
    raise QrCodeError(
        f"payload of {len(payload)} bytes is too long for a byte-mode QR code "
        f"(limit is {_max_payload_bytes(_MAX_VERSION, ecc)} bytes at level {ecc})"
    )


def _max_payload_bytes(version: int, ecc: str) -> int:
    """Bytes that fit in byte mode: data capacity minus mode and count fields."""
    capacity_bits = _data_codewords(version, ecc) * 8 - 4 - _length_bits(version)
    return max(0, capacity_bits // 8)


def _check_level(ecc: str) -> None:
    if ecc not in _ECC_FORMAT_BITS:
        raise QrCodeError(f"unsupported error-correction level: {ecc!r}")


def _length_bits(version: int) -> int:
    """Width of the byte-mode character-count field."""
    return 8 if version <= 9 else 16


def _data_codewords(version: int, ecc: str) -> int:
    return sum(_BLOCK_TABLE[(version, ecc)][1])


# ── Bit stream, block interleaving, error correction ─────────────────────────


def build_codeword_stream(text: str, version: int, ecc: str) -> list[int]:
    """Return the final (interleaved) codeword stream for *text*.

    Exposed so tests can check interleaving and error correction separately.
    """
    data = _encode_data_codewords(text, version, ecc)
    ecc_len, block_sizes = _BLOCK_TABLE[(version, ecc)]

    blocks: list[list[int]] = []
    offset = 0
    for size in block_sizes:
        blocks.append(data[offset : offset + size])
        offset += size

    ecc_blocks = [_reed_solomon(block, ecc_len) for block in blocks]

    stream: list[int] = []
    for index in range(max(block_sizes)):
        for block in blocks:
            if index < len(block):
                stream.append(block[index])
    for index in range(ecc_len):
        for block in ecc_blocks:
            stream.append(block[index])
    return stream


def _encode_data_codewords(text: str, version: int, ecc: str) -> list[int]:
    payload = text.encode("utf-8")
    total = _data_codewords(version, ecc)

    bits: list[int] = []

    def push(value: int, width: int) -> None:
        bits.extend((value >> shift) & 1 for shift in range(width - 1, -1, -1))

    push(_BYTE_MODE_INDICATOR, 4)
    push(len(payload), _length_bits(version))
    for byte in payload:
        push(byte, 8)
    push(0, min(4, total * 8 - len(bits)))  # terminator
    while len(bits) % 8:
        bits.append(0)

    codewords = [int("".join(str(b) for b in bits[i : i + 8]), 2) for i in range(0, len(bits), 8)]
    # Pad with the alternating spec codewords until the data capacity is full.
    for index in range(total - len(codewords)):
        codewords.append(0xEC if index % 2 == 0 else 0x11)
    return codewords


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator_polynomial(degree: int) -> list[int]:
    """Generator polynomial (x-a^0)(x-a^1)...(x-a^(degree-1)) for RS."""
    poly = [1]
    for i in range(degree):
        shifted = [0] * (len(poly) + 1)
        for index, coeff in enumerate(poly):
            shifted[index] ^= coeff
            shifted[index + 1] ^= _gf_mul(coeff, _EXP[i])
        poly = shifted
    return poly


def _reed_solomon(data: list[int], ecc_len: int) -> list[int]:
    generator = _generator_polynomial(ecc_len)
    remainder = [0] * ecc_len
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = [*remainder[1:], 0]
        if factor:
            # Skip the generator's leading term (implicit 1 on x^ecc_len).
            for index, coeff in enumerate(generator[1:]):
                remainder[index] ^= _gf_mul(coeff, factor)
    return remainder


def rs_syndromes(codewords: list[int], ecc_len: int) -> list[int]:
    """Evaluate a block at a^0..a^(ecc_len-1).

    A correctly encoded block (data followed by its EC codewords) evaluates to
    zero everywhere. Used by the tests to validate the encoder independently of
    the masking and matrix layout.
    """
    values = []
    for power in range(ecc_len):
        acc = 0
        for byte in codewords:
            acc = _gf_mul(acc, _EXP[power]) ^ byte
        values.append(acc)
    return values


# ── Matrix construction ──────────────────────────────────────────────────────


def _blank(version: int) -> tuple[list[list[int]], list[list[bool]]]:
    size = version * 4 + 17
    return [[0] * size for _ in range(size)], [[False] * size for _ in range(size)]


def _set_function(modules, reserved, row: int, col: int, value: int) -> None:
    modules[row][col] = value
    reserved[row][col] = True


def _draw_function_patterns(modules, reserved, version: int) -> None:
    size = len(modules)

    # Timing patterns (the finder patterns overwrite their ends).
    for i in range(size):
        _set_function(modules, reserved, 6, i, 1 if i % 2 == 0 else 0)
        _set_function(modules, reserved, i, 6, 1 if i % 2 == 0 else 0)

    # Finder patterns plus their separators, at the three corners.
    for center_row, center_col in ((3, 3), (3, size - 4), (size - 4, 3)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                row, col = center_row + dy, center_col + dx
                if not (0 <= row < size and 0 <= col < size):
                    continue
                distance = max(abs(dx), abs(dy))
                # Dark rings are 0, 2 and 3; light are the inner gap and the
                # separator ring at distance 4.
                _set_function(modules, reserved, row, col, 1 if distance in (0, 2, 3) else 0)

    # Alignment patterns, skipping the three that collide with finders.
    centers = _ALIGNMENT_CENTERS[version]
    skip = {(6, 6), (6, size - 7), (size - 7, 6)}
    for center_row in centers:
        for center_col in centers:
            if (center_row, center_col) in skip:
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    _set_function(
                        modules,
                        reserved,
                        center_row + dy,
                        center_col + dx,
                        0 if max(abs(dx), abs(dy)) == 1 else 1,
                    )

    # Reserve the format-information areas and set the always-dark module.
    # Index 6 is skipped: row 6 / column 6 belong to the timing patterns.
    for i in range(9):
        if i == 6:
            continue
        _set_function(modules, reserved, 8, i, 0)
        _set_function(modules, reserved, i, 8, 0)
    for i in range(8):
        _set_function(modules, reserved, 8, size - 1 - i, 0)
        _set_function(modules, reserved, size - 1 - i, 8, 0)
    _set_function(modules, reserved, size - 8, 8, 1)


def _format_bits(ecc: str, mask: int) -> int:
    """15-bit BCH(15,5) format string, XOR-masked with 0x5412."""
    data = (_ECC_FORMAT_BITS[ecc] << 3) | mask
    remainder = data
    for _ in range(10):
        remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
    return ((data << 10) | remainder) ^ 0x5412


def _write_format(modules, reserved, ecc: str, mask: int) -> None:
    size = len(modules)
    bits = _format_bits(ecc, mask)
    for i in range(15):
        bit = (bits >> i) & 1
        # First copy: around the top-left finder.
        if i < 6:
            modules[8][i] = bit
        elif i == 6:
            modules[8][7] = bit
        elif i == 7:
            modules[8][8] = bit
        elif i == 8:
            modules[7][8] = bit
        else:
            modules[14 - i][8] = bit
        # Second copy: split between the top-right and bottom-left finders.
        if i < 8:
            modules[8][size - 1 - i] = bit
        else:
            modules[size - 15 + i][8] = bit


def _apply_data(modules, reserved, stream: list[int], mask: int) -> None:
    """Write the codeword stream into the free modules, then mask it."""
    size = len(modules)
    mask_fn = _MASK_FUNCTIONS[mask]
    bits = [(byte >> shift) & 1 for byte in stream for shift in range(7, -1, -1)]

    index = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:  # the vertical timing pattern is not a data column
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for offset in (0, 1):
                target = col - offset
                if reserved[row][target]:
                    continue
                # Any leftover modules (the version's remainder bits) stay light.
                modules[row][target] = bits[index] if index < len(bits) else 0
                index += 1
                if mask_fn(row, target):
                    modules[row][target] ^= 1
        upward = not upward
        col -= 2


# ── Mask selection ───────────────────────────────────────────────────────────

_MASK_FUNCTIONS = (
    lambda row, col: (row + col) % 2 == 0,
    lambda row, col: row % 2 == 0,
    lambda row, col: col % 3 == 0,
    lambda row, col: (row + col) % 3 == 0,
    lambda row, col: (row // 2 + col // 3) % 2 == 0,
    lambda row, col: (row * col) % 2 + (row * col) % 3 == 0,
    lambda row, col: ((row * col) % 2 + (row * col) % 3) % 2 == 0,
    lambda row, col: ((row + col) % 2 + (row * col) % 3) % 2 == 0,
)

# 1:1:3:1:1 finder-like runs, used by penalty rule 3.
_FINDER_RUNS = (0b10111010000, 0b00001011101)


def _choose_mask(modules, reserved, version: int) -> int:
    """Pick the mask with the lowest penalty score (ISO/IEC 18004 §8.8)."""
    best_mask = 0
    best_score = None
    for mask in range(8):
        score = _masked_penalty(modules, reserved, mask)
        if best_score is None or score < best_score:
            best_score, best_mask = score, mask
    return best_mask


def _masked_penalty(modules, reserved, mask: int) -> int:
    """Penalty of the matrix with *mask* applied, without rebuilding the stream.

    Only the data modules change under a mask, so the penalty is computed on the
    unmasked matrix with each candidate mask applied to a scratch copy.
    """
    size = len(modules)
    grid = [row[:] for row in modules]
    mask_fn = _MASK_FUNCTIONS[mask]
    for row in range(size):
        for col in range(size):
            if not reserved[row][col] and mask_fn(row, col):
                grid[row][col] ^= 1
    return _penalty(grid, size)


def _penalty(grid: list[list[int]], size: int) -> int:
    score = 0

    # Rule 1 — runs of five or more modules of the same color.
    for index in range(size):
        for line in (grid[index], [grid[row][index] for row in range(size)]):
            run_color = line[0]
            run_length = 0
            for value in line:
                if value == run_color:
                    run_length += 1
                else:
                    if run_length >= 5:
                        score += 3 + (run_length - 5)
                    run_color, run_length = value, 1
            if run_length >= 5:
                score += 3 + (run_length - 5)

    # Rule 2 — 2x2 blocks of a single color.
    for row in range(size - 1):
        for col in range(size - 1):
            block = (grid[row][col], grid[row][col + 1], grid[row + 1][col], grid[row + 1][col + 1])
            if block[0] == block[1] == block[2] == block[3]:
                score += 3

    # Rule 3 — finder-like 1:1:3:1:1 patterns.
    for index in range(size):
        for line in (grid[index], [grid[row][index] for row in range(size)]):
            for start in range(size - 10):
                window = 0
                for offset in range(11):
                    window = (window << 1) | line[start + offset]
                if window in _FINDER_RUNS:
                    score += 40

    # Rule 4 — deviation from an even dark/light balance.
    dark = sum(1 for row in grid for value in row if value)
    score += (abs(dark * 20 - size * size * 10) // (size * size)) * 10

    return score


# ── Rendering ────────────────────────────────────────────────────────────────


def to_ascii(matrix: list[list[bool]], dark: str = "██", light: str = "  ") -> str:
    """Render *matrix* as text — handy for debugging and test failure output."""
    return "\n".join("".join(dark if cell else light for cell in row) for row in matrix)


def to_image(matrix: list[list[bool]], module_px: int = 6, quiet_modules: int = 4):
    """Render *matrix* to a ``QImage`` with a white quiet zone.

    Returns ``None`` when Qt is unavailable, so the encoder stays importable in
    headless tooling.
    """
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QColor, QImage, QPainter
    except ImportError:  # pragma: no cover - PyQt6 is a hard dependency
        return None

    size = len(matrix)
    span = (size + quiet_modules * 2) * module_px
    image = QImage(span, span, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))

    painter = QPainter(image)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("black"))
    for row in range(size):
        for col in range(size):
            if matrix[row][col]:
                painter.drawRect(
                    (col + quiet_modules) * module_px,
                    (row + quiet_modules) * module_px,
                    module_px,
                    module_px,
                )
    painter.end()
    return image

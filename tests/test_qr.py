"""Validate the dependency-free QR encoder in ``niruvi.utils.qr``.

There is no QR decoder available in this environment, so the encoder is checked
three ways instead:

1. The block table must sum to each version's fixed total codeword count.
2. Every encoded block must have zero Reed-Solomon syndromes — this validates the
   GF(256) arithmetic and the generator polynomial independently of the matrix
   layout, because a wrong polynomial or EC length produces non-zero syndromes.
3. The finished matrix must decode back to the original payload, which exercises
   masking, data placement and format information end to end.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from niruvi.utils import qr

PAYLOAD = qr.upi_uri("putinservai-1@okhdfcbank", "Niruvi", note="Support Niruvi")


class TestUpiUri:
    def test_builds_intent_link(self):
        uri = qr.upi_uri("putinservai-1@okhdfcbank", "Niruvi")
        assert uri.startswith("upi://pay?")
        assert "pa=putinservai-1%40okhdfcbank" in uri
        assert "pn=Niruvi" in uri

    def test_optional_amount_and_note(self):
        uri = qr.upi_uri("a@b", "Niruvi", amount="50", note="Thanks & cheers")
        assert "am=50" in uri
        assert "cu=INR" in uri
        assert "tn=Thanks%20%26%20cheers" in uri

    def test_empty_vpa_rejected(self):
        with pytest.raises(qr.QrCodeError):
            qr.upi_uri("   ", "Niruvi")


class TestBlockTable:
    @pytest.mark.parametrize("version", sorted(qr._TOTAL_CODEWORDS))
    @pytest.mark.parametrize("ecc", [qr.ECC_LOW, qr.ECC_MEDIUM, qr.ECC_QUARTILE, qr.ECC_HIGH])
    def test_blocks_sum_to_total_codewords(self, version, ecc):
        ecc_len, sizes = qr._BLOCK_TABLE[(version, ecc)]
        total = sum(sizes) + ecc_len * len(sizes)
        assert total == qr._TOTAL_CODEWORDS[version]

    def test_every_version_level_pair_present(self):
        for version in qr._TOTAL_CODEWORDS:
            for ecc in qr._ECC_FORMAT_BITS:
                assert (version, ecc) in qr._BLOCK_TABLE


class TestReedSolomon:
    def test_encoded_blocks_have_zero_syndromes(self):
        """The strongest available check on the RS implementation."""
        for version in sorted(qr._TOTAL_CODEWORDS):
            ecc = qr.ECC_MEDIUM
            ecc_len, sizes = qr._BLOCK_TABLE[(version, ecc)]
            payload = "x" * qr._max_payload_bytes(version, ecc)
            data = qr._encode_data_codewords(payload, version, ecc)

            blocks, offset = [], 0
            for size in sizes:
                blocks.append(data[offset : offset + size])
                offset += size

            assert offset == len(data)
            for block in blocks:
                codewords = block + qr._reed_solomon(block, ecc_len)
                assert qr.rs_syndromes(codewords, ecc_len) == [0] * ecc_len

    def test_single_bit_error_is_detected(self):
        """Proves the syndrome check has real detection power."""
        ecc_len = qr._BLOCK_TABLE[(1, qr.ECC_MEDIUM)][0]
        block = qr._encode_data_codewords("niruvi", 1, qr.ECC_MEDIUM)
        damaged = block + qr._reed_solomon(block, ecc_len)
        damaged[3] ^= 0x01
        assert any(qr.rs_syndromes(damaged, ecc_len))


class TestEncoding:
    def test_picks_smallest_version(self):
        # Spec byte-mode capacities: v1-M = 14, v2-M = 26, v3-M = 42.
        assert qr._max_payload_bytes(1, qr.ECC_MEDIUM) == 14
        assert qr._max_payload_bytes(2, qr.ECC_MEDIUM) == 26
        assert qr._max_payload_bytes(3, qr.ECC_MEDIUM) == 42
        assert qr.pick_version("hi") == 1
        assert qr.pick_version("a" * 14) == 1
        assert qr.pick_version("a" * 15) == 2
        assert qr.pick_version("a" * 26) == 2
        assert qr.pick_version("a" * 27) == 3

    def test_unknown_level_rejected(self):
        with pytest.raises(qr.QrCodeError):
            qr.pick_version("hi", "Z")

    def test_payload_too_long_rejected(self):
        with pytest.raises(qr.QrCodeError):
            qr.encode("a" * 5000)

    @pytest.mark.parametrize("ecc", [qr.ECC_LOW, qr.ECC_MEDIUM, qr.ECC_QUARTILE, qr.ECC_HIGH])
    def test_round_trip(self, ecc):
        for payload in ("NIRUVI", PAYLOAD, qr.upi_uri("a@b", "Niruvi", amount="120.50")):
            matrix = qr.encode(payload, ecc)
            assert _decode(matrix, ecc) == payload

    def test_upi_payload_round_trip(self):
        assert _decode(qr.encode(PAYLOAD), qr.ECC_MEDIUM) == PAYLOAD

    @pytest.mark.parametrize("version", sorted(qr._TOTAL_CODEWORDS))
    def test_matrix_geometry(self, version):
        payload = "a" * qr._max_payload_bytes(version, qr.ECC_MEDIUM)
        matrix = qr.encode(payload, qr.ECC_MEDIUM)
        expected = version * 4 + 17
        assert len(matrix) == expected
        assert all(len(row) == expected for row in matrix)
        assert all(isinstance(cell, bool) for row in matrix for cell in row)

    def test_finder_patterns_present(self):
        matrix = qr.encode("niruvi")
        size = len(matrix)
        for top, left in ((0, 0), (0, size - 7), (size - 7, 0)):
            for dy in range(7):
                for dx in range(7):
                    distance = max(abs(dy - 3), abs(dx - 3))
                    assert matrix[top + dy][left + dx] is (distance in (0, 2, 3))
            # The in-bounds separator edges around each finder stay light.
            for dy, dx in ((7, 3), (3, 7), (7, -1), (-1, 7)):
                row, col = top + dy, left + dx
                if 0 <= row < size and 0 <= col < size:
                    assert matrix[row][col] is False

    def test_timing_patterns_alternate(self):
        matrix = qr.encode("niruvi")
        size = len(matrix)
        for i in range(8, size - 8):
            assert matrix[6][i] is (i % 2 == 0)
            assert matrix[i][6] is (i % 2 == 0)

    def test_dark_module(self):
        matrix = qr.encode("niruvi")
        assert matrix[len(matrix) - 8][8] is True

    def test_renders_to_qimage(self):
        image = qr.to_image(qr.encode(PAYLOAD), module_px=4, quiet_modules=4)
        assert image is not None
        assert image.width() == (len(qr.encode(PAYLOAD)) + 8) * 4
        assert not image.isNull()

    def test_ascii_rendering(self):
        text = qr.to_ascii(qr.encode("hi"), dark="#", light=".")
        lines = text.splitlines()
        assert len(lines) == 21
        assert all(len(line) == 21 for line in lines)
        # The top row is the top edge of the two upper finder patterns.
        assert lines[0][:7] == "#" * 7
        assert lines[0][7] == "."
        assert lines[0][-7:] == "#" * 7


class TestFormatInformation:
    @pytest.mark.parametrize(
        "ecc,expected",
        [
            (qr.ECC_LOW, 0b111011111000100),
            (qr.ECC_MEDIUM, 0b101010000010010),
            (qr.ECC_QUARTILE, 0b011010101011111),
            (qr.ECC_HIGH, 0b001011010001001),
        ],
    )
    def test_mask_zero_matches_iso_table_c1(self, ecc, expected):
        """ISO/IEC 18004 table C.1 lists the format string for mask 0."""
        assert qr._format_bits(ecc, 0) == expected

    @pytest.mark.parametrize("ecc", [qr.ECC_LOW, qr.ECC_MEDIUM, qr.ECC_QUARTILE, qr.ECC_HIGH])
    @pytest.mark.parametrize("mask", range(8))
    def test_bch_codeword_is_valid(self, ecc, mask):
        """Undoing the 0x5412 mask must yield a word divisible by the BCH generator."""
        unmasked = qr._format_bits(ecc, mask) ^ 0x5412
        data = unmasked >> 10
        assert qr._ECC_FORMAT_BITS[ecc] == (data >> 3) & 0b11
        assert mask == data & 0b111
        assert _poly_mod_generator(unmasked) == 0

    def test_format_copies_match(self):
        matrix = qr.encode("niruvi")
        size = len(matrix)
        first = [matrix[8][i] for i in range(6)] + [matrix[8][7], matrix[8][8], matrix[7][8]]
        first += [matrix[14 - i][8] for i in range(9, 15)]
        second = [matrix[8][size - 1 - i] for i in range(8)]
        second += [matrix[size - 15 + i][8] for i in range(8, 15)]
        assert first == second


def _poly_mod_generator(value: int) -> int:
    """Reduce a 15-bit value modulo the BCH(15,5) generator 0b10100110111."""
    remainder = value
    for shift in range(14, 9, -1):
        if remainder & (1 << shift):
            remainder ^= 0x537 << (shift - 10)
    return remainder


# ── Reference decoder (test-only) ────────────────────────────────────────────


def _decode(matrix: list[list[bool]], ecc: str) -> str:
    """Read *matrix* back into its payload, independently of the encoder path."""
    size = len(matrix)
    version = (size - 17) // 4
    _, block_sizes = qr._BLOCK_TABLE[(version, ecc)]

    # Recover the mask from the second format copy.
    bits = 0
    for i in range(8):
        bits |= int(matrix[8][size - 1 - i]) << i
    for i in range(8, 15):
        bits |= int(matrix[size - 15 + i][8]) << i
    mask = ((bits ^ 0x5412) >> 10) & 0b111

    _, reserved = qr._blank(version)
    qr._draw_function_patterns([[0] * size for _ in range(size)], reserved, version)

    mask_fn = qr._MASK_FUNCTIONS[mask]
    collected: list[int] = []
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for offset in (0, 1):
                target = col - offset
                if reserved[row][target]:
                    continue
                bit = 1 if matrix[row][target] else 0
                if mask_fn(row, target):
                    bit ^= 1
                collected.append(bit)
        upward = not upward
        col -= 2

    stream = [int("".join(str(b) for b in collected[i : i + 8]), 2) for i in range(0, len(collected) - 7, 8)]

    # De-interleave back into blocks.
    blocks: list[list[int]] = [[] for _ in block_sizes]
    index = 0
    for position in range(max(block_sizes)):
        for b, length in enumerate(block_sizes):
            if position < length:
                blocks[b].append(stream[index])
                index += 1

    data: list[int] = []
    for block in blocks:
        data.extend(block)

    bits = []
    for byte in data:
        bits.extend((byte >> shift) & 1 for shift in range(7, -1, -1))

    reader = _BitReader(bits)
    mode = reader.read(4)
    assert mode == qr._BYTE_MODE_INDICATOR, "decoder expected byte mode"
    length = reader.read(qr._length_bits(version))
    return bytes(reader.read(8) for _ in range(length)).decode("utf-8")


class _BitReader:
    def __init__(self, bits):
        self.bits = bits
        self.pos = 0

    def read(self, width):
        value = 0
        for bit in self.bits[self.pos : self.pos + width]:
            value = (value << 1) | bit
        self.pos += width
        return value

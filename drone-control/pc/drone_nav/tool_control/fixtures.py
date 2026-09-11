"""Bounded, dependency-free variants of the repository's synthetic RGBA PNGs."""
from __future__ import annotations

from pathlib import Path
import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
LIMIT = 4 * 1024 * 1024
GLYPHS = {
    "S": ("11111", "10000", "10000", "11111", "00001", "00001", "11111"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "11111"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "E": ("11111", "10000", "10000", "11111", "10000", "10000", "11111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "2": ("11111", "00001", "00001", "11111", "10000", "10000", "11111"),
}


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def fixture_frames(path):
    """Return canonical bytes and a visibly labeled, pixel-distinct simulation."""
    with Path(path).open("rb") as source:
        original = source.read(LIMIT + 1)
    if not original.startswith(SIGNATURE) or len(original) > LIMIT:
        raise RuntimeError("Canonical mock PNG fixture unavailable")
    offset, header, compressed, ended = 8, None, [], False
    while offset + 12 <= len(original):
        length = struct.unpack(">I", original[offset:offset + 4])[0]
        kind = original[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(original):
            raise RuntimeError("Truncated mock PNG chunk")
        data = original[offset + 8:end - 4]
        if zlib.crc32(kind + data) != struct.unpack(">I", original[end - 4:end])[0]:
            raise RuntimeError("Mock PNG checksum failed")
        if kind == b"IHDR":
            if header is not None or offset != 8 or length != 13:
                raise RuntimeError("Invalid mock PNG header")
            header = data
        elif kind == b"IDAT":
            compressed.append(data)
        elif kind == b"IEND":
            ended = length == 0 and end == len(original)
            break
        offset = end
    if header is None or not ended or not compressed:
        raise RuntimeError("Incomplete mock PNG")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", header)
    if (not 32 <= width <= 960 or not 32 <= height <= 960
            or (depth, color, compression, filtering, interlace) != (8, 6, 0, 0, 0)):
        raise RuntimeError("Canonical fixture must be a bounded non-interlaced 8-bit RGBA PNG")
    stride = width * 4
    expected = (stride + 1) * height
    decoder = zlib.decompressobj()
    raw = decoder.decompress(b"".join(compressed), expected + 1)
    if len(raw) != expected or not decoder.eof or decoder.unused_data:
        raise RuntimeError("Invalid mock PNG decoded size")
    changed = bytearray(raw)
    previous = bytearray(stride)
    # Decode only the banner and its following row. Re-encoding that following
    # original row with filter 0 preserves all subsequent original scanlines.
    for y in range(min(height, 33)):
        start = y * (stride + 1)
        filtering = raw[start]
        if filtering > 4:
            raise RuntimeError("Invalid mock PNG scanline filter")
        row = bytearray(raw[start + 1:start + stride + 1])
        for x in range(stride):
            left = row[x - 4] if x >= 4 else 0
            above = previous[x]
            corner = previous[x - 4] if x >= 4 else 0
            if filtering == 1:
                predictor = left
            elif filtering == 2:
                predictor = above
            elif filtering == 3:
                predictor = (left + above) // 2
            elif filtering == 4:
                p = left + above - corner
                distances = abs(p - left), abs(p - above), abs(p - corner)
                predictor = (left, above, corner)[distances.index(min(distances))]
            else:
                predictor = 0
            row[x] = (row[x] + predictor) & 255
        previous = row[:]
        if y < 32:
            row[:min(width, 150) * 4] = b"\x50\x00\x50\xff" * min(width, 150)
            for ordinal, letter in enumerate("SIMULATED 2"):
                if letter == " " or not 8 <= y < 22:
                    continue
                glyph = GLYPHS[letter][(y - 8) // 2]
                for column, value in enumerate(glyph):
                    if value == "1":
                        x = 8 + ordinal * 12 + column * 2
                        if x + 2 <= width:
                            row[x * 4:(x + 2) * 4] = b"\xff\xff\xff\xff" * 2
        changed[start] = 0
        changed[start + 1:start + stride + 1] = row
    second = (SIGNATURE + _chunk(b"IHDR", header)
              + _chunk(b"tEXt", b"Description\x00SIMULATED fixture capture 2; not a camera")
              + _chunk(b"IDAT", zlib.compress(changed))
              + _chunk(b"IEND", b""))
    if len(second) > LIMIT or second == original:
        raise RuntimeError("Distinct synthetic fixture encoding failed")
    return original, second

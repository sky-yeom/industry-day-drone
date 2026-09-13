"""Bounded, dependency-free variants and previews of the repository's RGB/RGBA PNGs."""
from __future__ import annotations

from pathlib import Path
from functools import lru_cache
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


def _read(path):
    with Path(path).open("rb") as source:
        original = source.read(LIMIT + 1)
    if not original.startswith(SIGNATURE) or len(original) > LIMIT:
        raise RuntimeError("Canonical mock PNG fixture unavailable")
    return original


def _decode(original):
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
    if (not 32 <= width <= 4096 or not 32 <= height <= 4096
            or color not in (2, 6)
            or (depth, compression, filtering, interlace) != (8, 0, 0, 0)):
        raise RuntimeError("Canonical fixture must be a bounded non-interlaced 8-bit RGB/RGBA PNG")
    channels = 3 if color == 2 else 4
    stride = width * channels
    expected = (stride + 1) * height
    decoder = zlib.decompressobj()
    raw = decoder.decompress(b"".join(compressed), expected + 1)
    if len(raw) != expected or not decoder.eof or decoder.unused_data:
        raise RuntimeError("Invalid mock PNG decoded size")
    return header, width, height, channels, raw


def _row(raw, y, width, channels, previous):
    stride = width * channels
    start = y * (stride + 1)
    filtering = raw[start]
    if filtering > 4:
        raise RuntimeError("Invalid mock PNG scanline filter")
    row = bytearray(raw[start + 1:start + stride + 1])
    if filtering == 0:
        return row
    for x in range(stride):
        left = row[x - channels] if x >= channels else 0
        above = previous[x]
        corner = previous[x - channels] if x >= channels else 0
        if filtering == 1:
            predictor = left
        elif filtering == 2:
            predictor = above
        elif filtering == 3:
            predictor = (left + above) // 2
        else:
            p = left + above - corner
            distances = abs(p - left), abs(p - above), abs(p - corner)
            predictor = (left, above, corner)[distances.index(min(distances))]
        row[x] = (row[x] + predictor) & 255
    return row


def fixture_frames(path):
    """Return canonical bytes and a visibly labeled, pixel-distinct simulation."""
    original = _read(path)
    header, width, height, channels, raw = _decode(original)
    stride = width * channels
    changed = bytearray(raw)
    previous = bytearray(stride)
    # Decode only the banner and its following row. Re-encoding that following
    # original row with filter 0 preserves all subsequent original scanlines.
    for y in range(min(height, 33)):
        start = y * (stride + 1)
        row = _row(raw, y, width, channels, previous)
        previous = row[:]
        if y < 32:
            row[:min(width, 150) * channels] = b"\x50\x00\x50\xff"[:channels] * min(width, 150)
            for ordinal, letter in enumerate("SIMULATED 2"):
                if letter == " " or not 8 <= y < 22:
                    continue
                glyph = GLYPHS[letter][(y - 8) // 2]
                for column, value in enumerate(glyph):
                    if value == "1":
                        x = 8 + ordinal * 12 + column * 2
                        if x + 2 <= width:
                            row[x * channels:(x + 2) * channels] = b"\xff" * (channels * 2)
        changed[start] = 0
        changed[start + 1:start + stride + 1] = row
    second = (SIGNATURE + _chunk(b"IHDR", header)
              + _chunk(b"tEXt", b"Description\x00SIMULATED fixture capture 2; not a camera")
              + _chunk(b"IDAT", zlib.compress(changed))
              + _chunk(b"IEND", b""))
    if len(second) > LIMIT or second == original:
        raise RuntimeError("Distinct synthetic fixture encoding failed")
    return original, second


def fixture_preview(path, *, max_edge, max_bytes):
    return _preview(_read(path), max_edge, max_bytes)


@lru_cache(maxsize=3)
def _preview(original, max_edge, max_bytes):
    header, width, height, channels, raw = _decode(original)
    if max(width, height) <= max_edge and len(original) <= max_bytes:
        return original
    edge = min(max_edge, 640)
    out_width = max(1, width * edge // max(width, height))
    out_height = max(1, height * edge // max(width, height))
    sampled = []
    previous = bytearray(width * channels)
    next_y = 0
    for y in range(height):
        row = _row(raw, y, width, channels, previous)
        previous = row
        if len(sampled) < out_height and y == next_y:
            sampled.append(b"".join(row[(x * width // out_width) * channels:
                                      (x * width // out_width + 1) * channels] for x in range(out_width)))
            next_y = len(sampled) * height // out_height
    while True:
        output_header = struct.pack(">IIBBBBB", out_width, out_height, 8, header[9], 0, 0, 0)
        preview = (SIGNATURE + _chunk(b"IHDR", output_header)
                   + _chunk(b"IDAT", zlib.compress(b"".join(b"\0" + row for row in sampled)))
                   + _chunk(b"IEND", b""))
        if len(preview) <= max_bytes:
            return preview
        if min(out_width, out_height) <= 1:
            raise RuntimeError("Mock fixture cannot fit preview limits")
        new_width, new_height = max(1, out_width // 2), max(1, out_height // 2)
        sampled = [b"".join(sampled[y * out_height // new_height][
            (x * out_width // new_width) * channels:(x * out_width // new_width + 1) * channels]
            for x in range(new_width)) for y in range(new_height)]
        out_width, out_height = new_width, new_height

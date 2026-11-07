"""Tiny PNG and JPEG files with real headers, for the screenshot tests."""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png(path: Path, w: int, h: int, color: int = 2, trns: bool = False, size: int = 150) -> Path:
    """A PNG header with colour type `color` (2 = RGB, 6 = RGBA) and body bytes unique to the file name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (hashlib.sha256(path.name.encode()).digest() * (size // 32 + 1))[:size]
    data = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color, 0, 0, 0))
            + (_chunk(b"tRNS", b"\x00\x00\x00\x00\x00\x00") if trns else b"")
            + _chunk(b"IDAT", body) + _chunk(b"IEND", b""))
    path.write_bytes(data)
    return path


def jpeg(path: Path, w: int, h: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, h, w, 3) + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    path.write_bytes(b"\xff\xd8" + app0 + sof + b"\xff\xda\x00\x02" + path.name.encode() + b"\xff\xd9")
    return path

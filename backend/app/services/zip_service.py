"""Streaming ZIP generation.

The archive is produced incrementally: ``zipfile`` writes into a non-seekable sink (so it uses
data descriptors instead of seeking back), and each chunk is yielded to the HTTP response as
soon as it is produced. Memory use stays at a few chunks regardless of archive size.
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from app.utils.helpers import COMPRESSED_EXTS

logger = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class ZipEntry:
    arcname: str
    path: Path
    extension: str


class _StreamSink(io.RawIOBase):
    """Write-only, non-seekable buffer that hands back whatever has been written so far."""

    def __init__(self) -> None:
        super().__init__()
        self._buffer = bytearray()
        self._position = 0

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def write(self, b) -> int:  # type: ignore[override]
        data = bytes(b)
        self._buffer.extend(data)
        self._position += len(data)
        return len(data)

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        data = bytes(self._buffer)
        self._buffer.clear()
        return data


def stream_zip(entries: Iterable[ZipEntry]) -> Iterator[bytes]:
    """Yield the bytes of a ZIP archive containing ``entries``."""
    sink = _StreamSink()
    now = time.localtime()[:6]
    with zipfile.ZipFile(sink, mode="w", allowZip64=True) as zf:
        for entry in entries:
            if not entry.path.is_file():
                logger.warning("Skipping missing cached file %s", entry.path)
                continue
            info = zipfile.ZipInfo(entry.arcname, date_time=now)
            info.compress_type = zipfile.ZIP_STORED if entry.extension in COMPRESSED_EXTS else zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            size = entry.path.stat().st_size
            with entry.path.open("rb") as src, zf.open(info, mode="w", force_zip64=size > 0x7FFFFFFF) as dst:
                while chunk := src.read(CHUNK_SIZE):
                    dst.write(chunk)
                    out = sink.drain()
                    if out:
                        yield out
            out = sink.drain()
            if out:
                yield out
    tail = sink.drain()
    if tail:
        yield tail

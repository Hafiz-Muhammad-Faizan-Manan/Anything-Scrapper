import io
import os
import zipfile
from pathlib import Path

from app.services.zip_service import ZipEntry, stream_zip
from tests.conftest import SVG, make_image


def test_stream_zip_roundtrip(tmp_path: Path) -> None:
    png = make_image("PNG")
    big = os.urandom(300 * 1024)  # spans several chunks
    (tmp_path / "a.png").write_bytes(png)
    (tmp_path / "b.svg").write_bytes(SVG)
    (tmp_path / "c.jpg").write_bytes(big)
    entries = [
        ZipEntry("a.png", tmp_path / "a.png", "png"),
        ZipEntry("b.svg", tmp_path / "b.svg", "svg"),
        ZipEntry("c.jpg", tmp_path / "c.jpg", "jpg"),
        ZipEntry("missing.png", tmp_path / "missing.png", "png"),
    ]
    chunks = list(stream_zip(entries))
    assert len(chunks) > 3, "archive should be produced incrementally"
    archive = zipfile.ZipFile(io.BytesIO(b"".join(chunks)))
    assert archive.testzip() is None
    assert archive.namelist() == ["a.png", "b.svg", "c.jpg"]
    assert archive.read("a.png") == png
    assert archive.read("b.svg") == SVG
    assert archive.read("c.jpg") == big
    assert archive.getinfo("a.png").compress_type == zipfile.ZIP_STORED
    assert archive.getinfo("b.svg").compress_type == zipfile.ZIP_DEFLATED

"""Uploaded model files: save them safely in one folder and find the run file.

A user drops a `.run` file and its `.dd` files, a folder, or a zip. A TIMES
run file includes its DD files by name (`$BATINCLUDE base.dd`), so everything
is flattened into one folder; two different files with the same name are an
error. Zip members are extracted by name only, so a member path can never
leave the folder.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO

MAX_TOTAL_BYTES = 2 * 1024**3  # 2 GB of model files in one upload
_CHUNK = 1 << 20
MAINDRV = re.compile(r"^\s*\$\s*batinclude\s+maindrv\.mod", re.IGNORECASE | re.MULTILINE)


class UploadError(ValueError):
    """The upload cannot be used as a TIMES model."""


def safe_name(name: str) -> str:
    """The file name alone, from a browser or zip path; rejects empty and dot names."""
    base = PureWindowsPath(PurePosixPath(name.replace("\\", "/")).name).name.strip()
    if not base or base in (".", "..") or base.startswith("."):
        raise UploadError(f"not a usable file name: {name!r}")
    return base


class Folder:
    """Collects uploaded files into one folder, flattened, with a size limit."""

    def __init__(self, dest: Path, max_bytes: int = MAX_TOTAL_BYTES):
        self.dest = Path(dest)
        self.dest.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.total = 0
        self.files: dict[str, Path] = {}  # lower-case name -> path

    def _target(self, name: str) -> Path:
        base = safe_name(name)
        key = base.lower()
        if key in self.files:
            raise UploadError(f"two files are named {base}: a run file includes DD files by name")
        p = self.dest / base
        self.files[key] = p
        return p

    def _copy(self, src: BinaryIO, dst: Path):
        with open(dst, "wb") as out:
            while chunk := src.read(_CHUNK):
                self.total += len(chunk)
                if self.total > self.max_bytes:
                    raise UploadError(f"the upload is larger than {self.max_bytes // 1024**2} MB")
                out.write(chunk)

    def add(self, name: str, stream: BinaryIO):
        if name.lower().endswith(".zip"):
            self.add_zip(stream)
        else:
            self._copy(stream, self._target(name))

    def add_zip(self, stream: BinaryIO):
        try:
            zf = zipfile.ZipFile(stream)
        except zipfile.BadZipFile as e:
            raise UploadError("the zip file could not be read") from e
        with zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                parts = PurePosixPath(info.filename.replace("\\", "/")).parts
                if any(p.startswith(".") or p == "__MACOSX" for p in parts):
                    continue  # hidden files and macOS metadata
                with zf.open(info) as src:
                    self._copy(src, self._target(info.filename))


def save_uploads(files: Iterable[tuple[str, BinaryIO]], dest: Path) -> list[Path]:
    f = Folder(dest)
    for name, stream in files:
        f.add(name, stream)
    if not f.files:
        raise UploadError("no files were uploaded")
    return sorted(f.files.values())


def is_run_file(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(MAINDRV.search(text))


def find_run_files(folder: Path) -> list[Path]:
    """Files in the folder that are TIMES run files (they include maindrv.mod)."""
    return sorted(p for p in Path(folder).iterdir() if p.is_file() and is_run_file(p))

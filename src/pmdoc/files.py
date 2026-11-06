"""
files.py — atomic replacement for files other processes may read mid-write.

`atomic_write_text` writes a temp file in the same directory and renames it over
the target, so a reader never sees a half-written file and a crash mid-write
leaves the previous version intact.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _read_umask() -> int:
    umask = os.umask(0)
    os.umask(umask)
    return umask


# 0o666 minus the umask: the mode a plain open() would create. mkstemp always
# creates 0o600, which is wrong for docs other people read. Read once at import,
# before any worker thread exists, because os.umask is process-wide.
_DEFAULT_MODE = 0o666 & ~_read_umask()


def atomic_write_text(path: Path | str, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, _DEFAULT_MODE)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise

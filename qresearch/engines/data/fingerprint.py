"""Data fingerprint helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path


def fingerprint_paths(paths: list[Path]) -> str:
    if not paths:
        return "unavailable"
    h = hashlib.sha1()
    for p in sorted(paths, key=lambda x: str(x)):
        try:
            st = p.stat()
            h.update(str(p.resolve()).encode("utf-8"))
            h.update(str(st.st_mtime_ns).encode("utf-8"))
            h.update(str(st.st_size).encode("utf-8"))
        except OSError:
            return "unavailable"
    return h.hexdigest()


def fingerprint_dir_glob(root: Path, pattern: str = "**/*") -> str:
    if not root.exists():
        return "unavailable"
    files = [p for p in root.glob(pattern) if p.is_file()]
    return fingerprint_paths(files[:5000])  # cap

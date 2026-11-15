"""Small on-disk JSON cache with TTL, plus per-key locks for de-duplication."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def key_lock(key: str) -> threading.Lock:
    """Return a process-wide lock for ``key`` (used so two threads never download the same file)."""
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.Lock()
        return lock


def stable_hash(obj: Any) -> str:
    """Deterministic short hash of a JSON-serialisable object."""
    blob = json.dumps(obj, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:24]


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.part")
    with open(tmp, "wb") as fh:
        fh.write(data)
    _replace_with_retry(tmp, path)


def _replace_with_retry(tmp: Path, path: Path, attempts: int = 8) -> None:
    """``os.replace`` that tolerates Windows sharing violations.

    On Windows the rename fails with PermissionError while another thread or
    process (a concurrent request, an antivirus or OneDrive sync) briefly has the
    destination open. Writers of the same path write identical content, so a short
    retry is safe.
    """
    for i in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == attempts - 1:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.05 * (2 ** i))


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_bytes(path, json.dumps(obj, indent=2, default=str).encode("utf-8"))


class JsonCache:
    """Directory of ``<key>.json`` files, each stamped with its creation time."""

    def __init__(self, directory: Path, ttl_s: float | None):
        self.directory = Path(directory)
        self.ttl_s = ttl_s

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> Any | None:
        path = self._path(key)
        try:
            with open(path, encoding="utf-8") as fh:
                wrapped = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        if self.ttl_s is not None and time.time() - wrapped.get("created", 0) > self.ttl_s:
            return None
        return wrapped.get("value")

    def set(self, key: str, value: Any) -> None:
        atomic_write_json(self._path(key), {"created": time.time(), "value": value})

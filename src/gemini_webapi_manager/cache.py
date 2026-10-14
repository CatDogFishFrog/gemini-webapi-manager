"""Keeps gemini-webapi's own cookie cache out of plain sight.

gemini-webapi persists refreshed cookies as plain JSON under ``GEMINI_COOKIE_PATH``
(default: the system temp dir). That would defeat the encrypted vault, so while any
managed client is alive the cache is redirected to a private temporary directory that
is wiped afterwards. Rotated cookies are copied back into the vault by ManagedClient.

If the user already exports ``GEMINI_COOKIE_PATH`` it is respected and left untouched.
The variable is process-wide, hence the reference counting.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_ENV = "GEMINI_COOKIE_PATH"
_lock = threading.Lock()
_refs = 0
_owned: Path | None = None


def acquire() -> None:
    global _refs, _owned
    with _lock:
        if _refs == 0 and not os.environ.get(_ENV):
            _owned = Path(tempfile.mkdtemp(prefix="gwm-cookies-"))
            os.environ[_ENV] = str(_owned)
        _refs += 1


def _wipe() -> None:
    global _owned
    if _owned is None:
        return
    if os.environ.get(_ENV) == str(_owned):
        os.environ.pop(_ENV, None)
    shutil.rmtree(_owned, ignore_errors=True)
    _owned = None


def release() -> None:
    global _refs
    with _lock:
        _refs = max(0, _refs - 1)
        if _refs == 0:
            _wipe()


@contextmanager
def sandbox() -> Iterator[None]:
    acquire()
    try:
        yield
    finally:
        release()


def _cleanup_at_exit() -> None:
    with _lock:
        _wipe()


atexit.register(_cleanup_at_exit)

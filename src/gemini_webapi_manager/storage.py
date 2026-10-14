"""Encrypted, file-locked, atomically written vault."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from filelock import FileLock

from .config import Settings
from .crypto import VaultCipher
from .errors import VaultCorruptedError
from .models import VaultData

_LOCK_TIMEOUT = 30.0


class VaultStore:
    """Single encrypted file holding every account.

    Reads are lock-free (writes are atomic renames). Mutations go through
    :meth:`transaction`, which holds a cross-process file lock for read-modify-write.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cipher = VaultCipher(settings.key)
        self._thread_lock = threading.RLock()

    @property
    def settings(self) -> Settings:
        return self._settings

    def _ensure_home(self) -> None:
        home = self._settings.home
        home.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(home, 0o700)

    def load(self) -> VaultData:
        path = self._settings.vault_path
        if not path.exists():
            return VaultData()
        plaintext = self._cipher.decrypt(path.read_bytes())
        try:
            return VaultData.from_dict(json.loads(plaintext))
        except (ValueError, TypeError, KeyError) as exc:
            raise VaultCorruptedError("Vault decrypted but its content is malformed.") from exc

    def _save(self, data: VaultData) -> None:
        payload = json.dumps(data.to_dict(), separators=(",", ":")).encode("utf-8")
        blob = self._cipher.encrypt(payload)
        fd, tmp = tempfile.mkstemp(dir=self._settings.home, prefix=".vault-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(blob)
                handle.flush()
                os.fsync(handle.fileno())
            with contextlib.suppress(OSError):
                os.chmod(tmp, 0o600)
            os.replace(tmp, self._settings.vault_path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    @contextmanager
    def transaction(self) -> Iterator[VaultData]:
        """Yield the vault for modification; it is saved only if the block does not raise."""
        self._ensure_home()
        with self._thread_lock, FileLock(str(self._settings.lock_path), timeout=_LOCK_TIMEOUT):
            data = self.load()
            yield data
            self._save(data)

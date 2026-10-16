"""Encrypted, file-locked, atomically written vault."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterator
from contextlib import contextmanager

from filelock import FileLock

from .config import Settings, validate_key
from .crypto import VaultCipher
from .errors import ConfigError, VaultCorruptedError
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

    def _write_blob(self, blob: bytes) -> None:
        """Атомарно підмінити файл сховища: тимчасовий файл у тій самій теці + ``os.replace``."""
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

    def _save(self, data: VaultData) -> None:
        payload = json.dumps(data.to_dict(), separators=(",", ":")).encode("utf-8")
        self._write_blob(self._cipher.encrypt(payload))

    def rekey(self, new_key: str, *, backup: bool = True) -> Path | None:
        """Перешифрувати сховище іншим ключем (нова сіль); акаунти лишаються як були.

        Усе відбувається під тим самим блокуванням, що й будь-який запис: розшифрувати старим
        ключем → зашифрувати новим → перевірити, що результат розшифровується й збігається →
        атомарно підмінити файл. Збій на будь-якому кроці лишає старий файл недоторканим.
        Повертає шлях до резервної копії старого файла (``None``, якщо ``backup=False``).
        """
        validate_key(new_key)
        self._ensure_home()
        path = self._settings.vault_path
        with self._thread_lock, FileLock(str(self._settings.lock_path), timeout=_LOCK_TIMEOUT):
            if not path.exists():
                raise ConfigError("There is no vault yet: nothing to re-encrypt.")
            if new_key == self._settings.key:
                raise ConfigError("The new key is the same as the current one.")
            data = self.load()  # хибний старий ключ -> InvalidKeyError, нічого не змінено
            payload = json.dumps(data.to_dict(), separators=(",", ":")).encode("utf-8")
            blob = VaultCipher(new_key).encrypt(payload)
            check = VaultCipher(new_key).decrypt(blob)  # незалежний шифр: ключ виводиться заново із солі
            if json.loads(check) != data.to_dict():
                raise VaultCorruptedError("Re-encryption self-check failed; the vault was not changed.")
            backup_path: Path | None = None
            if backup:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                backup_path = path.with_name(f"{path.name}.bak.{stamp}")
                shutil.copy2(path, backup_path)
                with contextlib.suppress(OSError):
                    os.chmod(backup_path, 0o600)
            self._write_blob(blob)
            self._cipher = VaultCipher(new_key)
            self._settings = dataclasses.replace(self._settings, key=new_key)
            return backup_path

    @contextmanager
    def transaction(self) -> Iterator[VaultData]:
        """Yield the vault for modification; it is saved only if the block does not raise."""
        self._ensure_home()
        with self._thread_lock, FileLock(str(self._settings.lock_path), timeout=_LOCK_TIMEOUT):
            data = self.load()
            yield data
            self._save(data)

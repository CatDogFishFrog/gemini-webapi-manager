"""Runtime settings, resolved from arguments first and environment second."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError, MissingKeyError, WeakKeyError

ENV_KEY = "GEMINI_MANAGER_KEY"
ENV_KEY_FILE = "GEMINI_MANAGER_KEY_FILE"
ENV_HOME = "GEMINI_MANAGER_HOME"
ENV_MAX_SLOTS = "GEMINI_MANAGER_MAX_SLOTS"

DEFAULT_MAX_SLOTS = 10
MIN_KEY_LENGTH = 16


def validate_key(key: str | None) -> str:
    """Перевірити ключ шифрування (ті самі правила для ``Settings`` і ``rekey``)."""
    if not key:
        raise MissingKeyError(
            f"Encryption key not set. Export {ENV_KEY} (generate one with `gemini-vault keygen`), "
            f"point {ENV_KEY_FILE} at a file that holds it, or pass key=... explicitly."
        )
    if len(key) < MIN_KEY_LENGTH:
        raise WeakKeyError(f"Encryption key must be at least {MIN_KEY_LENGTH} characters.")
    return key


def read_key_file(path: str | os.PathLike[str]) -> str:
    """Прочитати ключ із файла (Docker/Kubernetes secret); кінцеві переводи рядка відкидаються."""
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read the encryption key file {str(path)!r}: {exc.strerror or exc}") from exc
    return text.rstrip("\r\n")


@dataclass(frozen=True)
class Settings:
    """Resolved configuration.

    ``key`` is a passphrase. The same vault file opened with the same key works on any
    machine, which is how sessions are moved between devices.
    """

    home: Path
    key: str = field(repr=False)
    max_slots: int = DEFAULT_MAX_SLOTS

    @property
    def vault_path(self) -> Path:
        return self.home / "vault.bin"

    @property
    def lock_path(self) -> Path:
        return self.home / "vault.lock"

    @classmethod
    def from_env(
        cls,
        *,
        home: str | os.PathLike[str] | None = None,
        key: str | None = None,
        max_slots: int | None = None,
    ) -> "Settings":
        # Пріоритет: аргумент -> GEMINI_MANAGER_KEY -> файл з GEMINI_MANAGER_KEY_FILE.
        resolved_key = key if key is not None else os.environ.get(ENV_KEY)
        if not resolved_key and os.environ.get(ENV_KEY_FILE):
            resolved_key = read_key_file(os.environ[ENV_KEY_FILE])
        validate_key(resolved_key)

        raw_home = home if home is not None else os.environ.get(ENV_HOME)
        resolved_home = (
            Path(raw_home).expanduser() if raw_home else Path.home() / ".gemini-webapi-manager"
        )

        if max_slots is None:
            raw_slots = os.environ.get(ENV_MAX_SLOTS)
            try:
                max_slots = int(raw_slots) if raw_slots else DEFAULT_MAX_SLOTS
            except ValueError as exc:
                raise ConfigError(f"{ENV_MAX_SLOTS} must be an integer, got {raw_slots!r}") from exc
        if max_slots < 1:
            raise ConfigError("max_slots must be at least 1.")

        return cls(home=resolved_home, key=resolved_key, max_slots=max_slots)

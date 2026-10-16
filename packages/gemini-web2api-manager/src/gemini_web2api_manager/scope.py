"""Облікові дані «поточного запиту».

Апстрім gemini-web2api читає cookie, xsrf-токен, ``bl`` і проксі з глобального ``CONFIG``. Шлюз
обслуговує запити в окремих потоках і від різних акаунтів, тож ці значення підміняються
потокобезпечно: ``ScopedConfig`` віддає значення активного запиту, а решту — з базового CONFIG.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

_local = threading.local()


@dataclass(frozen=True)
class Credentials:
    """Усе, що потрібно апстріму, щоб говорити з Gemini від імені одного акаунта."""

    slot: int
    cookie_header: str = field(repr=False)
    sapisid: str | None = field(default=None, repr=False)
    xsrf_token: str | None = field(default=None, repr=False)
    build_label: str | None = None
    proxy: str | None = field(default=None, repr=False)


def current() -> Credentials | None:
    return getattr(_local, "credentials", None)


def current_hold() -> Any | None:
    """Буфер відповіді поточної спроби (див. gateway.HoldWriter)."""
    return getattr(_local, "hold", None)


@contextmanager
def use(credentials: Credentials, hold: Any | None = None) -> Iterator[None]:
    previous = (current(), current_hold())
    _local.credentials, _local.hold = credentials, hold
    try:
        yield
    finally:
        _local.credentials, _local.hold = previous


_OVERRIDES = {
    "xsrf_token": lambda c: c.xsrf_token,
    "gemini_bl": lambda c: c.build_label,
    "proxy": lambda c: c.proxy,
    "auth_user": lambda c: None,  # окремий акаунт = окремі cookie, індекс /u/N не потрібен
    "cookie_file": lambda c: None,
}
_FALL_BACK_TO_BASE = {"gemini_bl", "proxy"}  # якщо в облікових даних порожньо — діє глобальне


class ScopedConfig(MutableMapping):
    """Подібний до dict вигляд CONFIG, який бачить значення поточного запиту."""

    def __init__(self, base: dict[str, Any]) -> None:
        self._base = base

    def __getitem__(self, key: str) -> Any:
        creds = current()
        if creds is not None and key in _OVERRIDES:
            value = _OVERRIDES[key](creds)
            if value is not None or key not in _FALL_BACK_TO_BASE:
                return value
        return self._base[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._base[key] = value

    def __delitem__(self, key: str) -> None:
        del self._base[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._base)

    def __len__(self) -> int:
        return len(self._base)

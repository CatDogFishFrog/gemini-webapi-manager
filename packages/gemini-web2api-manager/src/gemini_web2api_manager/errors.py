"""Класифікація відмов апстріму: що робити — перемикнути акаунт, оновити сесію чи просто видати помилку."""

from __future__ import annotations

import re
from enum import Enum


class Failure(str, Enum):
    QUOTA = "quota"  # вичерпано ліміт моделі/акаунта (BardErrorInfo 1037)
    RATE = "rate"  # тимчасове обмеження частоти (HTTP 429)
    AUTH = "auth"  # сесія/токен недійсні (HTTP 401/403, xsrf)
    BLOCKED = "blocked"  # IP/акаунт тимчасово заблоковано (1060 тощо)


_BARD = re.compile(r"BardErrorInfo\s*\[(\d+)\]")
_QUOTA_CODES = {1037}
_BLOCK_CODES = {1060}


def _status_code(exc: BaseException) -> int | None:
    code = getattr(exc, "code", None)  # urllib.error.HTTPError
    if isinstance(code, int):
        return code
    response = getattr(exc, "response", None)  # httpx.HTTPStatusError
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def classify(exc: BaseException) -> Failure | None:
    """None — це не те, що лікується заміною акаунта (мережа, некоректний запит тощо)."""
    text = str(exc)
    match = _BARD.search(text)
    if match:
        code = int(match.group(1))
        if code in _QUOTA_CODES:
            return Failure.QUOTA
        if code in _BLOCK_CODES:
            return Failure.BLOCKED
    status = _status_code(exc)
    if status == 429:
        return Failure.RATE
    if status in (401, 403):
        return Failure.AUTH
    if status == 400 and "xsrf" in text.lower():
        return Failure.AUTH
    return None

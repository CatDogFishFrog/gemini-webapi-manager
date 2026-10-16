"""Конфігурація шлюзу.

Файл — це звичайний ``config.json`` апстріму gemini-web2api (host, port, api_keys, retry_*,
default_model, temporary_chats, ...). Налаштування самого менеджера лежать у блоці ``"manager"``:

.. code-block:: json

    {"port": 8081, "api_keys": ["sk-me"],
     "manager": {"strategy": "sticky", "refresh_interval": 600, "default_cooldown": 1800}}

Поля ``cookie_file``, ``xsrf_token`` і ``auth_user`` ігноруються: сесіями керує vault.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STRATEGIES = ("sticky", "max-remaining")

# Слово в назві моделі -> «родина» квот (як вона називається в лімітах акаунта).
DEFAULT_FAMILIES = {"pro": "pro", "flash": "flash", "lite": "flash", "thinking": "flash", "auto": "flash"}


@dataclass
class GatewayConfig:
    strategy: str = "sticky"  # sticky: тримати акаунт, доки не вичерпається; max-remaining: завжди найбільший залишок
    refresh_interval: float = 600.0  # як часто оновлювати ліміти й cookie, с
    default_cooldown: float = 1800.0  # пауза акаунта після ліміту, якщо час скидання невідомий, с
    rate_cooldown: float = 60.0  # пауза після HTTP 429, с
    switch_threshold: float = 100.0  # вважати акаунт вичерпаним від цього % використання
    persist_active: bool = True  # записувати поточний акаунт у vault як активний
    failover_on_empty: bool = False  # порожню відповідь вважати відмовою й пробувати інший акаунт
    slots: list[int] | None = None  # None — усі слоти
    families: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FAMILIES))
    upstream: dict[str, Any] = field(default_factory=dict)  # те, що піде в CONFIG апстріму

    def __post_init__(self) -> None:
        if self.strategy not in STRATEGIES:
            raise ValueError(f"strategy must be one of {STRATEGIES}, got {self.strategy!r}")

    def family_for(self, model_name: str | None) -> str | None:
        name = (model_name or "").lower()
        for needle, family in self.families.items():
            if needle in name:
                return family
        return None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GatewayConfig":
        data = dict(data)
        manager = dict(data.pop("manager", {}) or {})
        for ignored in ("cookie_file", "xsrf_token", "auth_user"):
            data.pop(ignored, None)
        families = {**DEFAULT_FAMILIES, **(manager.pop("families", {}) or {})}
        known = {k: v for k, v in manager.items() if k in cls.__dataclass_fields__ and k not in ("upstream", "families")}
        return cls(families=families, upstream=data, **known)

    @classmethod
    def load(cls, path: str | Path | None) -> "GatewayConfig":
        if path is None:
            return cls()
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

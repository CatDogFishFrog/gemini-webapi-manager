"""Data models: what is stored in the vault, and the public, secret-free Account view."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Any

from .usage import UsageSnapshot

VAULT_FORMAT_VERSION = 1


@dataclass
class StoredAccount:
    """Internal record. Contains secrets (cookies) - never returned to callers."""

    slot: int
    cookies: dict[str, str]
    created_at: str
    email: str | None = None
    display_name: str | None = None
    proxy: str | None = None
    status: str | None = None
    usage: dict[str, Any] | None = None
    last_used_at: str | None = None
    last_limit_hit_at: str | None = None
    cookies_synced_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StoredAccount":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class VaultData:
    accounts: dict[int, StoredAccount] = field(default_factory=dict)
    active_slot: int | None = None
    version: int = VAULT_FORMAT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "active_slot": self.active_slot,
            "accounts": {str(slot): acc.to_dict() for slot, acc in self.accounts.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VaultData":
        accounts = {
            int(slot): StoredAccount.from_dict(raw)
            for slot, raw in (data.get("accounts") or {}).items()
        }
        active = data.get("active_slot")
        return cls(
            accounts=accounts,
            active_slot=active if active in accounts else None,
            version=data.get("version", VAULT_FORMAT_VERSION),
        )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class Account:
    """A connected account. Safe to print/serialise: it never carries cookies or proxy URLs.

    Extend by adding fields here and in ``to_dict``.
    """

    slot: int
    is_active: bool
    email: str | None
    display_name: str | None
    status: str | None
    usage: UsageSnapshot | None
    has_proxy: bool
    created_at: datetime | None
    last_used_at: datetime | None
    last_limit_hit_at: datetime | None

    @property
    def usage_percent(self) -> float | int | None:
        """Percent of the limit used at the time of the last usage refresh (None if unknown)."""
        return self.usage.percent if self.usage else None

    @classmethod
    def from_stored(cls, stored: StoredAccount, *, active_slot: int | None) -> "Account":
        return cls(
            slot=stored.slot,
            is_active=stored.slot == active_slot,
            email=stored.email,
            display_name=stored.display_name,
            status=stored.status,
            usage=UsageSnapshot.from_dict(stored.usage),
            has_proxy=bool(stored.proxy),
            created_at=_parse_dt(stored.created_at),
            last_used_at=_parse_dt(stored.last_used_at),
            last_limit_hit_at=_parse_dt(stored.last_limit_hit_at),
        )

    def to_dict(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "slot": self.slot,
            "is_active": self.is_active,
            "email": self.email,
            "display_name": self.display_name,
            "usage_percent": self.usage_percent,
            "usage": self.usage.to_dict() if self.usage else None,
            "status": self.status,
            "has_proxy": self.has_proxy,
            "created_at": iso(self.created_at),
            "last_used_at": iso(self.last_used_at),
            "last_limit_hit_at": iso(self.last_limit_hit_at),
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @staticmethod
    def list_to_json(accounts: list["Account"], *, indent: int | None = None) -> str:
        return json.dumps([a.to_dict() for a in accounts], ensure_ascii=False, indent=indent)

    def __str__(self) -> str:
        pct = "?" if self.usage_percent is None else f"{self.usage_percent:.0f}%"
        mark = "*" if self.is_active else " "
        return f"#{self.slot}{mark} {self.email or 'unknown'} ({pct})"

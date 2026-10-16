"""Account usage/limit snapshots.

The numbers come from gemini-webapi itself (``client.usage_info`` / ``client.quotas``);
this module only normalises them into something JSON-friendly and storable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_EXCLUDED_QUOTA_KEYS = frozenset({"usage_info", "extra"})


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _num(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _ts_to_iso(value: Any) -> str | None:
    number = _num(value)
    if not number:
        return None
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).isoformat(timespec="seconds")
    except (ValueError, OSError, OverflowError):
        return None


@dataclass(frozen=True)
class UsageWindow:
    """Usage of one limit window (e.g. the rolling 5-hour or the weekly window)."""

    percent: float | int | None = None
    reset_at: str | None = None
    remaining_credits: float | int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "percent": self.percent,
            "reset_at": self.reset_at,
            "remaining_credits": self.remaining_credits,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "UsageWindow | None":
        if not isinstance(data, dict):
            return None
        return cls(
            percent=_num(data.get("percent")),
            reset_at=data.get("reset_at") if isinstance(data.get("reset_at"), str) else None,
            remaining_credits=_num(data.get("remaining_credits")),
        )

    @classmethod
    def from_library(cls, raw: Any) -> "UsageWindow | None":
        if not isinstance(raw, dict):
            return None
        reset_at = raw.get("reset_at")
        return cls(
            percent=_num(raw.get("usage_percentage")),
            reset_at=reset_at if isinstance(reset_at, str) else None,
            remaining_credits=_num(raw.get("remaining_credits")),
        )


@dataclass(frozen=True)
class UsageSnapshot:
    """Last known usage of an account, as reported by Gemini at ``updated_at``."""

    tier: str | None = None
    five_hour: UsageWindow | None = None
    weekly: UsageWindow | None = None
    ai_credits_remaining: float | int | None = None
    model_quotas: list[dict[str, Any]] = field(default_factory=list)
    updated_at: str = field(default_factory=utcnow_iso)

    @property
    def percent(self) -> float | int | None:
        """Share of the limit used: the highest of the known windows (the binding one)."""
        values = [w.percent for w in (self.five_hour, self.weekly) if w and w.percent is not None]
        if not values:
            values = [
                q["usage_percentage"]
                for q in self.model_quotas
                if _num(q.get("usage_percentage")) is not None
            ]
        return max(values) if values else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "five_hour": self.five_hour.to_dict() if self.five_hour else None,
            "weekly": self.weekly.to_dict() if self.weekly else None,
            "ai_credits_remaining": self.ai_credits_remaining,
            "model_quotas": list(self.model_quotas),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "UsageSnapshot | None":
        if not isinstance(data, dict):
            return None
        quotas = data.get("model_quotas")
        return cls(
            tier=data.get("tier") if isinstance(data.get("tier"), str) else None,
            five_hour=UsageWindow.from_dict(data.get("five_hour")),
            weekly=UsageWindow.from_dict(data.get("weekly")),
            ai_credits_remaining=_num(data.get("ai_credits_remaining")),
            model_quotas=[q for q in quotas if isinstance(q, dict)] if isinstance(quotas, list) else [],
            updated_at=data.get("updated_at") or utcnow_iso(),
        )


def snapshot_from_client(client: Any) -> UsageSnapshot | None:
    """Build a snapshot from an initialised ``GeminiClient``; None if it exposes no usage data."""
    info = getattr(client, "usage_info", None) or {}
    quotas = getattr(client, "quotas", None) or {}
    if not isinstance(info, dict):
        info = {}
    if not isinstance(quotas, dict):
        quotas = {}

    tier_raw = info.get("tier")
    tier = tier_raw.get("label") if isinstance(tier_raw, dict) else None
    five_hour = UsageWindow.from_library(info.get("current_5h"))
    weekly = UsageWindow.from_library(info.get("weekly"))
    credits = _num(info.get("ai_credits_remaining"))

    model_quotas: list[dict[str, Any]] = []
    for quota_id, quota in quotas.items():
        if quota_id in _EXCLUDED_QUOTA_KEYS or not isinstance(quota, dict):
            continue
        model_quotas.append(
            {
                "id": str(quota_id),
                "label": quota.get("label") if isinstance(quota.get("label"), str) else None,
                "usage_percentage": _num(quota.get("usage_percentage")),
                "reset_at": _ts_to_iso(quota.get("reset_time")),
                "total": _num(quota.get("total")),
                "remaining": _num(quota.get("remaining")),
                "window": quota.get("window") if isinstance(quota.get("window"), str) else None,
            }
        )

    if not (tier or five_hour or weekly or credits is not None or model_quotas):
        return None
    return UsageSnapshot(
        tier=tier if isinstance(tier, str) else None,
        five_hour=five_hour,
        weekly=weekly,
        ai_credits_remaining=credits,
        model_quotas=model_quotas,
    )

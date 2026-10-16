"""Which account should serve the next request? A pure policy, no I/O.

Used by the web2api gateway to fail over when an account runs out of quota, but it only deals
in plain data so any other front-end can reuse it.

Rules, in order:

1. An account is *eligible* if it is usable (session valid, status not hostile), not in a
   cool-down after a recent limit hit, and not exhausted according to its last usage snapshot.
2. A usage snapshot ages: a window whose reset time has already passed counts as empty, even if
   the snapshot still says 100 %.
3. Among eligible accounts the one with the **most remaining limit** wins (ties: the one used
   least recently, then the lowest slot). With ``prefer`` (sticky mode) the preferred account is
   kept for as long as it stays eligible.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .usage import UsageSnapshot

# Account states (``AccountStatus`` names in gemini-webapi) that can still serve requests.
USABLE_STATUSES = frozenset({None, "AVAILABLE", "ACCOUNT_UNTRUSTED"})

UNKNOWN_REMAINING = 50.0  # accounts with no usage data rank below known-healthy ones
EXHAUSTED_AT = 100.0


def parse_time(value: Any) -> datetime | None:
    """ISO string / datetime -> aware datetime (naive values are taken as UTC)."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Candidate:
    """What the policy needs to know about one account."""

    slot: int
    usage: UsageSnapshot | None = None
    status: str | None = None
    last_used_at: datetime | None = None
    blocked_until: datetime | None = None  # cool-down after a limit hit
    usable: bool = True  # False once the session is known to be dead


def _effective(percent: Any, reset_at: Any, now: datetime) -> float | None:
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        return None
    reset = parse_time(reset_at)
    if reset is not None and reset <= now:
        return 0.0  # the window has reset since the snapshot was taken
    return float(percent)


def _windows(usage: UsageSnapshot, family: str | None) -> list[tuple[float | None, datetime | None]]:
    """(percent-used, reset) for every window that can block ``family``."""
    result: list[tuple[float | None, datetime | None]] = []
    for window in (usage.five_hour, usage.weekly):
        if window is not None:
            result.append((window.percent, parse_time(window.reset_at)))
    if family:
        wanted = family.lower()
        for quota in usage.model_quotas:
            if wanted in str(quota.get("label") or "").lower():
                result.append((quota.get("usage_percentage"), parse_time(quota.get("reset_at"))))
    return result


def used_percent(
    usage: UsageSnapshot | None, family: str | None = None, now: datetime | None = None
) -> float | None:
    """Percent of the binding limit used (highest relevant window), or None if unknown."""
    if usage is None:
        return None
    now = now or utcnow()
    values = [
        value
        for percent, reset in _windows(usage, family)
        if (value := _effective(percent, reset, now)) is not None
    ]
    return max(values) if values else None


def remaining_percent(
    usage: UsageSnapshot | None, family: str | None = None, now: datetime | None = None
) -> float | None:
    used = used_percent(usage, family, now)
    return None if used is None else max(0.0, 100.0 - used)


def recovery_time(
    usage: UsageSnapshot | None, family: str | None = None, now: datetime | None = None
) -> datetime | None:
    """When every exhausted window of ``family`` will have reset (None: nothing is exhausted)."""
    if usage is None:
        return None
    now = now or utcnow()
    pending: list[datetime] = []
    for percent, reset in _windows(usage, family):
        value = _effective(percent, reset, now)
        if value is not None and value >= EXHAUSTED_AT and reset is not None and reset > now:
            pending.append(reset)
    return max(pending) if pending else None


def is_eligible(
    candidate: Candidate,
    *,
    family: str | None = None,
    now: datetime | None = None,
    threshold: float = EXHAUSTED_AT,
) -> bool:
    now = now or utcnow()
    if not candidate.usable or candidate.status not in USABLE_STATUSES:
        return False
    if candidate.blocked_until is not None and candidate.blocked_until > now:
        return False
    used = used_percent(candidate.usage, family, now)
    return used is None or used < threshold


def rank(
    candidates: Iterable[Candidate],
    *,
    family: str | None = None,
    now: datetime | None = None,
    exclude: Collection[int] = (),
    threshold: float = EXHAUSTED_AT,
) -> list[Candidate]:
    """Eligible candidates, best first: most remaining limit, then least recently used."""
    now = now or utcnow()
    oldest = datetime.min.replace(tzinfo=timezone.utc)

    def key(candidate: Candidate) -> tuple[float, datetime, int]:
        remaining = remaining_percent(candidate.usage, family, now)
        score = UNKNOWN_REMAINING if remaining is None else remaining
        return (-score, candidate.last_used_at or oldest, candidate.slot)

    eligible = [
        c
        for c in candidates
        if c.slot not in exclude and is_eligible(c, family=family, now=now, threshold=threshold)
    ]
    return sorted(eligible, key=key)


def pick(
    candidates: Iterable[Candidate],
    *,
    family: str | None = None,
    now: datetime | None = None,
    exclude: Collection[int] = (),
    prefer: int | None = None,
    threshold: float = EXHAUSTED_AT,
) -> Candidate | None:
    """The account to use next, or None if nobody can serve the request right now.

    With ``prefer`` (sticky mode) that slot is returned as long as it is eligible; only when it
    is not, the best of the others is chosen.
    """
    ranked = rank(candidates, family=family, now=now, exclude=exclude, threshold=threshold)
    if prefer is not None:
        for candidate in ranked:
            if candidate.slot == prefer:
                return candidate
    return ranked[0] if ranked else None


def earliest_available(
    candidates: Iterable[Candidate], *, family: str | None = None, now: datetime | None = None
) -> datetime | None:
    """When the first currently-unavailable (but healthy) account is expected to recover."""
    now = now or utcnow()
    soonest: datetime | None = None
    for candidate in candidates:
        if not candidate.usable or candidate.status not in USABLE_STATUSES:
            continue
        moments = [
            moment
            for moment in (
                candidate.blocked_until if candidate.blocked_until and candidate.blocked_until > now else None,
                recovery_time(candidate.usage, family, now),
            )
            if moment is not None
        ]
        if not moments:
            continue
        ready = max(moments)  # both the cool-down and the exhausted windows must be over
        if soonest is None or ready < soonest:
            soonest = ready
    return soonest

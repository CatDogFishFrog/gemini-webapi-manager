"""AccountPool: потокобезпечний стан акаунтів шлюзу та політика перемикання.

Пул нічого не знає про HTTP чи Google: йому передають облікові дані й знімки лімітів (від
``SessionKeeper``), а він на кожен запит каже, який акаунт брати, і реагує на звіти про відмови.

* ``sticky`` (типово): тримається поточного акаунта, доки той придатний; щойно ліміт вичерпано —
  переходить на акаунт із **найбільшим залишком** ліміту.
* ``max-remaining``: кожен запит іде на акаунт із найбільшим залишком.

Ліміти рахуються по «родинах» моделей (``pro``/``flash``): вичерпаний Pro не блокує Flash.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from gemini_vault import selection
from gemini_vault.selection import Candidate, utcnow
from gemini_vault.usage import UsageSnapshot

from .errors import Failure
from .scope import Credentials

log = logging.getLogger(__name__)

MAX_COOLDOWN = timedelta(days=8)
ANY_FAMILY = "*"


@dataclass
class _Block:
    until: datetime
    from_reset: bool  # True: час узято зі скидання вікна ліміту; False: типова пауза


@dataclass
class SlotState:
    slot: int
    email: str | None = None
    credentials: Credentials | None = field(default=None, repr=False)
    usage: UsageSnapshot | None = None
    status: str | None = None
    last_used_at: datetime | None = None
    blocks: dict[str, _Block] = field(default_factory=dict)
    expired: bool = False
    last_error: str | None = None
    requests: int = 0
    limit_hits: int = 0
    refreshed_at: datetime | None = None


@dataclass(frozen=True)
class Lease:
    """Дозвіл використати акаунт для одного запиту."""

    slot: int
    credentials: Credentials = field(repr=False)
    family: str | None


class AccountPool:
    def __init__(
        self,
        *,
        strategy: str = "sticky",
        default_cooldown: float = 1800.0,
        rate_cooldown: float = 60.0,
        threshold: float = 100.0,
        on_switch: Callable[[int | None, int, str], None] | None = None,
        on_limit: Callable[[int], None] | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.strategy = strategy
        self.default_cooldown = default_cooldown
        self.rate_cooldown = rate_cooldown
        self.threshold = threshold
        self._on_switch = on_switch
        self._on_limit = on_limit
        self._now = clock
        self._lock = threading.RLock()
        self._slots: dict[int, SlotState] = {}
        self._current: int | None = None

    # ------------------------------------------------------------------ оновлення від keeper
    def update_slot(
        self,
        slot: int,
        *,
        email: str | None,
        credentials: Credentials | None,
        usage: UsageSnapshot | None,
        status: str | None,
    ) -> None:
        with self._lock:
            state = self._slots.setdefault(slot, SlotState(slot))
            state.email = email
            state.credentials = credentials
            state.usage = usage
            state.status = status
            state.expired = status == "UNAUTHENTICATED" or credentials is None
            state.last_error = None
            state.refreshed_at = self._now()
            self._rebase_blocks(state)

    def _rebase_blocks(self, state: SlotState) -> None:
        """Нові дані про ліміти уточнюють паузу, що її виставили навмання."""
        for family, block in list(state.blocks.items()):
            if block.until <= self._now():
                del state.blocks[family]
                continue
            if not block.from_reset:
                recovery = selection.recovery_time(state.usage, None if family == ANY_FAMILY else family, self._now())
                if recovery is not None:
                    state.blocks[family] = _Block(min(recovery, self._now() + MAX_COOLDOWN), True)

    def remove_slot(self, slot: int) -> None:
        with self._lock:
            self._slots.pop(slot, None)
            if self._current == slot:
                self._current = None

    def mark_expired(self, slot: int, reason: str) -> None:
        with self._lock:
            state = self._slots.setdefault(slot, SlotState(slot))
            state.expired = True
            state.last_error = reason
        log.warning("Slot %s: session is no longer valid (%s). Re-login: `login --console %s --replace`.", slot, reason, slot)

    def note_error(self, slot: int, message: str) -> None:
        with self._lock:
            if slot in self._slots:
                self._slots[slot].last_error = message

    # ------------------------------------------------------------------ вибір
    def _candidates(self, family: str | None) -> list[Candidate]:
        key = family or ANY_FAMILY
        result = []
        for state in self._slots.values():
            block = state.blocks.get(key) or state.blocks.get(ANY_FAMILY)
            result.append(
                Candidate(
                    slot=state.slot,
                    usage=state.usage,
                    status=state.status,
                    last_used_at=state.last_used_at,
                    blocked_until=block.until if block else None,
                    usable=state.credentials is not None and not state.expired,
                )
            )
        return result

    def acquire(self, family: str | None = None, *, exclude: set[int] | frozenset[int] = frozenset()) -> Lease | None:
        """Акаунт для наступного запиту або None, якщо нікого придатного немає."""
        with self._lock:
            now = self._now()
            prefer = self._current if self.strategy == "sticky" else None
            chosen = selection.pick(
                self._candidates(family), family=family, now=now, exclude=exclude,
                prefer=prefer, threshold=self.threshold,
            )
            if chosen is None:
                return None
            state = self._slots[chosen.slot]
            if self._current != chosen.slot:
                previous, self._current = self._current, chosen.slot
                reason = "initial" if previous is None else "previous account unavailable"
                log.info("Switching account: %s -> %s (%s)", previous, chosen.slot, reason)
                self._fire_switch(previous, chosen.slot, reason)
            assert state.credentials is not None
            return Lease(chosen.slot, state.credentials, family)

    def _fire_switch(self, previous: int | None, new: int, reason: str) -> None:
        if self._on_switch is not None:
            try:
                self._on_switch(previous, new, reason)
            except Exception:  # noqa: BLE001 - побічний ефект не має ламати запит
                log.exception("on_switch callback failed")

    def force_use(self, slot: int) -> None:
        """Примусово зробити акаунт поточним (для sticky)."""
        with self._lock:
            if slot not in self._slots or self._slots[slot].credentials is None:
                raise KeyError(slot)
            previous, self._current = self._current, slot
        if previous != slot:
            self._fire_switch(previous, slot, "manual")

    def block(self, slot: int, seconds: float) -> None:
        """Вручну вивести акаунт з ротації (перевірка перемикання, обслуговування)."""
        with self._lock:
            if slot not in self._slots:
                raise KeyError(slot)
            self._slots[slot].blocks[ANY_FAMILY] = _Block(self._now() + timedelta(seconds=seconds), False)

    def unblock(self, slot: int) -> None:
        with self._lock:
            if slot not in self._slots:
                raise KeyError(slot)
            self._slots[slot].blocks.clear()

    @property
    def current(self) -> int | None:
        return self._current

    # ------------------------------------------------------------------ звіти про результат
    def report_success(self, lease: Lease) -> None:
        with self._lock:
            state = self._slots.get(lease.slot)
            if state is not None:
                state.requests += 1
                state.last_used_at = self._now()

    def report_failure(self, lease: Lease, failure: Failure, message: str = "") -> None:
        """Занести відмову й виставити паузу; повторний вибір дасть уже інший акаунт."""
        with self._lock:
            state = self._slots.get(lease.slot)
            if state is None:
                return
            state.last_error = f"{failure.value}: {message}"[:300]
            now = self._now()
            key = lease.family or ANY_FAMILY
            if failure is Failure.QUOTA:
                state.limit_hits += 1
                recovery = selection.recovery_time(state.usage, lease.family, now)
                if recovery is not None:
                    state.blocks[key] = _Block(min(recovery, now + MAX_COOLDOWN), True)
                else:
                    state.blocks[key] = _Block(now + timedelta(seconds=self.default_cooldown), False)
            elif failure in (Failure.RATE, Failure.BLOCKED):
                state.blocks[ANY_FAMILY] = _Block(now + timedelta(seconds=self.rate_cooldown), False)
            elif failure is Failure.AUTH:
                # Токен міг просто застаріти: keeper оновить сесію, а до того — пауза.
                state.blocks[ANY_FAMILY] = _Block(now + timedelta(seconds=self.rate_cooldown), False)
        log.warning("Slot %s: %s (%s) -> trying another account", lease.slot, failure.value, message[:120])
        if failure in (Failure.QUOTA, Failure.AUTH) and self._on_limit is not None:
            try:
                self._on_limit(lease.slot)  # keeper отримає свіжі ліміти й уточнить паузу
            except Exception:  # noqa: BLE001
                log.exception("on_limit callback failed")

    # ------------------------------------------------------------------ огляд
    def retry_after(self, family: str | None = None) -> float | None:
        """Через скільки секунд очікується перший придатний акаунт (None — невідомо)."""
        with self._lock:
            moment = selection.earliest_available(self._candidates(family), family=family, now=self._now())
        if moment is None:
            return None
        return max(1.0, (moment - self._now()).total_seconds())

    def has_usable(self) -> bool:
        with self._lock:
            return any(s.credentials is not None and not s.expired for s in self._slots.values())

    def status(self) -> dict[str, Any]:
        """Знімок без секретів (cookie/токени сюди не потрапляють)."""
        with self._lock:
            now = self._now()
            out = []
            for state in sorted(self._slots.values(), key=lambda s: s.slot):
                out.append(
                    {
                        "slot": state.slot,
                        "email": state.email,
                        "current": state.slot == self._current,
                        "usable": state.credentials is not None and not state.expired,
                        "status": state.status,
                        "used_percent": selection.used_percent(state.usage, None, now),
                        "used_percent_by_family": {
                            fam: selection.used_percent(state.usage, fam, now) for fam in ("pro", "flash")
                        },
                        "blocked": {
                            fam: {"until": b.until.isoformat(), "from_reset": b.from_reset}
                            for fam, b in state.blocks.items() if b.until > now
                        },
                        "requests": state.requests,
                        "limit_hits": state.limit_hits,
                        "last_used_at": state.last_used_at.isoformat() if state.last_used_at else None,
                        "last_error": state.last_error,
                        "refreshed_at": state.refreshed_at.isoformat() if state.refreshed_at else None,
                    }
                )
            return {"strategy": self.strategy, "current": self._current, "accounts": out}

"""AccountPool: перемикання, паузи, оновлення лімітів, класифікація відмов."""

from datetime import datetime, timedelta, timezone

import pytest

from gemini_vault.usage import UsageSnapshot, UsageWindow
from gemini_web2api_manager.errors import Failure, classify
from gemini_web2api_manager.pool import AccountPool
from gemini_web2api_manager.scope import Credentials

T0 = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


def creds(slot):
    return Credentials(slot=slot, cookie_header=f"SID=s{slot}", xsrf_token=f"x{slot}")


def snap(five, reset_hours=2):
    return UsageSnapshot(five_hour=UsageWindow(percent=five, reset_at=(T0 + timedelta(hours=reset_hours)).isoformat()))


@pytest.fixture
def clock():
    return Clock()


def make_pool(clock, usages, **kw):
    switches = []
    pool = AccountPool(clock=clock, on_switch=lambda a, b, r: switches.append((a, b)), **kw)
    for slot, pct in usages.items():
        pool.update_slot(slot, email=f"u{slot}@x", credentials=creds(slot), usage=snap(pct), status="AVAILABLE")
    return pool, switches


def test_first_pick_is_most_remaining_then_sticky(clock):
    pool, switches = make_pool(clock, {1: 50, 2: 10, 3: 30})
    assert pool.acquire().slot == 2
    pool.update_slot(2, email="u2@x", credentials=creds(2), usage=snap(60), status="AVAILABLE")
    assert pool.acquire().slot == 2  # sticky: тримаємось, хоч у 3-го залишок більший
    assert switches == [(None, 2)]


def test_quota_failure_switches_to_most_remaining_not_next_by_number(clock):
    pool, switches = make_pool(clock, {1: 5, 2: 90, 3: 20})
    first = pool.acquire()
    assert first.slot == 1
    pool.report_failure(first, Failure.QUOTA, "1037")
    assert pool.acquire().slot == 3  # 3-й має 80% залишку проти 10% у 2-го
    assert switches == [(None, 1), (1, 3)]


def test_cooldown_uses_window_reset_when_exhausted_else_default(clock):
    pool, _ = make_pool(clock, {1: 100, 2: 10}, default_cooldown=600)
    pool.update_slot(1, email="u1@x", credentials=creds(1), usage=snap(100, 2), status="AVAILABLE")
    lease = pool.acquire()
    assert lease.slot == 2
    pool.report_failure(lease, Failure.QUOTA)  # 2-й: знімок не показує вичерпання -> типова пауза
    blocked = pool.status()["accounts"][1]["blocked"]["*"]
    assert blocked["from_reset"] is False
    assert pool.acquire() is None
    assert 1 < pool.retry_after() <= 600 + 1
    clock.now += timedelta(seconds=601)
    assert pool.acquire().slot == 2  # пауза минула


def test_refresh_after_limit_refines_cooldown_to_reset_time(clock):
    pool, _ = make_pool(clock, {1: 40})
    lease = pool.acquire()
    pool.report_failure(lease, Failure.QUOTA)
    assert pool.status()["accounts"][0]["blocked"]["*"]["from_reset"] is False
    pool.update_slot(1, email="u1@x", credentials=creds(1), usage=snap(100, 3), status="AVAILABLE")
    block = pool.status()["accounts"][0]["blocked"]["*"]
    assert block["from_reset"] is True and block["until"] == (T0 + timedelta(hours=3)).isoformat()
    clock.now += timedelta(hours=3, seconds=1)
    assert pool.acquire().slot == 1


def test_family_blocks_are_independent(clock):
    pool, _ = make_pool(clock, {1: 10, 2: 50})
    lease = pool.acquire("pro")
    assert lease.slot == 1
    pool.report_failure(lease, Failure.QUOTA)
    assert pool.acquire("pro").slot == 2
    assert pool.acquire("flash").slot == 2  # sticky залишився на 2; 1 для flash теж був би придатний
    assert pool.status()["accounts"][0]["blocked"].keys() == {"pro"}


def test_exclude_prevents_retrying_the_same_slot(clock):
    pool, _ = make_pool(clock, {1: 10, 2: 20})
    assert pool.acquire(exclude={1}).slot == 2
    assert pool.acquire(exclude={1, 2}) is None


def test_max_remaining_strategy_always_routes_to_best(clock):
    pool, _ = make_pool(clock, {1: 10, 2: 30}, strategy="max-remaining")
    assert pool.acquire().slot == 1
    pool.update_slot(1, email="u1@x", credentials=creds(1), usage=snap(50), status="AVAILABLE")
    assert pool.acquire().slot == 2


def test_expired_session_is_never_picked_and_status_hides_secrets(clock):
    pool, _ = make_pool(clock, {1: 10, 2: 20})
    pool.mark_expired(1, "test")
    assert pool.acquire().slot == 2
    dump = str(pool.status())
    assert "SID=" not in dump and "x1" not in dump and "x2" not in dump
    pool.update_slot(2, email="u2@x", credentials=None, usage=None, status="UNAUTHENTICATED")
    assert pool.acquire() is None and not pool.has_usable()


def test_callbacks_fire_for_limit_and_force_use(clock):
    limits = []
    pool = AccountPool(clock=clock, on_limit=limits.append)
    for s in (1, 2):
        pool.update_slot(s, email=None, credentials=creds(s), usage=snap(10), status="AVAILABLE")
    lease = pool.acquire()
    pool.report_failure(lease, Failure.QUOTA)
    assert limits == [lease.slot]
    pool.force_use(2)
    assert pool.current == 2
    with pytest.raises(KeyError):
        pool.force_use(9)


def test_classify_upstream_errors():
    class Http(Exception):
        def __init__(self, code):
            self.code = code
    assert classify(RuntimeError("Gemini upstream rejected request: BardErrorInfo [1037]")) is Failure.QUOTA
    assert classify(RuntimeError("... BardErrorInfo [1060]")) is Failure.BLOCKED
    assert classify(RuntimeError("... BardErrorInfo [1013]")) is None
    assert classify(Http(429)) is Failure.RATE
    assert classify(Http(403)) is Failure.AUTH
    assert classify(Http(400)) is None
    assert classify(RuntimeError("HTTP 400 bad xsrf token")) is None  # без коду статусу не гадаємо
    assert classify(ConnectionError("boom")) is None

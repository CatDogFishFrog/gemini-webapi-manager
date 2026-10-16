"""Політика вибору акаунта (чиста логіка)."""

from datetime import datetime, timedelta, timezone

from gemini_vault import selection as sel
from gemini_vault.usage import UsageSnapshot, UsageWindow

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)


def iso(delta_hours: float) -> str:
    return (NOW + timedelta(hours=delta_hours)).isoformat()


def usage(five=None, week=None, quotas=()):
    return UsageSnapshot(
        five_hour=UsageWindow(percent=five, reset_at=iso(2)) if five is not None else None,
        weekly=UsageWindow(percent=week, reset_at=iso(48)) if week is not None else None,
        model_quotas=list(quotas),
    )


def cand(slot, u=None, **kw):
    return sel.Candidate(slot, u, **kw)


def test_most_remaining_wins_and_binding_window_counts():
    cands = [cand(1, usage(50, 10)), cand(2, usage(20, 90)), cand(3, usage(30, 30))]
    # 1: використано 50, 2: 90 (тижневе вікно), 3: 30
    assert [c.slot for c in sel.rank(cands, now=NOW)] == [3, 1, 2]


def test_exhausted_blocked_expired_and_bad_status_are_skipped():
    cands = [
        cand(1, usage(100, 0)),
        cand(2, usage(10, 0), blocked_until=NOW + timedelta(minutes=5)),
        cand(3, usage(10, 0), usable=False),
        cand(4, usage(10, 0), status="UNAUTHENTICATED"),
        cand(5, usage(60, 0)),
    ]
    assert [c.slot for c in sel.rank(cands, now=NOW)] == [5]
    assert sel.pick([cands[0]], now=NOW) is None


def test_expired_cooldown_and_reset_window_make_account_eligible_again():
    stale = UsageSnapshot(five_hour=UsageWindow(percent=100, reset_at=iso(-1)))  # скинулося годину тому
    cands = [cand(1, stale), cand(2, usage(10), blocked_until=NOW - timedelta(seconds=1))]
    assert {c.slot for c in sel.rank(cands, now=NOW)} == {1, 2}
    assert sel.remaining_percent(stale, now=NOW) == 100


def test_sticky_prefers_current_until_it_is_ineligible():
    cands = [cand(1, usage(70)), cand(2, usage(5))]
    assert sel.pick(cands, now=NOW, prefer=1).slot == 1
    cands = [cand(1, usage(100)), cand(2, usage(5))]
    assert sel.pick(cands, now=NOW, prefer=1).slot == 2


def test_ties_prefer_least_recently_used_then_lowest_slot():
    old, new = NOW - timedelta(hours=2), NOW - timedelta(minutes=1)
    cands = [cand(1, usage(10), last_used_at=new), cand(2, usage(10), last_used_at=old), cand(3, usage(10), last_used_at=old)]
    assert [c.slot for c in sel.rank(cands, now=NOW)] == [2, 3, 1]


def test_unknown_usage_ranks_below_healthy_but_is_still_usable():
    cands = [cand(1, None), cand(2, usage(20)), cand(3, usage(80))]
    assert [c.slot for c in sel.rank(cands, now=NOW)] == [2, 1, 3]


def test_model_family_quota_is_respected():
    pro_full = [{"label": "Gemini Pro [None-4]", "usage_percentage": 100, "reset_at": iso(3)},
                {"label": "Gemini Flash [None-11]", "usage_percentage": 5, "reset_at": iso(3)}]
    a = cand(1, usage(10, 0, pro_full))
    b = cand(2, usage(50, 0))
    assert sel.pick([a, b], family="pro", now=NOW).slot == 2  # у 1-го вичерпано Pro
    assert sel.pick([a, b], family="flash", now=NOW).slot == 1  # а Flash майже порожній


def test_recovery_and_earliest_available():
    both = UsageSnapshot(five_hour=UsageWindow(percent=100, reset_at=iso(2)), weekly=UsageWindow(percent=100, reset_at=iso(48)))
    assert sel.recovery_time(both, now=NOW) == NOW + timedelta(hours=48)  # потрібні обидва вікна
    five_only = usage(100, 10)
    assert sel.recovery_time(five_only, now=NOW) == NOW + timedelta(hours=2)
    cands = [cand(1, both), cand(2, five_only), cand(3, usage(10), usable=False)]
    assert sel.earliest_available(cands, now=NOW) == NOW + timedelta(hours=2)
    assert sel.earliest_available([cand(1, usage(10))], now=NOW) is None

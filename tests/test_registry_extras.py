"""Нове в ядрі: replace-логін, вибір методу входу, вихід на стороні Google, контракт gemini-webapi,
синхронізація cookie, SessionKeeper."""

from __future__ import annotations

import asyncio
import time

import pytest

import gemini_vault.registry as registry_mod
from conftest import KEY
from gemini_vault import AccountRegistry, LoginError, SlotOccupiedError
from gemini_vault.login import LoginResult
from gemini_vault.sessions import usage_refresh_contract
from gemini_web2api_manager.keeper import SessionKeeper, build_cookie_header
from gemini_web2api_manager.pool import AccountPool


def run(coro):
    return asyncio.run(coro)


def test_login_replace_keeps_proxy_and_active_flag(manager, login):
    run(login(1, "a@x.com"))
    run(login(2, "b@x.com"))
    manager.set_proxy(2, "http://p:1")
    manager.set_active(2)
    with pytest.raises(SlotOccupiedError):
        run(login(2, "b2@x.com"))

    async def relogin():
        return await manager.login(2, replace=True)

    account = run(relogin())  # фейковий браузерний вхід лишився від останнього login(): це b2@x.com
    assert account.slot == 2 and account.is_active and account.email == "b2@x.com"
    assert manager.store.load().accounts[2].proxy == "http://p:1"  # публічний Account URL проксі не показує


def test_console_method_is_routed_with_io_email_and_debug_dir(manager, monkeypatch):
    seen = {}

    async def fake_console(io, **kwargs):
        seen.update(kwargs, io=io)
        return LoginResult("c@x.com", "C", {"__Secure-1PSID": "p", "__Secure-1PSIDTS": "t"})

    monkeypatch.setattr(registry_mod, "console_login", fake_console)
    marker = object()
    account = run(manager.login(1, method="console", io=marker, email="c@x.com", debug_dir="dbg", executable_path="/bin/chrome"))
    assert account.email == "c@x.com"
    assert seen["io"] is marker and seen["email"] == "c@x.com" and seen["debug_dir"] == "dbg"
    assert seen["headless"] is True and seen["executable_path"] == "/bin/chrome"
    with pytest.raises(LoginError, match="Unknown login method"):
        run(manager.login(2, method="telepathy"))


def _prime(monkeypatch, *, requested=True, valid=False, boom=False):
    async def revoke(cookies, proxy=None, **kw):
        if boom:
            raise OSError("offline")
        return requested

    async def is_valid(cookies, proxy=None):
        return valid

    monkeypatch.setattr(registry_mod, "revoke_session", revoke)
    monkeypatch.setattr(registry_mod, "session_is_valid", is_valid)


def test_sign_out_confirmed(manager, login, monkeypatch):
    run(login(1, "a@x.com"))
    _prime(monkeypatch, valid=False)
    result = run(manager.sign_out(1))
    assert (result.revoke_requested, result.revoke_confirmed) == (True, True)
    assert manager.accounts() == []


def test_sign_out_not_confirmed_and_unreachable_still_remove_the_slot(manager, login, monkeypatch):
    run(login(1, "a@x.com"))
    _prime(monkeypatch, valid=True)
    result = run(manager.sign_out(1))
    assert (result.revoke_requested, result.revoke_confirmed) == (True, False)
    run(login(1, "a@x.com"))
    _prime(monkeypatch, boom=True)
    result = run(manager.sign_out(1))
    assert (result.revoke_requested, result.revoke_confirmed) == (False, None)
    assert manager.accounts() == []


def test_gemini_webapi_private_usage_api_contract():
    """Падає, якщо нова версія gemini-webapi прибрала приватні методи: тоді переглянути залежність."""
    ok, missing = usage_refresh_contract()
    assert ok, f"gemini-webapi no longer has {missing}; the lightweight usage refresh silently degrades"


def test_sync_cookies_and_limit_marker_reach_the_vault(manager, login, fake_gemini):
    run(login(1, "a@x.com"))

    async def go():
        async with manager.client(1) as client:
            client.raw._cookies["__Secure-1PSIDTS"] = "rotated-by-test"
            client.sync_cookies()
            client.mark_limit_hit()

    run(go())
    stored = manager.store.load().accounts[1]
    assert stored.cookies["__Secure-1PSIDTS"] == "rotated-by-test"
    assert stored.last_limit_hit_at
    manager.mark_limit_hit(1)


def test_cookie_header_puts_main_cookies_first():
    header = build_cookie_header({"NID": "n", "SID": "s", "__Secure-1PSID": "p", "A": "a", "EMPTY": ""})
    assert header == "SID=s; __Secure-1PSID=p; A=a; NID=n"


def test_keeper_publishes_credentials_follows_vault_changes_and_marks_expired(manager, login, fake_gemini):
    run(login(1, "a@x.com"))
    run(login(2, "b@x.com"))
    pool = AccountPool()
    keeper = SessionKeeper(AccountRegistry(home=manager.settings.home, key=KEY), pool, refresh_interval=0.5)
    keeper.start(wait=30)
    try:
        status = {a["slot"]: a for a in pool.status()["accounts"]}
        assert set(status) == {1, 2} and all(a["usable"] for a in status.values())
        lease = pool.acquire()
        assert lease.credentials.xsrf_token.startswith("at-psid-") and lease.credentials.build_label == "boq_test"
        assert "__Secure-1PSID=psid-" in lease.credentials.cookie_header
        before = pool.status()["accounts"][0]["refreshed_at"]
        time.sleep(2.2)  # фоновий цикл оновлює знімок
        assert pool.status()["accounts"][0]["refreshed_at"] != before
        manager.remove(2)
        deadline = time.time() + 15
        while time.time() < deadline and len(pool.status()["accounts"]) != 1:
            time.sleep(0.3)
        assert [a["slot"] for a in pool.status()["accounts"]] == [1]
    finally:
        keeper.stop()


def _wait(predicate, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


def test_keeper_reopens_slot_when_account_is_replaced(manager, login, fake_gemini, monkeypatch):
    """Повторний вхід у той самий слот: шлюз має працювати з cookie нового акаунта, а не старого."""
    run(login(1, "a@x.com"))
    pool = AccountPool()
    keeper = SessionKeeper(AccountRegistry(home=manager.settings.home, key=KEY), pool, refresh_interval=300)
    keeper.start(wait=30)
    try:
        assert "psid-a@x.com" in pool.acquire().credentials.cookie_header
        pool.report_success(pool.acquire())  # лічильник запитів належить старому акаунту

        async def fake_login(**kwargs):
            return LoginResult("b@x.com", "b", {"__Secure-1PSID": "psid-b@x.com", "__Secure-1PSIDTS": "ts-1"})

        monkeypatch.setattr(registry_mod, "browser_login", fake_login)
        run(manager.login(1, replace=True))
        assert _wait(lambda: pool.status()["accounts"] and pool.status()["accounts"][0]["email"] == "b@x.com"
                     and pool.has_usable())
        lease = pool.acquire()
        assert "psid-b@x.com" in lease.credentials.cookie_header
        assert "psid-a@x.com" not in lease.credentials.cookie_header
        assert lease.credentials.xsrf_token == "at-psid-b@x.com"
        assert pool.status()["accounts"][0]["requests"] == 0  # статистика старого акаунта не переходить на новий
    finally:
        keeper.stop()


def test_keeper_reopens_slot_when_proxy_changes(manager, login, fake_gemini):
    run(login(1, "a@x.com"))
    pool = AccountPool()
    keeper = SessionKeeper(AccountRegistry(home=manager.settings.home, key=KEY), pool, refresh_interval=300)
    keeper.start(wait=30)
    try:
        assert pool.acquire().credentials.proxy is None
        manager.set_proxy(1, "http://p:1")
        assert _wait(lambda: pool.acquire().credentials.proxy == "http://p:1")
        assert fake_gemini.instances[-1].proxy == "http://p:1"  # внутрішній клієнт теж відкрито з новим проксі
    finally:
        keeper.stop()

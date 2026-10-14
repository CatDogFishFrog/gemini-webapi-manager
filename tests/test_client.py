import asyncio
import os

import pytest
from gemini_webapi import UsageLimitExceededError

from gemini_webapi_manager import ClientNotStartedError, SessionExpiredError


def _stored(manager, slot):
    return manager._store.load().accounts[slot]


def test_cookie_cache_is_private_and_rotation_synced_back(manager, login, fake_gemini):
    asyncio.run(login(1, "a@x.com"))

    async def go():
        async with manager.client() as client:
            assert os.environ.get("GEMINI_COOKIE_PATH")
            out = await client.generate_content("hi")
            assert out.text == "psid-a@x.com:hi"
            return client.raw._cookies["__Secure-1PSIDTS"]

    rotated = asyncio.run(go())
    assert "GEMINI_COOKIE_PATH" not in os.environ
    assert _stored(manager, 1).cookies["__Secure-1PSIDTS"] == rotated
    assert manager.get(1).last_used_at is not None


def test_user_supplied_cookie_path_is_respected(manager, login, monkeypatch, tmp_path):
    asyncio.run(login(1, "a@x.com"))
    monkeypatch.setenv("GEMINI_COOKIE_PATH", str(tmp_path / "mine"))

    async def go():
        async with manager.client():
            assert os.environ["GEMINI_COOKIE_PATH"] == str(tmp_path / "mine")

    asyncio.run(go())
    assert os.environ["GEMINI_COOKIE_PATH"] == str(tmp_path / "mine")


def test_limit_error_is_recorded_and_client_recovers(manager, login, fake_gemini):
    asyncio.run(login(1, "a@x.com"))

    async def go():
        async with manager.client() as client:
            client.raw.limit_next = True
            with pytest.raises(UsageLimitExceededError):
                await client.generate_content("one")
            out = await client.generate_content("two")  # transparently re-initialised
            return out.text, client.raw.init_calls

    text, init_calls = asyncio.run(go())
    assert text.endswith(":two") and init_calls == 2
    assert manager.get(1).last_limit_hit_at is not None


def test_active_client_follows_set_active(manager, login, fake_gemini):
    asyncio.run(login(1, "a@x.com"))
    asyncio.run(login(2, "b@x.com"))

    async def go():
        async with manager:
            first = await manager.generate_content("q")
            manager.set_active(2)
            second = await manager.generate_content("q")
            return first.text, second.text

    first, second = asyncio.run(go())
    assert first == "psid-a@x.com:q" and second == "psid-b@x.com:q"
    assert all(inst.closed for inst in fake_gemini.instances[:-1])


def test_refresh_usage_updates_snapshot(manager, login, fake_gemini):
    asyncio.run(login(1, "a@x.com"))
    original = fake_gemini.instances[-1]
    original.usage_info["current_5h"]["usage_percentage"] = 40

    async def go():
        # new FakeGemini instances are created per client; patch class default via instances hook
        fake_gemini.instances.clear()
        account = await manager.refresh_usage(1)
        return account

    account = asyncio.run(go())
    assert account.usage_percent == 40


def test_use_before_start_raises(manager, login):
    asyncio.run(login(1, "a@x.com"))
    client = manager.client()
    with pytest.raises(ClientNotStartedError):
        asyncio.run(client.generate_content("x"))


def test_removed_slot_is_not_resurrected_by_running_client(manager, login, fake_gemini):
    asyncio.run(login(1, "a@x.com"))

    async def go():
        async with manager.client() as client:
            manager.remove(1)
            await client.generate_content("x")

    asyncio.run(go())
    assert manager.accounts() == []

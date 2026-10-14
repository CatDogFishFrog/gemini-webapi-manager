import asyncio
import json
import shutil

import pytest

from gemini_webapi_manager import (
    AccountManager,
    DuplicateAccountError,
    InvalidKeyError,
    MissingKeyError,
    NoActiveAccountError,
    SlotEmptyError,
    SlotError,
    SlotOccupiedError,
    WeakKeyError,
)
from gemini_webapi_manager.config import Settings

from conftest import KEY


def test_first_login_becomes_active_and_reports_usage(manager, login):
    account = asyncio.run(login(1, "a@x.com"))
    assert account.slot == 1 and account.is_active and account.email == "a@x.com"
    assert account.usage_percent == 40
    assert account.status == "AVAILABLE"
    assert [a.slot for a in manager.accounts()] == [1]
    assert manager.free_slots()[0] == 2


def test_occupied_slot_refuses_login(manager, login):
    asyncio.run(login(1, "a@x.com"))
    with pytest.raises(SlotOccupiedError):
        asyncio.run(login(1, "b@x.com"))


def test_same_email_in_two_slots_rejected(manager, login):
    asyncio.run(login(1, "a@x.com"))
    with pytest.raises(DuplicateAccountError):
        asyncio.run(login(2, "A@x.com"))
    assert [a.slot for a in manager.accounts()] == [1]


def test_set_active_and_remove(manager, login):
    asyncio.run(login(1, "a@x.com"))
    asyncio.run(login(2, "b@x.com"))
    assert manager.active().slot == 1
    manager.set_active(2)
    assert manager.active().email == "b@x.com"
    manager.remove(2)
    assert manager.active() is None
    assert [a.slot for a in manager.accounts()] == [1]
    with pytest.raises(SlotEmptyError):
        manager.set_active(2)
    with pytest.raises(NoActiveAccountError):
        asyncio.run(manager.generate_content("x"))


def test_slot_validation(manager):
    for bad in (0, 11, -1, "1", True):
        with pytest.raises(SlotError):
            manager.get(bad)
    with pytest.raises(SlotEmptyError):
        manager.get(3)


def test_account_json_has_no_secrets(manager, login):
    account = asyncio.run(login(1, "a@x.com"))
    raw = account.to_json()
    data = json.loads(raw)
    assert data["slot"] == 1 and data["is_active"] is True and data["email"] == "a@x.com"
    assert data["usage_percent"] == 40
    assert "psid" not in raw and "cookies" not in data and "proxy" not in data


def test_vault_is_encrypted_at_rest(manager, login):
    asyncio.run(login(1, "a@x.com"))
    blob = manager.settings.vault_path.read_bytes()
    assert b"psid-" not in blob and b"a@x.com" not in blob


def test_wrong_key_and_transfer_between_homes(manager, login, tmp_path):
    asyncio.run(login(1, "a@x.com"))
    with pytest.raises(InvalidKeyError):
        AccountManager(home=manager.settings.home, key="a-totally-different-key-123").accounts()

    other_home = tmp_path / "other-device"
    other_home.mkdir()
    shutil.copy(manager.settings.vault_path, other_home / "vault.bin")
    moved = AccountManager(home=other_home, key=KEY)
    assert moved.accounts()[0].email == "a@x.com"


def test_key_validation(tmp_path):
    with pytest.raises(MissingKeyError):
        Settings.from_env(home=tmp_path)
    with pytest.raises(WeakKeyError):
        Settings.from_env(home=tmp_path, key="short")


def test_key_and_home_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_MANAGER_KEY", KEY)
    monkeypatch.setenv("GEMINI_MANAGER_HOME", str(tmp_path / "h"))
    monkeypatch.setenv("GEMINI_MANAGER_MAX_SLOTS", "3")
    settings = Settings.from_env()
    assert settings.max_slots == 3 and settings.home == tmp_path / "h"

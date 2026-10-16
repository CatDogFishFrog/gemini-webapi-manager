"""Перешифрування vault.bin іншим ключем і ключ із файла (GEMINI_MANAGER_KEY_FILE)."""

from __future__ import annotations

import os

import pytest

from conftest import KEY
from gemini_vault import (
    AccountRegistry, ConfigError, InvalidKeyError, MissingKeyError, Settings, WeakKeyError,
)
from gemini_vault.cli import main
from gemini_vault.models import StoredAccount

NEW = "another-key-0123456789-abcdef"


def _filled(tmp_path, key=KEY) -> AccountRegistry:
    reg = AccountRegistry(home=tmp_path / "home", key=key)
    with reg.store.transaction() as vault:
        for slot, mail in ((1, "a@x.com"), (3, "c@x.com")):
            vault.accounts[slot] = StoredAccount(
                slot=slot, cookies={"__Secure-1PSID": f"p{slot}", "__Secure-1PSIDTS": "t"},
                created_at="2030-01-01T00:00:00+00:00", email=mail, proxy="http://p:1" if slot == 3 else None,
                usage={"tier": "PRO"},
            )
        vault.active_slot = 3
    return reg


def test_rekey_keeps_everything_and_switches_the_key(tmp_path):
    reg = _filled(tmp_path)
    before = reg.store.load().to_dict()
    old_blob = reg.settings.vault_path.read_bytes()
    backup = reg.rekey(NEW)
    assert reg.settings.key == NEW and reg.store.load().to_dict() == before
    new_blob = reg.settings.vault_path.read_bytes()
    assert new_blob != old_blob and new_blob[5:21] != old_blob[5:21]  # інша сіль
    with pytest.raises(InvalidKeyError):
        AccountRegistry(home=reg.settings.home, key=KEY).accounts()
    assert AccountRegistry(home=reg.settings.home, key=NEW).store.load().to_dict() == before
    assert backup is not None and backup.read_bytes() == old_blob  # копія відкривається старим ключем
    restored = tmp_path / "restored"
    restored.mkdir()
    (restored / "vault.bin").write_bytes(backup.read_bytes())
    assert AccountRegistry(home=restored, key=KEY).store.load().to_dict() == before
    if os.name != "nt":
        assert oct(backup.stat().st_mode & 0o777) == "0o600"
        assert oct(reg.settings.vault_path.stat().st_mode & 0o777) == "0o600"
    reg.set_active(1)  # реєстр далі працює з новим ключем
    assert AccountRegistry(home=reg.settings.home, key=NEW).active().slot == 1


def test_rekey_without_backup(tmp_path):
    reg = _filled(tmp_path)
    assert reg.rekey(NEW, backup=False) is None
    assert not list(reg.settings.home.glob("vault.bin.bak.*"))


def test_failed_rekey_leaves_the_old_vault_intact(tmp_path, monkeypatch):
    reg = _filled(tmp_path)
    before = reg.store.load().to_dict()
    blob = reg.settings.vault_path.read_bytes()

    def boom(src, dst):
        raise OSError("disk is full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        reg.rekey(NEW)
    monkeypatch.undo()
    assert reg.settings.vault_path.read_bytes() == blob
    assert reg.settings.key == KEY and reg.store.load().to_dict() == before
    assert not list(reg.settings.home.glob(".vault-*.tmp"))


@pytest.mark.parametrize("bad, error", [("short", WeakKeyError), (KEY, ConfigError), ("", MissingKeyError)])
def test_rekey_rejects_bad_new_keys(tmp_path, bad, error):
    reg = _filled(tmp_path)
    blob = reg.settings.vault_path.read_bytes()
    with pytest.raises(error):
        reg.rekey(bad)
    assert reg.settings.vault_path.read_bytes() == blob


def test_rekey_needs_a_vault_and_the_right_old_key(tmp_path):
    with pytest.raises(ConfigError, match="no vault"):
        AccountRegistry(home=tmp_path / "empty", key=KEY).rekey(NEW)
    reg = _filled(tmp_path)
    with pytest.raises(InvalidKeyError):
        AccountRegistry(home=reg.settings.home, key="wrong-key-0123456789-abcdef").rekey(NEW)


def test_cli_rekey_from_env_never_prints_the_key(tmp_path, monkeypatch, capsys):
    reg = _filled(tmp_path)
    monkeypatch.setenv("GEMINI_MANAGER_HOME", str(reg.settings.home))
    monkeypatch.setenv("GEMINI_MANAGER_KEY", KEY)
    monkeypatch.setenv("MY_NEW_KEY", NEW)
    assert main(["rekey", "--new-key-env", "MY_NEW_KEY"]) == 0
    out = capsys.readouterr()
    assert NEW not in out.out + out.err and KEY not in out.out + out.err
    assert out.out == "" and "re-encrypted" in out.err
    assert AccountRegistry(home=reg.settings.home, key=NEW).accounts()


def test_cli_rekey_generate_prints_only_the_key_to_stdout(tmp_path, monkeypatch, capsys):
    reg = _filled(tmp_path)
    monkeypatch.setenv("GEMINI_MANAGER_HOME", str(reg.settings.home))
    monkeypatch.setenv("GEMINI_MANAGER_KEY", KEY)
    assert main(["rekey", "--generate", "--no-backup"]) == 0
    out = capsys.readouterr()
    new_key = out.out.strip()
    assert len(new_key) >= 16 and "\n" not in new_key and new_key not in out.err
    assert AccountRegistry(home=reg.settings.home, key=new_key).accounts()


def test_cli_rekey_from_file_and_without_terminal(tmp_path, monkeypatch, capsys):
    reg = _filled(tmp_path)
    monkeypatch.setenv("GEMINI_MANAGER_HOME", str(reg.settings.home))
    monkeypatch.setenv("GEMINI_MANAGER_KEY", KEY)
    assert main(["rekey"]) == 1  # у тесті немає терміналу, а джерело ключа не вказано
    assert "no terminal" in capsys.readouterr().err
    key_file = tmp_path / "new.key"
    key_file.write_text(NEW + "\n", encoding="utf-8")
    assert main(["rekey", "--new-key-file", str(key_file)]) == 0
    assert AccountRegistry(home=reg.settings.home, key=NEW).accounts()


def test_key_file_is_read_with_the_documented_precedence(tmp_path, monkeypatch):
    key_file = tmp_path / "secret"
    key_file.write_text(KEY + "\r\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_MANAGER_KEY_FILE", str(key_file))
    assert Settings.from_env(home=tmp_path).key == KEY  # кінцевий перевід рядка відкинуто
    monkeypatch.setenv("GEMINI_MANAGER_KEY", NEW)
    assert Settings.from_env(home=tmp_path).key == NEW  # змінна ключа сильніша за файл
    assert Settings.from_env(home=tmp_path, key="explicit-key-0123456789ab").key == "explicit-key-0123456789ab"


def test_key_file_errors_do_not_leak_content(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_MANAGER_KEY_FILE", str(tmp_path / "missing"))
    with pytest.raises(ConfigError, match="Cannot read"):
        Settings.from_env(home=tmp_path)
    empty = tmp_path / "empty"
    empty.write_text("\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_MANAGER_KEY_FILE", str(empty))
    with pytest.raises(MissingKeyError):
        Settings.from_env(home=tmp_path)
    short = tmp_path / "short"
    short.write_text("short-secret\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_MANAGER_KEY_FILE", str(short))
    with pytest.raises(WeakKeyError) as info:
        Settings.from_env(home=tmp_path)
    assert "short-secret" not in str(info.value)

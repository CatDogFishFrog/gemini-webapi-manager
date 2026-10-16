"""Консольний логін: чиста класифікація сторінок + повні сценарії в справжньому Chromium
проти макета Google (mock_google.py). Браузерні тести потребують GEMINI_VAULT_BROWSER_PATH."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from gemini_vault import AccountRegistry
from gemini_vault.errors import LoginAbortedError, LoginBlockedError, LoginChallengeError
from gemini_vault.login import LoginTarget, ScriptedIO, Step, classify, console_login
from gemini_vault.login.console import Observation, mask_text, sanitise_observation
from mock_google import EMAIL, PASSWORD, SMS, TOTP, MockGoogle, Scenario

BROWSER = os.environ.get("GEMINI_VAULT_BROWSER_PATH")
needs_browser = pytest.mark.skipif(
    not (BROWSER and Path(BROWSER).exists()), reason="set GEMINI_VAULT_BROWSER_PATH to a Chromium binary"
)

G = LoginTarget()


def obs(url="https://accounts.google.com/v3/signin/identifier", **kw) -> Observation:
    return Observation(url=url, text=kw.pop("text", "Sign in to continue to something"), **kw)


# ----------------------------------------------------------------------------- класифікація
def test_classify_identifier_and_password():
    assert classify(obs(inputs=[{"name": "identifier", "type": "email"}]), G) is Step.IDENTIFIER
    assert classify(obs(url="https://accounts.google.com/v3/signin/challenge/pwd",
                        inputs=[{"name": "Passwd", "type": "password"}]), G) is Step.PASSWORD


def test_classify_code_inputs_and_options():
    assert classify(obs(url="https://accounts.google.com/signin/challenge/totp",
                        inputs=[{"name": "totpPin", "type": "tel"}]), G) is Step.INPUT
    options = [{"text": "A", "type": "9"}, {"text": "B", "type": "6"}]
    assert classify(obs(url="https://accounts.google.com/signin/challenge/selection", options=options), G) is Step.OPTIONS


def test_classify_device_action_rejected_app_unknown():
    assert classify(obs(url="https://accounts.google.com/signin/challenge/dp", text="Check your phone, tap Yes " * 3), G) is Step.WAIT_DEVICE
    assert classify(obs(text="Do you want this? " * 3, buttons=[{"text": "Not now"}]), G) is Step.ACTION
    assert classify(obs(url="https://accounts.google.com/signin/rejected"), G) is Step.REJECTED
    assert classify(obs(text="Couldn't sign you in. This browser or app may not be secure."), G) is Step.REJECTED
    assert classify(obs(url="https://gemini.google.com/app"), G) is Step.APP
    assert classify(obs(text="Some unexpected but long enough decorative text here"), G) is Step.UNKNOWN
    assert classify(obs(text=""), G) is Step.LOADING


def test_footer_noise_is_not_an_action():
    page = obs(text="Some unexpected but long enough decorative text here",
               buttons=[{"text": "Help"}, {"text": "Privacy"}, {"text": "English (United States)"}])
    assert classify(page, G) is Step.UNKNOWN


def test_dump_is_sanitised():
    page = obs(url="https://accounts.google.com/x?token=SECRET&e=a@b.com", text="Hi john.doe@gmail.com, confirm",
               inputs=[{"id": "i", "name": "n", "type": "text", "label": "mail john.doe@gmail.com", "maxlength": 0, "autocomplete": ""}],
               buttons=[{"text": "Use john.doe@gmail.com", "tag": "button", "id": "", "role": "", "href": ""}])
    dump = json.dumps(sanitise_observation(page))
    assert "john.doe" not in dump and "SECRET" not in dump and "a@b.com" not in dump
    assert mask_text("x me@host.io y") == "x <email> y"


# ----------------------------------------------------------------------------- браузер
def _run(io, mock, **kw):
    return asyncio.run(console_login(io, target=mock.target, executable_path=BROWSER, timeout=60,
                                     device_wait=20, **kw))


def _assert_no_secrets(io: ScriptedIO):
    shown = "\n".join(text for _, text in io.transcript)
    for secret in (PASSWORD, TOTP, SMS):
        assert secret not in shown


@needs_browser
def test_password_and_totp():
    with MockGoogle(Scenario("totp")) as mock:
        io = ScriptedIO([EMAIL, PASSWORD, TOTP])
        result = _run(io, mock)
    assert result.email == EMAIL
    assert result.cookies["__Secure-1PSID"] == "psid-value"
    assert [k for k, _ in io.transcript if k in ("ask", "secret")] == ["ask", "secret", "secret"]
    _assert_no_secrets(io)


@needs_browser
def test_email_given_up_front_and_wrong_password_retry():
    with MockGoogle(Scenario(None)) as mock:
        io = ScriptedIO(["wrong-password", PASSWORD])
        result = _run(io, mock, email=EMAIL)
        attempts = mock.scenario.password_attempts
    assert result.email == EMAIL and attempts == 2
    assert any(k == "warn" and "Wrong password" in t for k, t in io.transcript)


@needs_browser
def test_unknown_email_is_asked_again():
    with MockGoogle(Scenario(None)) as mock:
        io = ScriptedIO(["nobody@example.com", EMAIL, PASSWORD])
        assert _run(io, mock).email == EMAIL
    assert any("find your Google Account" in t for k, t in io.transcript if k == "warn")


@needs_browser
def test_method_selection_then_sms_with_wrong_code_first():
    with MockGoogle(Scenario("selection")) as mock:
        io = ScriptedIO([EMAIL, PASSWORD, "text message", "000000", SMS])
        assert _run(io, mock).cookies["SID"] == "sid-value"
    menu = next(t for k, t in io.transcript if k == "choose")
    assert "Authenticator" in menu and "text message" in menu and "Tap Yes" in menu
    assert any("Wrong code" in t for k, t in io.transcript if k == "warn")
    _assert_no_secrets(io)


@needs_browser
def test_try_another_way_from_code_page():
    with MockGoogle(Scenario("totp")) as mock:
        io = ScriptedIO([EMAIL, PASSWORD, "!", "text message", SMS])
        assert _run(io, mock).email == EMAIL


@needs_browser
def test_device_prompt_waits_and_shows_number_then_consent_screen():
    with MockGoogle(Scenario("device", dp_delay=2.0)) as mock:
        io = ScriptedIO([EMAIL, PASSWORD, "Not now"])
        assert _run(io, mock).email == EMAIL
    assert any("42" in t for k, t in io.transcript if k == "info")


@needs_browser
def test_rejected_raises_blocked_and_dumps_debug(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with MockGoogle(Scenario("rejected")) as mock:
        with pytest.raises(LoginBlockedError) as exc:
            _run(ScriptedIO([EMAIL, PASSWORD]), mock)
    assert "vault.bin" in str(exc.value)
    assert list((tmp_path / "gemini-login-debug").glob("*-rejected.png"))


@needs_browser
def test_unknown_page_dumps_sanitised_debug_and_aborts(tmp_path):
    with MockGoogle(Scenario("unknown")) as mock:
        with pytest.raises(LoginChallengeError) as exc:
            _run(ScriptedIO([EMAIL, PASSWORD, "Abort"]), mock, debug_dir=tmp_path / "dbg")
    files = sorted(p.name for p in (tmp_path / "dbg").iterdir())
    assert any(f.endswith("unknown.png") for f in files) and any(f.endswith("unknown.json") for f in files)
    assert "transcript.txt" in files
    blob = "".join(p.read_text() for p in (tmp_path / "dbg").glob("*.json")) + (tmp_path / "dbg/transcript.txt").read_text()
    assert PASSWORD not in blob and EMAIL not in blob
    assert exc.value.debug_dir


@needs_browser
def test_too_many_wrong_passwords_stops_before_lockout():
    with MockGoogle(Scenario(None)) as mock:
        from gemini_vault.errors import LoginError
        with pytest.raises(LoginError, match="Too many"):
            _run(ScriptedIO(["a", "b", "c", "d"]), mock, email=EMAIL)
        assert mock.scenario.password_attempts == 3


@needs_browser
def test_user_can_abort_from_the_menu():
    with MockGoogle(Scenario("selection")) as mock:
        def abort(kind, prompt, options):
            raise LoginAbortedError("Login cancelled.")
        with pytest.raises(LoginAbortedError):
            _run(ScriptedIO([EMAIL, PASSWORD, abort]), mock)


@needs_browser
def test_registry_console_login_stores_account(tmp_path):
    registry = AccountRegistry(home=tmp_path / "h", key="unit-test-key-0123456789abcdef")
    with MockGoogle(Scenario("totp")) as mock:
        account = asyncio.run(registry.login(
            1, method="console", io=ScriptedIO([EMAIL, PASSWORD, TOTP]), verify=False,
            target=mock.target, executable_path=BROWSER, timeout=60))
    assert account.email == EMAIL and account.is_active
    stored = registry.store.load().accounts[1]
    assert stored.cookies["__Secure-1PSID"] == "psid-value"


# ----------------------------------------------------------------------------- термінал
def test_terminal_io_choose_secret_and_cancel(monkeypatch):
    import io as _io
    from gemini_vault.login import TerminalIO

    out = _io.StringIO()
    terminal = TerminalIO(out)
    answers = iter(["abc", "9", "2"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    assert terminal.choose("Pick", ["one", "two"]) == 1  # нечисло й число поза межами відхиляються
    assert "Enter one of the numbers" in out.getvalue()
    monkeypatch.setattr("getpass.getpass", lambda prompt="", stream=None: "s3cret\n")
    assert terminal.ask_secret("pw: ") == "s3cret"
    monkeypatch.setattr("builtins.input", lambda *a: "q")
    with pytest.raises(LoginAbortedError):
        terminal.choose("Pick", ["one"])
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(EOFError))
    with pytest.raises(LoginAbortedError):
        terminal.ask("x: ")

from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest
from gemini_webapi import UsageLimitExceededError

import gemini_webapi_manager.client as client_mod
import gemini_webapi_manager.manager as manager_mod
from gemini_webapi_manager import AccountManager
from gemini_webapi_manager.login import LoginResult

KEY = "unit-test-key-0123456789abcdef"
_rotation = itertools.count(1)


class _Jar:
    def __init__(self, data: dict[str, str]) -> None:
        self.jar = [
            SimpleNamespace(name=k, value=v, domain=".google.com") for k, v in data.items() if v
        ]


class FakeGemini:
    """Offline stand-in for gemini_webapi.GeminiClient."""

    instances: list["FakeGemini"] = []

    def __init__(self, secure_1psid=None, secure_1psidts=None, proxy=None, **kwargs):
        self._cookies = {"__Secure-1PSID": secure_1psid, "__Secure-1PSIDTS": secure_1psidts}
        self.proxy = proxy
        self.account_status = SimpleNamespace(name="AVAILABLE")
        self.usage_info = {
            "tier": {"id": 2, "label": "PRO"},
            "current_5h": {"usage_percentage": 40, "reset_at": "2030-01-01T00:00:00+00:00",
                           "remaining_credits": 6},
            "weekly": {"usage_percentage": 10, "reset_at": "2030-01-05T00:00:00+00:00",
                       "remaining_credits": 90},
            "ai_credits_remaining": None,
        }
        self.quotas = {}
        self.init_calls = 0
        self.closed = False
        self.limit_next = False
        FakeGemini.instances.append(self)

    @property
    def cookies(self):
        return _Jar(self._cookies)

    @cookies.setter
    def cookies(self, value):
        self._cookies.update(value)

    async def init(self, **kwargs):
        self.init_calls += 1
        self.closed = False
        self._cookies["__Secure-1PSIDTS"] = f"rotated-{next(_rotation)}"

    async def close(self):
        self.closed = True

    async def generate_content(self, prompt, **kwargs):
        if self.limit_next:
            self.limit_next = False
            self.closed = True  # the real library closes itself before raising
            raise UsageLimitExceededError("limit")
        return SimpleNamespace(text=f"{self._cookies['__Secure-1PSID']}:{prompt}")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("GEMINI_COOKIE_PATH", "GEMINI_MANAGER_KEY", "GEMINI_MANAGER_HOME",
                 "GEMINI_MANAGER_MAX_SLOTS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def fake_gemini(monkeypatch):
    FakeGemini.instances.clear()
    monkeypatch.setattr(client_mod, "GeminiClient", FakeGemini)
    return FakeGemini


@pytest.fixture
def manager(tmp_path, fake_gemini):
    return AccountManager(home=tmp_path / "home", key=KEY)


@pytest.fixture
def login(monkeypatch, manager):
    """``await login(slot, email)`` runs a fake browser login against ``manager``."""

    async def do(slot: int, email: str):
        async def fake_browser_login(**kwargs):
            return LoginResult(
                email=email,
                display_name=email.split("@")[0],
                cookies={"__Secure-1PSID": f"psid-{email}", "__Secure-1PSIDTS": "ts-0", "NID": "n"},
            )

        monkeypatch.setattr(manager_mod, "browser_login", fake_browser_login)
        return await manager.login(slot)

    return do

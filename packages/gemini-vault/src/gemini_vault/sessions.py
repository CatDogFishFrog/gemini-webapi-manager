"""ManagedClient: a thin, transparent wrapper over ``gemini_webapi.GeminiClient``.

Everything not overridden here is forwarded to the underlying client, so new
gemini-webapi features work without changes. The wrapper adds:

* credentials loaded from the vault for one slot,
* rotated cookies written back (encrypted) so sessions stay alive,
* a fresh usage snapshot after init and on demand,
* bookkeeping (last use, usage-limit hits) and transparent recovery after a limit error.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from gemini_webapi import AuthError, GeminiClient, UsageLimitExceededError

from . import cache
from .errors import (
    ClientNotStartedError,
    GeminiManagerError,
    SessionExpiredError,
    SlotEmptyError,
)
from .models import StoredAccount
from .storage import VaultStore
from .usage import UsageSnapshot, snapshot_from_client, utcnow_iso

log = logging.getLogger(__name__)

PSID = "__Secure-1PSID"
PSIDTS = "__Secure-1PSIDTS"

_COOKIE_SYNC_INTERVAL = 300.0
_TOUCH_INTERVAL = 30.0


def cookies_to_dict(cookies: Any) -> dict[str, str]:
    """Flatten a curl_cffi ``Cookies`` jar to name->value for google.com cookies."""
    items = list(getattr(cookies, "jar", []))
    items.sort(key=lambda c: (c.domain or "").startswith("."))  # '.google.com' entries win
    chosen: dict[str, str] = {}
    for cookie in items:
        domain = (cookie.domain or "").lstrip(".")
        if cookie.value and domain.endswith("google.com"):
            chosen[cookie.name] = cookie.value
    return chosen


def status_name(client: Any) -> str | None:
    status = getattr(client, "account_status", None)
    if status is None:
        return None
    return getattr(status, "name", None) or str(status)


def build_gemini_client(cookies: Mapping[str, str], proxy: str | None = None) -> GeminiClient:
    """Create a GeminiClient from stored cookies.

    Refuses to proceed without ``__Secure-1PSID``: gemini-webapi would otherwise fall back
    to cookies from the local browser and silently use a different account.
    """
    psid = cookies.get(PSID)
    if not psid:
        raise GeminiManagerError("Stored session has no __Secure-1PSID cookie.")
    client = GeminiClient(psid, cookies.get(PSIDTS) or None, proxy=proxy)
    extra = {k: v for k, v in cookies.items() if k not in (PSID, PSIDTS) and v}
    if extra:
        client.cookies = extra
    return client


class ManagedClient:
    """Use as ``async with manager.client() as client`` or call ``start()`` / ``close()``."""

    def __init__(
        self,
        store: VaultStore,
        slot: int,
        init_options: Mapping[str, Any] | None = None,
    ) -> None:
        self._store = store
        self._slot = slot
        self._init_options = dict(init_options or {})
        self._client: GeminiClient | None = None
        self._cache_held = False
        self._needs_reinit = False
        self._account_created_at: str | None = None
        self._last_sync = 0.0
        self._last_touch = 0.0

    # ------------------------------------------------------------------ properties
    @property
    def slot(self) -> int:
        return self._slot

    @property
    def account_created_at(self) -> str | None:
        """Identity of the vault record this client was started from."""
        return self._account_created_at

    @property
    def is_running(self) -> bool:
        return self._client is not None and not self._needs_reinit

    @property
    def raw(self) -> GeminiClient:
        """The underlying ``GeminiClient`` (escape hatch)."""
        if self._client is None:
            raise ClientNotStartedError("Client not started: use `async with` or await start().")
        return self._client

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        client = self.__dict__.get("_client")
        if client is None:
            raise ClientNotStartedError(
                f"Cannot access {name!r}: client not started (use `async with` or await start())."
            )
        return getattr(client, name)

    # ------------------------------------------------------------------ lifecycle
    async def __aenter__(self) -> "ManagedClient":
        return await self.start()

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def start(self) -> "ManagedClient":
        if self._client is None or self._needs_reinit:
            await self._open()
        return self

    def _load_account(self) -> StoredAccount:
        account = self._store.load().accounts.get(self._slot)
        if account is None:
            raise SlotEmptyError(self._slot)
        return account

    async def _open(self) -> None:
        reuse = self._client is not None  # re-init the same object so chats stay valid
        account = self._load_account()
        self._account_created_at = account.created_at
        if not self._cache_held:
            cache.acquire()
            self._cache_held = True
        client = self._client if reuse else build_gemini_client(account.cookies, account.proxy)
        assert client is not None
        try:
            await client.init(**self._init_options)
        except AuthError as exc:
            self._persist(status="UNAUTHENTICATED")
            if not reuse:
                self._release_cache()
            raise SessionExpiredError(self._slot, account.email) from exc
        except BaseException:
            if not reuse:
                self._release_cache()
            raise
        self._client = client
        self._needs_reinit = False
        self._persist(
            cookies=True,
            snapshot=snapshot_from_client(client),
            touch=True,
            status=status_name(client),
        )
        self._last_sync = self._last_touch = time.monotonic()

    def _release_cache(self) -> None:
        if self._cache_held:
            self._cache_held = False
            cache.release()

    async def close(self) -> None:
        client = self._client
        if client is None:
            self._release_cache()
            return
        try:
            await client.close()
        finally:
            self._persist(cookies=True)
            self._client = None
            self._release_cache()

    # ------------------------------------------------------------------ persistence
    def _persist(
        self,
        *,
        cookies: bool = False,
        snapshot: UsageSnapshot | None = None,
        touch: bool = False,
        limit_hit: bool = False,
        status: str | None = None,
    ) -> None:
        fresh_cookies = (
            cookies_to_dict(self._client.cookies) if cookies and self._client is not None else None
        )
        try:
            with self._store.transaction() as vault:
                stored = vault.accounts.get(self._slot)
                if stored is None or stored.created_at != self._account_created_at:
                    return  # slot was removed or re-used meanwhile: do not resurrect it
                now = utcnow_iso()
                if fresh_cookies:
                    stored.cookies.update(fresh_cookies)
                    stored.cookies_synced_at = now
                if snapshot is not None:
                    stored.usage = snapshot.to_dict()
                if touch:
                    stored.last_used_at = now
                if limit_hit:
                    stored.last_limit_hit_at = now
                if status:
                    stored.status = status
        except (OSError, GeminiManagerError) as exc:
            log.warning("Could not update vault for slot %s: %s", self._slot, exc)

    def _after_request(self) -> None:
        now = time.monotonic()
        sync = now - self._last_sync >= _COOKIE_SYNC_INTERVAL
        touch = now - self._last_touch >= _TOUCH_INTERVAL
        if not (sync or touch):
            return
        self._persist(cookies=sync, touch=touch)
        if sync:
            self._last_sync = now
        if touch:
            self._last_touch = now

    # ------------------------------------------------------------------ guarded calls
    async def _ensure_ready(self) -> None:
        if self._client is None:
            raise ClientNotStartedError("Client not started: use `async with` or await start().")
        if self._needs_reinit:
            await self._open()

    async def _run(self, getter: Callable[[], Callable[..., Any]], *args: Any, **kwargs: Any) -> Any:
        await self._ensure_ready()
        try:
            result = await getter()(*args, **kwargs)
        except UsageLimitExceededError:
            self._on_limit()
            raise
        except AuthError as exc:
            self._persist(status="UNAUTHENTICATED")
            raise SessionExpiredError(self._slot) from exc
        self._after_request()
        return result

    async def _run_stream(
        self, getter: Callable[[], Callable[..., Any]], *args: Any, **kwargs: Any
    ) -> AsyncIterator[Any]:
        await self._ensure_ready()
        try:
            async for chunk in getter()(*args, **kwargs):
                yield chunk
        except UsageLimitExceededError:
            self._on_limit()
            raise
        except AuthError as exc:
            self._persist(status="UNAUTHENTICATED")
            raise SessionExpiredError(self._slot) from exc
        self._after_request()

    def _on_limit(self) -> None:
        # gemini-webapi closes itself when the limit error arrives; re-init lazily next call.
        self._needs_reinit = True
        self._persist(cookies=True, limit_hit=True)

    # ------------------------------------------------------------------ public API
    async def generate_content(self, *args: Any, **kwargs: Any) -> Any:
        return await self._run(lambda: self.raw.generate_content, *args, **kwargs)

    def generate_content_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        return self._run_stream(lambda: self.raw.generate_content_stream, *args, **kwargs)

    def start_chat(self, **kwargs: Any) -> "ManagedChat":
        return ManagedChat(self, self.raw.start_chat(**kwargs))

    async def refresh_usage(self) -> UsageSnapshot | None:
        """Fetch current limits from Gemini and store them. Returns the new snapshot."""
        await self._ensure_ready()
        raw = self.raw
        # Cheap path: re-run just the two usage RPCs. These are private in gemini-webapi,
        # so fall back to a full re-init (public API only) if they vanish or fail.
        fetch_usage = getattr(raw, "_fetch_usage_info", None)
        fetch_quota = getattr(raw, "_fetch_quota", None)
        refreshed = False
        if callable(fetch_usage) and callable(fetch_quota):
            try:
                await fetch_usage()
                await fetch_quota()
                refreshed = True
            except Exception as exc:  # noqa: BLE001 - library internals, fall back below
                log.debug("Lightweight usage refresh failed (%s); re-initialising.", exc)
        if not refreshed:
            await raw.close()
            await raw.init(**self._init_options)
        snapshot = snapshot_from_client(raw)
        self._persist(snapshot=snapshot, status=status_name(raw))
        return snapshot


class ManagedChat:
    """Wraps a gemini-webapi ``ChatSession`` so its messages get the same bookkeeping."""

    def __init__(self, owner: ManagedClient, chat: Any) -> None:
        self._owner = owner
        self._chat = chat

    @property
    def raw(self) -> Any:
        return self._chat

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.__dict__["_chat"], name)

    async def send_message(self, *args: Any, **kwargs: Any) -> Any:
        return await self._owner._run(lambda: self._chat.send_message, *args, **kwargs)

    def send_message_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        return self._owner._run_stream(lambda: self._chat.send_message_stream, *args, **kwargs)

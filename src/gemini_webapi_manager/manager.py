"""AccountManager: the public entry point."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Callable
from typing import Any

from gemini_webapi import AuthError

from . import cache
from .client import (
    ManagedClient,
    build_gemini_client,
    cookies_to_dict,
    status_name,
)
from .config import Settings
from .errors import (
    DuplicateAccountError,
    GeminiManagerError,
    LoginError,
    NoActiveAccountError,
    SlotEmptyError,
    SlotError,
    SlotOccupiedError,
)
from .login import LoginResult, browser_login
from .models import Account, StoredAccount, VaultData
from .storage import VaultStore
from .usage import snapshot_from_client, utcnow_iso

log = logging.getLogger(__name__)


class AccountManager:
    """Manage several Google accounts for gemini-webapi.

    Slots are numbered ``1..max_slots``. Inspection and slot management are synchronous
    (no network); login, usage refresh and Gemini calls are ``async``.

    >>> mgr = AccountManager()                    # key from GEMINI_MANAGER_KEY
    >>> await mgr.login(1)                        # opens a browser window
    >>> mgr.accounts()                            # -> list[Account]
    >>> mgr.set_active(1)
    >>> out = await mgr.generate_content("Hi")    # uses the active account
    >>> async with mgr.client() as client:        # full gemini-webapi API
    ...     chat = client.start_chat()
    """

    def __init__(
        self,
        *,
        home: str | os.PathLike[str] | None = None,
        key: str | None = None,
        max_slots: int | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or Settings.from_env(home=home, key=key, max_slots=max_slots)
        self._store = VaultStore(self._settings)
        self._shared: ManagedClient | None = None
        self._shared_lock: asyncio.Lock | None = None

    @property
    def settings(self) -> Settings:
        return self._settings

    # ------------------------------------------------------------------ helpers
    def _validate_slot(self, slot: int) -> int:
        if isinstance(slot, bool) or not isinstance(slot, int):
            raise SlotError(f"Slot must be an integer, got {slot!r}.")
        if not 1 <= slot <= self._settings.max_slots:
            raise SlotError(f"Slot must be between 1 and {self._settings.max_slots}, got {slot}.")
        return slot

    def _resolve_slot(self, slot: int | None) -> int:
        if slot is not None:
            return self._validate_slot(slot)
        active = self._store.load().active_slot
        if active is None:
            raise NoActiveAccountError()
        return active

    @staticmethod
    def _find_duplicate(vault: VaultData, email: str | None, slot: int) -> None:
        if not email:
            return
        for other in vault.accounts.values():
            if other.slot != slot and other.email and other.email.lower() == email.lower():
                raise DuplicateAccountError(email, other.slot)

    # ------------------------------------------------------------------ inspection
    def accounts(self) -> list[Account]:
        """All connected accounts, ordered by slot."""
        vault = self._store.load()
        return [
            Account.from_stored(acc, active_slot=vault.active_slot)
            for _, acc in sorted(vault.accounts.items())
        ]

    def get(self, slot: int) -> Account:
        self._validate_slot(slot)
        vault = self._store.load()
        stored = vault.accounts.get(slot)
        if stored is None:
            raise SlotEmptyError(slot)
        return Account.from_stored(stored, active_slot=vault.active_slot)

    def active(self) -> Account | None:
        """The active account, or None if nothing is selected."""
        vault = self._store.load()
        if vault.active_slot is None:
            return None
        return Account.from_stored(vault.accounts[vault.active_slot], active_slot=vault.active_slot)

    def free_slots(self) -> list[int]:
        used = set(self._store.load().accounts)
        return [s for s in range(1, self._settings.max_slots + 1) if s not in used]

    # ------------------------------------------------------------------ slot management
    def set_active(self, slot: int) -> Account:
        self._validate_slot(slot)
        with self._store.transaction() as vault:
            if slot not in vault.accounts:
                raise SlotEmptyError(slot)
            vault.active_slot = slot
            return Account.from_stored(vault.accounts[slot], active_slot=slot)

    def remove(self, slot: int) -> Account:
        """Delete the account from the vault and return what was removed.

        If it was the active account, nothing is active afterwards (select another
        explicitly). This only forgets the session locally; it does not sign the
        session out on Google's side.
        """
        self._validate_slot(slot)
        with self._store.transaction() as vault:
            stored = vault.accounts.pop(slot, None)
            if stored is None:
                raise SlotEmptyError(slot)
            removed = Account.from_stored(stored, active_slot=vault.active_slot)
            if vault.active_slot == slot:
                vault.active_slot = None
            return removed

    def set_proxy(self, slot: int, proxy: str | None) -> Account:
        """Set or clear the proxy used for this account's Gemini requests."""
        self._validate_slot(slot)
        with self._store.transaction() as vault:
            stored = vault.accounts.get(slot)
            if stored is None:
                raise SlotEmptyError(slot)
            stored.proxy = proxy or None
            return Account.from_stored(stored, active_slot=vault.active_slot)

    # ------------------------------------------------------------------ login
    async def login(
        self,
        slot: int,
        *,
        timeout: float = 300.0,
        headless: bool = False,
        browser: str = "auto",
        proxy: str | None = None,
        verify: bool = True,
        on_status: Callable[[str], None] | None = None,
    ) -> Account:
        """Sign in through a browser window and store the session in ``slot``.

        Fails immediately if the slot is occupied. With ``verify`` the captured session is
        tried against Gemini before it is saved. The first account added becomes active.
        """
        self._validate_slot(slot)
        existing = self._store.load().accounts.get(slot)
        if existing is not None:
            raise SlotOccupiedError(slot, existing.email)

        result: LoginResult = await browser_login(
            timeout=timeout, headless=headless, browser=browser, proxy=proxy, on_status=on_status
        )
        self._find_duplicate(self._store.load(), result.email, slot)

        cookies = dict(result.cookies)
        snapshot = None
        status: str | None = None
        if verify:
            if on_status:
                on_status("Verifying the session with Gemini...")
            with cache.sandbox():
                client = build_gemini_client(cookies, proxy)
                try:
                    await client.init(auto_refresh=False, timeout=60)
                    snapshot = snapshot_from_client(client)
                    status = status_name(client)
                except AuthError as exc:
                    raise LoginError("Google did not accept the captured session.") from exc
                except GeminiManagerError:
                    raise
                except Exception as exc:  # noqa: BLE001 - surface any init failure as login failure
                    raise LoginError(f"Could not verify the session: {exc}") from exc
                finally:
                    await client.close()
                cookies.update(cookies_to_dict(client.cookies))
            if status == "UNAUTHENTICATED":
                raise LoginError("Google reports the captured session as unauthenticated.")

        with self._store.transaction() as vault:
            if slot in vault.accounts:  # lost a race with another process
                raise SlotOccupiedError(slot, vault.accounts[slot].email)
            self._find_duplicate(vault, result.email, slot)
            stored = StoredAccount(
                slot=slot,
                cookies=cookies,
                created_at=utcnow_iso(),
                email=result.email,
                display_name=result.display_name,
                proxy=proxy or None,
                status=status,
                usage=snapshot.to_dict() if snapshot else None,
                cookies_synced_at=utcnow_iso(),
            )
            vault.accounts[slot] = stored
            if vault.active_slot is None:
                vault.active_slot = slot
            return Account.from_stored(stored, active_slot=vault.active_slot)

    def login_sync(self, slot: int, **kwargs: Any) -> Account:
        """Blocking variant of :meth:`login` for scripts (must not be called inside a running loop)."""
        return asyncio.run(self.login(slot, **kwargs))

    # ------------------------------------------------------------------ usage
    async def refresh_usage(self, slot: int | None = None) -> Account:
        """Fetch fresh limits for one account (default: active) and store them."""
        target = self._resolve_slot(slot)
        shared = self._shared
        if shared is not None and shared.slot == target and shared.is_running:
            await shared.refresh_usage()
        else:
            # start() already stores a fresh snapshot after init.
            async with self.client(target, auto_refresh=False, timeout=60):
                pass
        return self.get(target)

    async def refresh_all_usage(self) -> list[Account]:
        """Refresh every account; failures are logged and the stale snapshot is kept."""
        for account in self.accounts():
            try:
                await self.refresh_usage(account.slot)
            except Exception as exc:  # noqa: BLE001 - one bad account must not stop the rest
                log.warning("Could not refresh usage for slot %s: %s", account.slot, exc)
        return self.accounts()

    # ------------------------------------------------------------------ gemini access
    def client(self, slot: int | None = None, **init_options: Any) -> ManagedClient:
        """A client for ``slot`` (default: the active account right now).

        ``init_options`` are passed to ``GeminiClient.init`` (timeout, auto_refresh, ...).
        Use as an async context manager.
        """
        return ManagedClient(self._store, self._resolve_slot(slot), init_options)

    async def active_client(self) -> ManagedClient:
        """Shared client for the active account; re-created automatically after a switch.

        Do not keep the returned object across ``set_active`` calls; fetch it again.
        """
        if self._shared_lock is None:
            self._shared_lock = asyncio.Lock()
        async with self._shared_lock:
            vault = self._store.load()
            stored = vault.accounts.get(vault.active_slot) if vault.active_slot else None
            if stored is None:
                raise NoActiveAccountError()
            shared = self._shared
            if (
                shared is not None
                and shared.is_running
                and shared.slot == stored.slot
                and shared.account_created_at == stored.created_at
            ):
                return shared
            if shared is not None:
                self._shared = None
                await shared.close()
            fresh = ManagedClient(self._store, stored.slot)
            await fresh.start()
            self._shared = fresh
            return fresh

    async def generate_content(self, *args: Any, **kwargs: Any) -> Any:
        """``GeminiClient.generate_content`` on the active account."""
        return await (await self.active_client()).generate_content(*args, **kwargs)

    async def generate_content_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        """``GeminiClient.generate_content_stream`` on the active account."""
        client = await self.active_client()
        async for chunk in client.generate_content_stream(*args, **kwargs):
            yield chunk

    async def aclose(self) -> None:
        shared, self._shared = self._shared, None
        if shared is not None:
            await shared.close()

    async def __aenter__(self) -> "AccountManager":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

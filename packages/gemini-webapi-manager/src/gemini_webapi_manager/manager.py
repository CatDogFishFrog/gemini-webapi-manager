"""AccountManager: публічна точка входу бібліотеки (надбудова над ``gemini_vault.AccountRegistry``)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

from gemini_vault.config import Settings
from gemini_vault.errors import NoActiveAccountError
from gemini_vault.models import Account
from gemini_vault.registry import AccountRegistry
from gemini_vault.sessions import ManagedClient


class AccountManager(AccountRegistry):
    """Кілька акаунтів Google для gemini-webapi.

    Слоти ``1..max_slots``. Огляд і керування слотами синхронні (без мережі); вхід, оновлення
    лімітів і виклики Gemini — ``async``.

    >>> mgr = AccountManager()                    # ключ з GEMINI_MANAGER_KEY
    >>> await mgr.login(1)                        # вікно браузера (або method="console")
    >>> mgr.set_active(1)
    >>> out = await mgr.generate_content("Hi")    # активний акаунт
    >>> async with mgr.client() as client:        # повний API gemini-webapi
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
        super().__init__(home=home, key=key, max_slots=max_slots, settings=settings)
        self._shared: ManagedClient | None = None
        self._shared_lock: asyncio.Lock | None = None

    # ------------------------------------------------------------------ ліміти
    async def refresh_usage(self, slot: int | None = None) -> Account:
        """Свіжі ліміти для одного акаунта (за замовчуванням активного)."""
        target = self._resolve_slot(slot)
        shared = self._shared
        if shared is not None and shared.slot == target and shared.is_running:
            await shared.refresh_usage()
            return self.get(target)
        return await super().refresh_usage(target)

    # ------------------------------------------------------------------ доступ до Gemini
    async def active_client(self) -> ManagedClient:
        """Спільний клієнт активного акаунта; після перемикання створюється заново.

        Не зберігайте повернений об'єкт через виклики ``set_active``: беріть його щоразу.
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
        """``GeminiClient.generate_content`` на активному акаунті."""
        return await (await self.active_client()).generate_content(*args, **kwargs)

    async def generate_content_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        """``GeminiClient.generate_content_stream`` на активному акаунті."""
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

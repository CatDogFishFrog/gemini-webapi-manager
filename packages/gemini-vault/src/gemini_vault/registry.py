"""AccountRegistry: керування слотами акаунтів у зашифрованому сховищі.

Це спільна основа для обох фронтендів (gemini-webapi-manager та gemini-web2api-manager).
Перегляд і керування слотами синхронні (без мережі); вхід, оновлення лімітів і виклики
Gemini — ``async``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from .login import DEFAULT_TARGET, LoginIO, LoginResult, LoginTarget, browser_login, console_login
from .models import Account, StoredAccount, VaultData
from .sessions import ManagedClient, revoke_session, session_is_valid, verify_session
from .storage import VaultStore
from .usage import UsageSnapshot, utcnow_iso

log = logging.getLogger(__name__)

LOGIN_METHODS = ("browser", "console")


@dataclass(frozen=True)
class RemoveResult:
    """Результат ``sign_out``: що видалено і що вдалося зробити на боці Google."""

    account: Account
    revoke_requested: bool  # запит на вихід надіслано й прийнято
    revoke_confirmed: bool | None  # True: Google більше не приймає сесію; None: не перевіряли/невідомо


class AccountRegistry:
    """Слоти ``1..max_slots`` з акаунтами Google.

    >>> reg = AccountRegistry()                       # ключ з GEMINI_MANAGER_KEY
    >>> await reg.login(1, method="console")          # вхід лише через термінал
    >>> reg.accounts()                                # -> list[Account]
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

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def store(self) -> VaultStore:
        return self._store

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

    # ------------------------------------------------------------------ огляд
    def accounts(self) -> list[Account]:
        """Усі підключені акаунти, за порядком слотів."""
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
        """Активний акаунт або None, якщо жоден не обраний."""
        vault = self._store.load()
        if vault.active_slot is None:
            return None
        return Account.from_stored(vault.accounts[vault.active_slot], active_slot=vault.active_slot)

    def free_slots(self) -> list[int]:
        used = set(self._store.load().accounts)
        return [s for s in range(1, self._settings.max_slots + 1) if s not in used]

    # ------------------------------------------------------------------ керування слотами
    def set_active(self, slot: int) -> Account:
        self._validate_slot(slot)
        with self._store.transaction() as vault:
            if slot not in vault.accounts:
                raise SlotEmptyError(slot)
            vault.active_slot = slot
            return Account.from_stored(vault.accounts[slot], active_slot=slot)

    def remove(self, slot: int) -> Account:
        """Видалити акаунт зі сховища (лише локально, сесія на стороні Google лишається).

        Якщо це був активний акаунт — після видалення активного немає (обрати іншого треба
        явно). Для виходу на стороні Google див. :meth:`sign_out`.
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

    async def sign_out(self, slot: int, *, confirm: bool = True) -> RemoveResult:
        """Спробувати завершити сесію на стороні Google, а тоді видалити слот.

        Запит на вихід — «найкращі зусилля». Із ``confirm`` після нього перевіряється, що
        Google справді відхиляє сесію (інакше ``revoke_confirmed`` буде False). Слот видаляється
        в будь-якому разі: користувач цього попросив.
        """
        self._validate_slot(slot)
        stored = self._store.load().accounts.get(slot)
        if stored is None:
            raise SlotEmptyError(slot)
        requested = False
        confirmed: bool | None = None
        try:
            requested = await revoke_session(stored.cookies, stored.proxy)
            if requested and confirm:
                confirmed = not await session_is_valid(stored.cookies, stored.proxy)
        except Exception as exc:  # noqa: BLE001 - мережа/бібліотека: звітуємо, але видаляємо
            log.warning("Не вдалося завершити сесію слота %s на стороні Google: %s", slot, exc)
        return RemoveResult(self.remove(slot), requested, confirmed)

    def set_proxy(self, slot: int, proxy: str | None) -> Account:
        """Встановити або прибрати проксі для запитів цього акаунта."""
        self._validate_slot(slot)
        with self._store.transaction() as vault:
            stored = vault.accounts.get(slot)
            if stored is None:
                raise SlotEmptyError(slot)
            stored.proxy = proxy or None
            return Account.from_stored(stored, active_slot=vault.active_slot)

    def mark_limit_hit(self, slot: int) -> None:
        """Записати, що акаунт упирався в ліміт (для шлюзів, які виявили це самі)."""
        self._validate_slot(slot)
        with self._store.transaction() as vault:
            stored = vault.accounts.get(slot)
            if stored is not None:
                stored.last_limit_hit_at = utcnow_iso()

    def rekey(self, new_key: str, *, backup: bool = True) -> Path | None:
        """Перевести сховище на інший ключ шифрування (акаунти входити заново не потрібно).

        Повертає шлях до резервної копії старого файла. Процеси, що тримають старий ключ
        (наприклад, працюючий шлюз), після цього не зможуть розшифрувати файл: їх треба
        зупинити до перешифрування й запустити з новим ключем.
        """
        backup_path = self._store.rekey(new_key, backup=backup)
        self._settings = self._store.settings
        return backup_path

    # ------------------------------------------------------------------ вхід
    async def login(
        self,
        slot: int,
        *,
        method: str = "browser",
        timeout: float | None = None,
        headless: bool | None = None,
        browser: str = "auto",
        proxy: str | None = None,
        verify: bool = True,
        replace: bool = False,
        on_status: Callable[[str], None] | None = None,
        io: LoginIO | None = None,
        email: str | None = None,
        executable_path: str | None = None,
        debug_dir: str | Path | None = None,
        target: LoginTarget = DEFAULT_TARGET,
    ) -> Account:
        """Увійти в Google і зберегти сесію в ``slot``.

        ``method="browser"`` відкриває вікно браузера; ``method="console"`` працює повністю в
        терміналі (headless-браузер, логін/пароль/2FA вводяться в консоль).

        Зайнятий слот → ``SlotOccupiedError``, якщо не вказано ``replace=True``: тоді новий
        вхід замінює акаунт атомарно і зберігає проксі та статус «активного». Із ``verify``
        захоплену сесію перевіряють у Gemini до збереження. Перший доданий акаунт стає активним.
        """
        self._validate_slot(slot)
        if method not in LOGIN_METHODS:
            raise LoginError(f"Unknown login method {method!r}; use one of {', '.join(LOGIN_METHODS)}.")
        existing = self._store.load().accounts.get(slot)
        if existing is not None and not replace:
            raise SlotOccupiedError(slot, existing.email)
        if proxy is None and existing is not None:
            proxy = existing.proxy

        if method == "console":
            result: LoginResult = await console_login(
                io,
                email=email,
                timeout=timeout if timeout is not None else 900.0,
                headless=True if headless is None else headless,
                browser=browser,
                proxy=proxy,
                executable_path=executable_path,
                debug_dir=debug_dir,
                target=target,
                on_status=on_status,
            )
        else:
            result = await browser_login(
                timeout=timeout if timeout is not None else 300.0,
                headless=False if headless is None else headless,
                browser=browser,
                proxy=proxy,
                on_status=on_status,
                executable_path=executable_path,
                target=target,
            )
        self._find_duplicate(self._store.load(), result.email, slot)

        cookies = dict(result.cookies)
        snapshot: UsageSnapshot | None = None
        status: str | None = None
        if verify:
            if on_status:
                on_status("Verifying the session with Gemini...")
            verified = await verify_session(cookies, proxy)
            cookies, snapshot, status = verified.cookies, verified.snapshot, verified.status

        with self._store.transaction() as vault:
            current = vault.accounts.get(slot)
            if current is not None and not replace:  # програли гонку іншому процесу
                raise SlotOccupiedError(slot, current.email)
            self._find_duplicate(vault, result.email, slot)
            stored = StoredAccount(
                slot=slot,
                cookies=cookies,
                # мікросекунди: заміна акаунта в межах тієї самої секунди теж змінює «ідентичність» запису
                created_at=utcnow_iso("microseconds"),
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
        """Блокуючий варіант :meth:`login` для скриптів (не викликати всередині циклу подій)."""
        return asyncio.run(self.login(slot, **kwargs))

    # ------------------------------------------------------------------ ліміти
    def client(self, slot: int | None = None, **init_options: Any) -> ManagedClient:
        """Клієнт для ``slot`` (за замовчуванням — активний на цей момент).

        ``init_options`` передаються в ``GeminiClient.init`` (timeout, auto_refresh, ...).
        Використовувати як async context manager.
        """
        return ManagedClient(self._store, self._resolve_slot(slot), init_options)

    async def refresh_usage(self, slot: int | None = None) -> Account:
        """Отримати свіжі ліміти для одного акаунта (за замовчуванням активного)."""
        target = self._resolve_slot(slot)
        # start() уже зберігає свіжий знімок після init.
        async with self.client(target, auto_refresh=False, timeout=60):
            pass
        return self.get(target)

    async def refresh_all_usage(self) -> list[Account]:
        """Оновити всі акаунти; збої логуються, старий знімок лишається."""
        for account in self.accounts():
            try:
                await self.refresh_usage(account.slot)
            except GeminiManagerError as exc:
                log.warning("Could not refresh usage for slot %s: %s", account.slot, exc)
            except Exception as exc:  # noqa: BLE001 - один поганий акаунт не має зупиняти решту
                log.warning("Could not refresh usage for slot %s: %s", account.slot, exc)
        return self.accounts()

"""SessionKeeper: фоновий потік, що тримає сесії акаунтів свіжими й годує ними пул.

Для кожного слота vault тримається відкритий ``ManagedClient`` (gemini-webapi). Він:

* отримує xsrf-токен (``at``) і ``bl`` — без них автентифіковані запити апстріму не проходять;
* ротує ``__Secure-1PSIDTS`` у фоні й записує нові cookie назад у зашифрований vault;
* періодично (та на вимогу після ліміту) оновлює знімок лімітів.

Результат — незмінні ``Credentials`` + ``UsageSnapshot`` у ``AccountPool``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Mapping

from gemini_vault.errors import GeminiManagerError, SessionExpiredError
from gemini_vault.registry import AccountRegistry
from gemini_vault.sessions import ManagedClient, cookies_to_dict, status_name
from gemini_vault.usage import snapshot_from_client

from .pool import AccountPool
from .scope import Credentials

log = logging.getLogger(__name__)

_FIRST = ("SID", "HSID", "SSID", "APISID", "SAPISID", "__Secure-1PSID", "__Secure-1PSIDTS")
_MIN_FORCED_GAP = 20.0  # не частіше за раз на 20 с оновлювати той самий слот «на вимогу»


def build_cookie_header(cookies: Mapping[str, str]) -> str:
    """``name=value; ...`` — головні cookie першими, як їх надсилає браузер."""
    ordered = [k for k in _FIRST if cookies.get(k)] + sorted(k for k in cookies if k not in _FIRST and cookies[k])
    return "; ".join(f"{k}={cookies[k]}" for k in ordered)


def build_credentials(slot: int, cookies: Mapping[str, str], raw, proxy: str | None) -> Credentials | None:
    """None, якщо Gemini не дав xsrf-токена (гостьова/недійсна сесія)."""
    token = getattr(raw, "access_token", None)
    if not token:
        return None
    return Credentials(
        slot=slot,
        cookie_header=build_cookie_header(cookies),
        sapisid=cookies.get("SAPISID") or cookies.get("__Secure-3PAPISID"),
        xsrf_token=token,
        build_label=getattr(raw, "build_label", None),
        proxy=proxy,
    )


class SessionKeeper:
    def __init__(
        self,
        registry: AccountRegistry,
        pool: AccountPool,
        *,
        slots: list[int] | None = None,
        refresh_interval: float = 600.0,
        init_timeout: float = 60.0,
    ) -> None:
        self._registry = registry
        self._pool = pool
        self._only = set(slots) if slots else None
        self._interval = refresh_interval
        self._init_timeout = init_timeout
        self._clients: dict[int, ManagedClient] = {}
        self._due: dict[int, float] = {}
        self._forced: dict[int, float] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._wanted: dict[int, tuple[str | None, str | None]] = {}  # слот -> (created_at, проксі) у сховищі
        self._attempted: dict[int, tuple[str | None, str | None]] = {}  # з чим востаннє пробували відкрити слот
        self._next_scan = 0.0

    # ------------------------------------------------------------------ життєвий цикл
    def start(self, *, wait: float = 120.0) -> None:
        """Запустити потік і дочекатися першого проходу по всіх слотах."""
        self._thread = threading.Thread(target=self._thread_main, name="session-keeper", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=wait)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run())
        finally:
            self._loop.close()

    async def _run(self) -> None:
        try:
            await self._cycle(first=True)
            self._ready.set()
            while not self._stop.is_set():
                await asyncio.sleep(1.0)
                await self._cycle(first=False)
        finally:
            self._ready.set()
            for slot in list(self._clients):
                await self._close(slot)

    # ------------------------------------------------------------------ цикл
    def _scan_vault(self) -> dict[int, tuple[str | None, str | None]]:
        """Слоти, з якими працюємо, й «відбиток» запису: за ним видно, що акаунт замінили або змінили проксі."""
        accounts = self._registry.store.load().accounts
        return {
            slot: (acc.created_at, acc.proxy)
            for slot, acc in sorted(accounts.items())
            if self._only is None or slot in self._only
        }

    async def _cycle(self, *, first: bool) -> None:
        if time.monotonic() >= self._next_scan:  # vault читаємо не частіше за раз на 5 с
            try:
                self._wanted = self._scan_vault()
            except GeminiManagerError as exc:
                log.error("Cannot read the vault: %s", exc)
                return
            self._next_scan = time.monotonic() + 5.0
        wanted = self._wanted
        for slot, client in list(self._clients.items()):
            fingerprint = wanted.get(slot)
            if fingerprint is None:  # слот видалили з vault
                await self._close(slot)
                self._pool.remove_slot(slot)
            elif fingerprint != (client.account_created_at, client.account_proxy):
                # Акаунт у слоті замінили (або змінили проксі): клієнт тримає сесію старого запису,
                # тому його закривають і відкривають заново. Для заміни акаунта пул теж починає з нуля
                # (паузи й лічильники належали іншому акаунту).
                replaced = fingerprint[0] != client.account_created_at
                log.info("Slot %s: %s; reopening the session", slot, "account replaced" if replaced else "proxy changed")
                await self._close(slot)
                if replaced:
                    self._pool.remove_slot(slot)
                self._due.pop(slot, None)
        for slot in list(self._attempted):
            if slot not in wanted:
                del self._attempted[slot]
        now = time.monotonic()
        tasks = []
        for slot, fingerprint in wanted.items():
            if slot not in self._clients:
                if self._attempted.get(slot) not in (None, fingerprint):
                    self._due[slot] = 0.0  # запис змінився після невдалої спроби: повторюємо одразу
                if now >= self._due.get(slot, 0.0):
                    tasks.append(self._open(slot))
            elif now >= self._due.get(slot, 0.0):
                tasks.append(self._refresh(slot))
        if tasks:
            await asyncio.gather(*tasks)

    async def _open(self, slot: int) -> None:
        self._due[slot] = time.monotonic() + self._interval  # при збої повторимо через інтервал
        self._attempted[slot] = self._wanted.get(slot)
        client = ManagedClient(self._registry.store, slot, {"timeout": self._init_timeout})
        try:
            await client.start()
        except SessionExpiredError as exc:
            self._pool.update_slot(slot, email=self._email(slot), credentials=None, usage=None, status="UNAUTHENTICATED")
            self._pool.mark_expired(slot, "Google rejected the stored session")
            log.error("%s", exc)
            return
        except Exception as exc:  # noqa: BLE001 - мережа тощо: спробуємо пізніше
            log.warning("Slot %s: could not start (%s); will retry", slot, exc)
            self._pool.note_error(slot, f"start failed: {exc}")
            return
        self._clients[slot] = client
        self._publish(slot)

    async def _refresh(self, slot: int) -> None:
        client = self._clients[slot]
        self._due[slot] = time.monotonic() + self._interval
        try:
            await client.refresh_usage()
            client.sync_cookies()
            self._publish(slot)
        except SessionExpiredError:
            self._pool.mark_expired(slot, "session expired")
            await self._close(slot)
        except Exception as exc:  # noqa: BLE001
            log.warning("Slot %s: refresh failed (%s); reopening", slot, exc)
            await self._close(slot)
            await self._open(slot)

    async def _close(self, slot: int) -> None:
        client = self._clients.pop(slot, None)
        if client is not None:
            try:
                await client.close()
            except Exception as exc:  # noqa: BLE001
                log.debug("Slot %s: close failed: %s", slot, exc)

    def _email(self, slot: int) -> str | None:
        try:
            return self._registry.get(slot).email
        except GeminiManagerError:
            return None

    def _publish(self, slot: int) -> None:
        client = self._clients[slot]
        raw = client.raw
        stored = self._registry.store.load().accounts.get(slot)
        if stored is None:
            return
        if stored.created_at != client.account_created_at:
            # Слот замінили між проходами: cookie цього клієнта належать старому акаунту, тож їх не публікуємо;
            # найближчий прохід побачить заміну й відкриє слот заново.
            self._next_scan = 0.0
            return
        cookies = dict(stored.cookies)
        cookies.update(cookies_to_dict(raw.cookies))
        self._pool.update_slot(
            slot,
            email=stored.email,
            credentials=build_credentials(slot, cookies, raw, stored.proxy),
            usage=snapshot_from_client(raw),
            status=status_name(raw),
        )

    # ------------------------------------------------------------------ виклики з інших потоків
    def request_refresh(self, slot: int) -> None:
        """Оновити ліміти слота якнайшвидше (наприклад, після ліміту); безпечно з будь-якого потоку."""
        loop = self._loop
        if loop is None or slot not in self._clients:
            return
        now = time.monotonic()
        if now - self._forced.get(slot, 0.0) < _MIN_FORCED_GAP:
            return
        self._forced[slot] = now
        loop.call_soon_threadsafe(self._due.__setitem__, slot, 0.0)

    def refresh_all(self) -> None:
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(lambda: [self._due.__setitem__(s, 0.0) for s in list(self._due)])

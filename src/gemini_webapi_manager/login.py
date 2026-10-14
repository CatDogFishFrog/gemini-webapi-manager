"""Interactive login: open a real browser window, wait for the user to sign in, collect cookies.

A fresh, isolated browser context is used every time, so the user's everyday browser
session is never touched (important: gemini-webapi rotates ``__Secure-1PSIDTS``, which
can otherwise sign the browser out).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

from .errors import LoginError, LoginTimeoutError

GEMINI_URL = "https://gemini.google.com/app"
GEMINI_HOST = "gemini.google.com"
PSID = "__Secure-1PSID"
PSIDTS = "__Secure-1PSIDTS"

_PSIDTS_GRACE = 20.0
_BROWSER_CHOICES = ("auto", "chrome", "msedge", "chromium")

_IDENTITY_JS = """
() => {
  let email = null;
  try {
    const w = window.WIZ_global_data;
    if (w && typeof w.oPEP7c === 'string' && w.oPEP7c.includes('@')) email = w.oPEP7c;
  } catch (e) {}
  const el = document.querySelector('a[aria-label^="Google Account"]');
  return { email, label: el ? el.getAttribute('aria-label') : null };
}
"""


@dataclass(frozen=True)
class LoginResult:
    email: str | None
    display_name: str | None
    cookies: dict[str, str]


def _google_cookie_dict(cookies: list[dict[str, Any]]) -> dict[str, str]:
    """Flatten Playwright cookies to name->value, keeping google.com ones; '.google.com' wins."""
    ranked = sorted(cookies, key=lambda c: str(c.get("domain", "")).startswith("."))
    chosen: dict[str, str] = {}
    for cookie in ranked:
        domain = str(cookie.get("domain", "")).lstrip(".")
        if domain.endswith("google.com") and cookie.get("value"):
            chosen[cookie["name"]] = cookie["value"]
    return chosen


def _parse_label(label: str | None) -> tuple[str | None, str | None]:
    """Parse 'Google Account: Name (email@host)' into (email, name)."""
    if not label:
        return None, None
    email_match = re.search(r"\(([^()\s]+@[^()\s]+)\)", label)
    name_match = re.match(r"\s*Google Account:\s*(.+?)\s*\(", label, flags=re.S)
    return (
        email_match.group(1) if email_match else None,
        name_match.group(1).strip() if name_match else None,
    )


def _playwright_proxy(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    server = f"{parsed.scheme}://{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "")
    result = {"server": server}
    if parsed.username:
        result["username"] = unquote(parsed.username)
    if parsed.password:
        result["password"] = unquote(parsed.password)
    return result


async def _launch(pw: Any, browser: str, headless: bool, proxy: str | None, pw_error: type) -> Any:
    if browser not in _BROWSER_CHOICES:
        raise LoginError(f"Unknown browser {browser!r}; choose one of {', '.join(_BROWSER_CHOICES)}.")
    channels: list[str | None]
    if browser == "auto":
        channels = ["chrome", "msedge", None]  # None = Playwright's bundled Chromium
    else:
        channels = [None if browser == "chromium" else browser]

    options: dict[str, Any] = {
        "headless": headless,
        "args": ["--disable-blink-features=AutomationControlled"],
        "ignore_default_args": ["--enable-automation"],
    }
    if proxy:
        options["proxy"] = _playwright_proxy(proxy)

    last_error: Exception | None = None
    for channel in channels:
        try:
            return await pw.chromium.launch(channel=channel, **options)
        except pw_error as exc:
            last_error = exc
    raise LoginError(
        "Could not start a browser. Install Google Chrome/Edge, or run "
        "`gemini-webapi-manager install-browser` to download Playwright's Chromium."
    ) from last_error


async def _read_identity(page: Any, pw_error: type) -> tuple[str | None, str | None]:
    email: str | None = None
    name: str | None = None
    for _ in range(8):
        try:
            data = await page.evaluate(_IDENTITY_JS)
        except pw_error:
            data = None
        if isinstance(data, dict):
            label_email, label_name = _parse_label(data.get("label"))
            email = data.get("email") or label_email or email
            name = label_name or name
        if email:
            break
        await asyncio.sleep(1.0)
    return email, name


async def browser_login(
    *,
    timeout: float = 300.0,
    headless: bool = False,
    browser: str = "auto",
    proxy: str | None = None,
    on_status: Callable[[str], None] | None = None,
) -> LoginResult:
    """Open a browser, wait until the user is signed in to Gemini, and return their session."""
    try:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - playwright is a hard dependency
        raise LoginError("Playwright is not installed: pip install playwright") from exc

    say = on_status or (lambda _message: None)
    deadline = time.monotonic() + timeout

    async with async_playwright() as pw:
        browser_obj = await _launch(pw, browser, headless, proxy, PlaywrightError)
        try:
            context = await browser_obj.new_context(viewport={"width": 1100, "height": 820})
            page = await context.new_page()
            try:
                await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=60_000)
            except PlaywrightError as exc:
                raise LoginError(f"Could not open Gemini: {exc}") from exc

            say("Browser opened. Sign in to your Google account in the window...")
            page = await _wait_for_session(browser_obj, context, deadline, PlaywrightError)

            say("Signed in. Collecting session data...")
            grace_end = min(deadline, time.monotonic() + _PSIDTS_GRACE)
            while time.monotonic() < grace_end:
                names = {c["name"] for c in await context.cookies(GEMINI_URL)}
                if PSIDTS in names:
                    break
                await asyncio.sleep(1.0)
            try:
                await page.wait_for_load_state("load", timeout=15_000)
            except PlaywrightError:
                pass

            email, display_name = await _read_identity(page, PlaywrightError)
            cookies = _google_cookie_dict(await context.cookies())
        except PlaywrightError as exc:
            raise LoginError(f"Browser error during login: {exc}") from exc
        finally:
            try:
                await browser_obj.close()
            except PlaywrightError:
                pass

    if PSID not in cookies:
        raise LoginError("Login finished but no __Secure-1PSID cookie was captured.")
    if not email:
        say("Warning: could not read the account e-mail from the page.")
    return LoginResult(email=email, display_name=display_name, cookies=cookies)


async def _wait_for_session(browser_obj: Any, context: Any, deadline: float, pw_error: type) -> Any:
    """Poll until the session cookie exists and a tab is back on gemini.google.com."""
    while True:
        if time.monotonic() > deadline:
            raise LoginTimeoutError("Timed out waiting for the sign-in to complete.")
        if not browser_obj.is_connected():
            raise LoginError("The browser was closed before the login finished.")
        pages = [p for p in context.pages if not p.is_closed()]
        if not pages:
            raise LoginError("The browser window was closed before the login finished.")
        try:
            names = {c["name"] for c in await context.cookies(GEMINI_URL)}
        except pw_error as exc:
            raise LoginError("The browser was closed before the login finished.") from exc
        on_gemini = [p for p in pages if urlparse(p.url).hostname == GEMINI_HOST]
        if PSID in names and on_gemini:
            return on_gemini[0]
        await asyncio.sleep(1.0)

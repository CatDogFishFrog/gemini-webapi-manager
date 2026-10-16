"""Window-based login: open a real browser window, wait for the user to sign in, collect cookies.

A fresh, isolated browser context is used every time, so the user's everyday browser
session is never touched (important: gemini-webapi rotates ``__Secure-1PSIDTS``, which
can otherwise sign the browser out).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from ..errors import LoginError, LoginTimeoutError
from .common import (
    DEFAULT_TARGET,
    PSID,
    LoginResult,
    LoginTarget,
    capture_session,
    import_playwright,
    launch_browser,
)


async def browser_login(
    *,
    timeout: float = 300.0,
    headless: bool = False,
    browser: str = "auto",
    proxy: str | None = None,
    on_status: Callable[[str], None] | None = None,
    executable_path: str | None = None,
    target: LoginTarget = DEFAULT_TARGET,
) -> LoginResult:
    """Open a browser, wait until the user is signed in to Gemini, and return their session."""
    async_playwright, PlaywrightError = import_playwright()
    say = on_status or (lambda _message: None)
    deadline = time.monotonic() + timeout

    async with async_playwright() as pw:
        browser_obj = await launch_browser(
            pw, browser, headless, proxy, PlaywrightError, executable_path=executable_path
        )
        try:
            context = await browser_obj.new_context(viewport={"width": 1100, "height": 820})
            page = await context.new_page()
            try:
                await page.goto(target.app_url, wait_until="domcontentloaded", timeout=60_000)
            except PlaywrightError as exc:
                raise LoginError(f"Could not open Gemini: {exc}") from exc

            say("Browser opened. Sign in to your Google account in the window...")
            page = await _wait_for_session(browser_obj, context, deadline, PlaywrightError, target)
            say("Signed in. Collecting session data...")
            return await capture_session(
                context, page, target=target, deadline=deadline, pw_error=PlaywrightError, say=say
            )
        except PlaywrightError as exc:
            raise LoginError(f"Browser error during login: {exc}") from exc
        finally:
            try:
                await browser_obj.close()
            except PlaywrightError:
                pass


async def _wait_for_session(
    browser_obj: Any, context: Any, deadline: float, pw_error: type, target: LoginTarget
) -> Any:
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
            names = {c["name"] for c in await context.cookies(target.app_url)}
        except pw_error as exc:
            raise LoginError("The browser was closed before the login finished.") from exc
        on_gemini = [p for p in pages if urlparse(p.url).hostname == target.app_host]
        if PSID in names and on_gemini:
            return on_gemini[0]
        await asyncio.sleep(1.0)

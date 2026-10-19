"""Pieces shared by the window-based and the console login: browser launch, session capture."""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

from ..errors import LoginError

GEMINI_URL = "https://gemini.google.com/app"
GEMINI_HOST = "gemini.google.com"
GOOGLE_SIGN_IN_URL = (
    "https://accounts.google.com/v3/signin/identifier?hl=en"
    "&continue=https%3A%2F%2Fgemini.google.com%2Fapp"
    "&flowName=GlifWebSignIn&flowEntry=ServiceLogin"
)
PSID = "__Secure-1PSID"
PSIDTS = "__Secure-1PSIDTS"

ENV_NO_SANDBOX = "GEMINI_VAULT_NO_SANDBOX"
ENV_BROWSER_PATH = "GEMINI_VAULT_BROWSER_PATH"

PSIDTS_GRACE = 20.0
BROWSER_CHOICES = ("auto", "chrome", "msedge", "chromium")

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


@dataclass(frozen=True)
class LoginTarget:
    """Where to sign in and what counts as "signed in". Overridable so tests can use a mock."""

    sign_in_url: str = GOOGLE_SIGN_IN_URL
    app_url: str = GEMINI_URL
    app_host: str = GEMINI_HOST
    cookie_domain: str = "google.com"


DEFAULT_TARGET = LoginTarget()


def google_cookie_dict(
    cookies: list[dict[str, Any]], domain_suffix: str = "google.com"
) -> dict[str, str]:
    """Flatten Playwright cookies to name->value, keeping google.com ones; '.google.com' wins."""
    ranked = sorted(cookies, key=lambda c: str(c.get("domain", "")).startswith("."))
    chosen: dict[str, str] = {}
    for cookie in ranked:
        domain = str(cookie.get("domain", "")).lstrip(".")
        if domain.endswith(domain_suffix) and cookie.get("value"):
            chosen[cookie["name"]] = cookie["value"]
    return chosen


def parse_label(label: str | None) -> tuple[str | None, str | None]:
    """Parse 'Google Account: Name (email@host)' into (email, name)."""
    if not label:
        return None, None
    email_match = re.search(r"\(([^()\s]+@[^()\s]+)\)", label)
    name_match = re.match(r"\s*Google Account:\s*(.+?)\s*\(", label, flags=re.S)
    return (
        email_match.group(1) if email_match else None,
        name_match.group(1).strip() if name_match else None,
    )


def playwright_proxy(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    server = f"{parsed.scheme}://{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "")
    result = {"server": server}
    if parsed.username:
        result["username"] = unquote(parsed.username)
    if parsed.password:
        result["password"] = unquote(parsed.password)
    return result


def import_playwright() -> tuple[Any, type]:
    """Import Playwright lazily: it is an optional dependency (``gemini-vault[login]``)."""
    try:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise LoginError(
            "Playwright is not installed. Install the login extra: "
            "pip install 'gemini-vault[login]'"
        ) from exc
    return async_playwright, PlaywrightError


def _running_as_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


async def launch_browser(
    pw: Any,
    browser: str,
    headless: bool,
    proxy: str | None,
    pw_error: type,
    *,
    executable_path: str | None = None,
) -> Any:
    """Start Chromium. ``executable_path`` (or ``GEMINI_VAULT_BROWSER_PATH``) wins over channels."""
    if browser not in BROWSER_CHOICES:
        raise LoginError(f"Unknown browser {browser!r}; choose one of {', '.join(BROWSER_CHOICES)}.")
    executable_path = executable_path or os.environ.get(ENV_BROWSER_PATH) or None

    args = ["--disable-blink-features=AutomationControlled"]
    if _running_as_root() or os.environ.get(ENV_NO_SANDBOX) == "1":
        # Chromium не стартує як root у контейнері; в образі admin (звичайний користувач без привілеїв) — теж
        args.append("--no-sandbox")
    options: dict[str, Any] = {
        "headless": headless,
        "args": args,
        "ignore_default_args": ["--enable-automation"],
    }
    if proxy:
        options["proxy"] = playwright_proxy(proxy)

    if executable_path:
        try:
            return await pw.chromium.launch(executable_path=executable_path, **options)
        except pw_error as exc:
            raise LoginError(f"Could not start the browser at {executable_path}: {exc}") from exc

    channels: list[str | None]
    if browser == "auto":
        channels = ["chrome", "msedge", None]  # None = Playwright's bundled Chromium
    else:
        channels = [None if browser == "chromium" else browser]

    last_error: Exception | None = None
    for channel in channels:
        try:
            return await pw.chromium.launch(channel=channel, **options)
        except pw_error as exc:
            last_error = exc
    raise LoginError(
        "Could not start a browser. Install Google Chrome/Edge, or run "
        "`gemini-vault install-browser` (add `--with-deps` on a bare Linux server), "
        f"or point {ENV_BROWSER_PATH} at a Chromium binary."
    ) from last_error


async def normalised_user_agent(browser_obj: Any) -> str | None:
    """The browser's own UA without the ``Headless`` marker (bare headless Chromium adds it)."""
    try:
        version = str(browser_obj.version)
    except Exception:  # noqa: BLE001 - cosmetic only
        return None
    if not re.match(r"^\d+(\.\d+)*$", version):
        return None
    system = {
        "win32": "Windows NT 10.0; Win64; x64",
        "darwin": "Macintosh; Intel Mac OS X 10_15_7",
    }.get(sys.platform, "X11; Linux x86_64")
    return (
        f"Mozilla/5.0 ({system}) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{version} Safari/537.36"
    )


async def read_identity(page: Any, pw_error: type) -> tuple[str | None, str | None]:
    email: str | None = None
    name: str | None = None
    for _ in range(8):
        try:
            data = await page.evaluate(_IDENTITY_JS)
        except pw_error:
            data = None
        if isinstance(data, dict):
            label_email, label_name = parse_label(data.get("label"))
            email = data.get("email") or label_email or email
            name = label_name or name
        if email:
            break
        await asyncio.sleep(1.0)
    return email, name


async def capture_session(
    context: Any,
    page: Any,
    *,
    target: LoginTarget,
    deadline: float,
    pw_error: type,
    say: Callable[[str], None],
) -> LoginResult:
    """Called once the browser is signed in: wait for rotation cookie, read identity, collect."""
    grace_end = min(deadline, time.monotonic() + PSIDTS_GRACE)
    while time.monotonic() < grace_end:
        names = {c["name"] for c in await context.cookies(target.app_url)}
        if PSIDTS in names:
            break
        await asyncio.sleep(1.0)
    try:
        await page.wait_for_load_state("load", timeout=15_000)
    except pw_error:
        pass

    email, display_name = await read_identity(page, pw_error)
    cookies = google_cookie_dict(await context.cookies(), target.cookie_domain)
    if PSID not in cookies:
        raise LoginError("Login finished but no __Secure-1PSID cookie was captured.")
    if not email:
        say("Warning: could not read the account e-mail from the page.")
    return LoginResult(email=email, display_name=display_name, cookies=cookies)

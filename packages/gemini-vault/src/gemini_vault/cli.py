"""Спільний командний рядок: ``gemini-vault`` (його ж використовують фронтенди-пакети).

Фронтенди додають власні підкоманди через ``build_parser(...).add_subparsers`` і викликають
``dispatch`` для спільних команд.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from collections.abc import Sequence

from .crypto import generate_key
from .errors import GeminiManagerError
from .models import Account
from .registry import AccountRegistry
from .sessions import usage_refresh_contract

COMMON_COMMANDS = {
    "keygen", "install-browser", "login", "login-console", "list", "use", "remove", "usage", "proxy",
    "doctor",
}


def _pct(value: float | int | None) -> str:
    return "-" if value is None else f"{value:.0f}%"


def print_accounts(accounts: list[Account]) -> None:
    if not accounts:
        print("No accounts connected.")
        return
    rows = [("SLOT", "ACTIVE", "EMAIL", "USED", "5H", "WEEK", "TIER", "STATUS")]
    for acc in accounts:
        usage = acc.usage
        rows.append(
            (
                str(acc.slot),
                "*" if acc.is_active else "",
                acc.email or "?",
                _pct(acc.usage_percent),
                _pct(usage.five_hour.percent if usage and usage.five_hour else None),
                _pct(usage.weekly.percent if usage and usage.weekly else None),
                (usage.tier if usage and usage.tier else "-"),
                acc.status or "-",
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())


def status_line(message: str) -> None:
    print(message, file=sys.stderr)


def build_parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("keygen", help="print a new random encryption key")
    ib = sub.add_parser("install-browser", help="download Playwright's Chromium")
    ib.add_argument("--with-deps", action="store_true",
                    help="also install the system libraries (bare Linux server; needs root)")

    def login_args(p: argparse.ArgumentParser, *, console_default: bool) -> None:
        p.add_argument("slot", type=int)
        p.add_argument("--console", action="store_true", default=console_default,
                       help="sign in from the terminal (headless browser; no window needed)")
        p.add_argument("--email", help="(console) account e-mail, otherwise it is asked")
        p.add_argument("--timeout", type=float, help="seconds to wait (window: 300, console: idle 900)")
        p.add_argument("--browser", choices=["auto", "chrome", "msedge", "chromium"], default="auto")
        p.add_argument("--browser-path", help="path to a Chromium/Chrome binary (or GEMINI_VAULT_BROWSER_PATH)")
        p.add_argument("--headless", action="store_true", help="(window mode) no window")
        p.add_argument("--headed", action="store_true", help="(console mode) show the window (needs a display)")
        p.add_argument("--proxy", help="proxy URL for the browser and later Gemini requests")
        p.add_argument("--no-verify", action="store_true", help="skip the Gemini check before saving")
        p.add_argument("--replace", action="store_true",
                       help="replace the account in an occupied slot (keeps its proxy and active flag)")
        p.add_argument("--debug-dir", help="(console) dump a screenshot + sanitised page description of every step")

    login = sub.add_parser("login", help="sign in into a slot (window by default, --console for terminal only)")
    login_args(login, console_default=False)
    lc = sub.add_parser("login-console", help="same as `login --console`")
    login_args(lc, console_default=True)

    lst = sub.add_parser("list", help="show connected accounts (last known usage)")
    lst.add_argument("--json", action="store_true")

    use = sub.add_parser("use", help="make a slot the active account")
    use.add_argument("slot", type=int)

    rm = sub.add_parser("remove", help="remove the account from a slot")
    rm.add_argument("slot", type=int)
    rm.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    rm.add_argument("--revoke", action="store_true",
                    help="also try to sign the session out on Google's side (best effort)")

    usage = sub.add_parser("usage", help="refresh usage from Gemini and show it")
    usage.add_argument("slot", type=int, nargs="?", help="default: all accounts")
    usage.add_argument("--json", action="store_true")

    proxy = sub.add_parser("proxy", help="set or clear the proxy of a slot")
    proxy.add_argument("slot", type=int)
    proxy.add_argument("url", nargs="?", help="omit to clear")

    sub.add_parser("doctor", help="check the environment (python deps, browser, gemini-webapi contract)")
    return parser


async def _cmd_login(registry: AccountRegistry, args: argparse.Namespace) -> int:
    console = bool(args.console)
    account = await registry.login(
        args.slot,
        method="console" if console else "browser",
        timeout=args.timeout,
        headless=(not args.headed) if console else args.headless,
        browser=args.browser,
        proxy=args.proxy,
        verify=not args.no_verify,
        replace=args.replace,
        on_status=status_line,
        email=args.email,
        executable_path=args.browser_path,
        debug_dir=args.debug_dir,
    )
    print(f"Slot {account.slot}: {account.email or 'unknown e-mail'} connected.")
    if account.is_active:
        print("It is the active account.")
    return 0


async def _cmd_usage(registry: AccountRegistry, args: argparse.Namespace) -> int:
    if args.slot is None:
        accounts = await registry.refresh_all_usage()
    else:
        accounts = [await registry.refresh_usage(args.slot)]
    if args.json:
        print(Account.list_to_json(accounts, indent=2))
    else:
        print_accounts(accounts)
    return 0


async def _cmd_remove(registry: AccountRegistry, args: argparse.Namespace) -> int:
    account = registry.get(args.slot)
    if not args.yes:
        if not sys.stdin.isatty():
            print("Refusing to remove without -y in a non-interactive session.", file=sys.stderr)
            return 1
        answer = input(f"Remove slot {args.slot} ({account.email or 'unknown'})? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("Aborted.")
            return 1
    if args.revoke:
        result = await registry.sign_out(args.slot)
        if result.revoke_confirmed:
            note = "signed out on Google's side (confirmed: the session is rejected now)"
        elif result.revoke_requested:
            note = "sign-out requested, but could not confirm that Google dropped the session"
        else:
            note = "could not reach Google to sign out; the session was only forgotten locally"
        print(f"Slot {args.slot} removed; {note}.")
    else:
        registry.remove(args.slot)
        print(f"Slot {args.slot} removed (the session is only forgotten locally; use --revoke to sign out).")
    return 0


def _cmd_doctor() -> int:
    import importlib.util
    import os
    import shutil

    from .login.common import ENV_BROWSER_PATH

    ok = True
    ok_contract, missing = usage_refresh_contract()
    print(f"gemini-webapi usage-refresh contract: {'ok' if ok_contract else 'MISSING ' + ', '.join(missing) + ' (slow fallback is used)'}")
    has_pw = importlib.util.find_spec("playwright") is not None
    print(f"playwright (login extra): {'installed' if has_pw else 'NOT installed (pip install gemini-vault[login])'}")
    browser = os.environ.get(ENV_BROWSER_PATH) or next(
        (p for p in (shutil.which(n) for n in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")) if p), None
    )
    print(f"system browser: {browser or 'none found (use `install-browser --with-deps` or set ' + ENV_BROWSER_PATH + ')'}")
    print(f"GEMINI_MANAGER_KEY: {'set' if os.environ.get('GEMINI_MANAGER_KEY') else 'NOT set'}")
    return 0 if ok else 1


def dispatch(args: argparse.Namespace) -> int | None:
    """Виконати спільну команду; None — команда не спільна (її має обробити фронтенд)."""
    if args.command not in COMMON_COMMANDS:
        return None
    if args.command == "keygen":
        print(generate_key())
        print("Export it as GEMINI_MANAGER_KEY (keep it safe: it opens your vault).", file=sys.stderr)
        return 0
    if args.command == "install-browser":
        cmd = [sys.executable, "-m", "playwright", "install"]
        if getattr(args, "with_deps", False):
            cmd.append("--with-deps")
        return subprocess.call(cmd + ["chromium"])
    if args.command == "doctor":
        return _cmd_doctor()

    registry = AccountRegistry()
    if args.command in {"login", "login-console"}:
        return asyncio.run(_cmd_login(registry, args))
    if args.command == "list":
        accounts = registry.accounts()
        print(Account.list_to_json(accounts, indent=2)) if args.json else print_accounts(accounts)
        return 0
    if args.command == "use":
        print(f"Active: {registry.set_active(args.slot)}")
        return 0
    if args.command == "remove":
        return asyncio.run(_cmd_remove(registry, args))
    if args.command == "proxy":
        registry.set_proxy(args.slot, args.url)
        print(f"Slot {args.slot}: proxy {'set' if args.url else 'cleared'}.")
        return 0
    if args.command == "usage":
        return asyncio.run(_cmd_usage(registry, args))
    return 2  # pragma: no cover


def run_main(parser: argparse.ArgumentParser, extra: "callable | None" = None, argv: Sequence[str] | None = None) -> int:
    """Спільний ``main``: розбір аргументів, обробка помилок, делегування фронтенду."""
    args = parser.parse_args(argv)
    try:
        from gemini_webapi import set_log_level

        set_log_level("WARNING")
    except Exception:  # noqa: BLE001 - налаштування логів не обов'язкове
        pass
    try:
        code = dispatch(args)
        if code is None and extra is not None:
            code = extra(args)
        return 2 if code is None else code
    except GeminiManagerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - мережа/бібліотека: коротке повідомлення замість traceback
        if os.environ.get("GEMINI_VAULT_DEBUG"):
            raise
        print(f"Error: {type(exc).__name__}: {exc}\n(set GEMINI_VAULT_DEBUG=1 for the traceback)", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser("gemini-vault", "Encrypted multi-account vault for Gemini. Needs GEMINI_MANAGER_KEY.")
    return run_main(parser, None, argv)


if __name__ == "__main__":
    sys.exit(main())

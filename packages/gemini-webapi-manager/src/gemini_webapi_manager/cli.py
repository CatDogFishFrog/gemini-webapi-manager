"""Command line interface: ``gemini-webapi-manager`` / ``python -m gemini_webapi_manager``."""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from collections.abc import Sequence

from .crypto import generate_key
from .errors import GeminiManagerError
from .manager import AccountManager
from .models import Account


def _pct(value: float | int | None) -> str:
    return "-" if value is None else f"{value:.0f}%"


def _print_accounts(accounts: list[Account]) -> None:
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


def _status(message: str) -> None:
    print(message, file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gemini-webapi-manager",
        description="Manage Google accounts for gemini-webapi. Needs GEMINI_MANAGER_KEY in the environment.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("keygen", help="print a new random encryption key")
    sub.add_parser("install-browser", help="download Playwright's Chromium (if you have no Chrome/Edge)")

    login = sub.add_parser("login", help="sign in through a browser window into a slot")
    login.add_argument("slot", type=int)
    login.add_argument("--timeout", type=float, default=300.0, help="seconds to wait (default 300)")
    login.add_argument("--browser", choices=["auto", "chrome", "msedge", "chromium"], default="auto")
    login.add_argument("--headless", action="store_true", help="no window (Google usually blocks this)")
    login.add_argument("--proxy", help="proxy URL for the browser and later Gemini requests")
    login.add_argument("--no-verify", action="store_true", help="skip the Gemini check before saving")

    lst = sub.add_parser("list", help="show connected accounts (last known usage)")
    lst.add_argument("--json", action="store_true")

    use = sub.add_parser("use", help="make a slot the active account")
    use.add_argument("slot", type=int)

    rm = sub.add_parser("remove", help="remove the account from a slot")
    rm.add_argument("slot", type=int)
    rm.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")

    usage = sub.add_parser("usage", help="refresh usage from Gemini and show it")
    usage.add_argument("slot", type=int, nargs="?", help="default: all accounts")
    usage.add_argument("--json", action="store_true")

    proxy = sub.add_parser("proxy", help="set or clear the proxy of a slot")
    proxy.add_argument("slot", type=int)
    proxy.add_argument("url", nargs="?", help="omit to clear")

    ask = sub.add_parser("ask", help="send a prompt using the active (or given) account")
    ask.add_argument("prompt", nargs="+")
    ask.add_argument("--slot", type=int)
    ask.add_argument("--model")
    return parser


async def _cmd_login(manager: AccountManager, args: argparse.Namespace) -> int:
    account = await manager.login(
        args.slot,
        timeout=args.timeout,
        headless=args.headless,
        browser=args.browser,
        proxy=args.proxy,
        verify=not args.no_verify,
        on_status=_status,
    )
    print(f"Slot {account.slot}: {account.email or 'unknown e-mail'} connected.")
    if account.is_active:
        print("It is the active account.")
    return 0


async def _cmd_usage(manager: AccountManager, args: argparse.Namespace) -> int:
    if args.slot is None:
        accounts = await manager.refresh_all_usage()
    else:
        accounts = [await manager.refresh_usage(args.slot)]
    if args.json:
        print(Account.list_to_json(accounts, indent=2))
    else:
        _print_accounts(accounts)
    return 0


async def _cmd_ask(manager: AccountManager, args: argparse.Namespace) -> int:
    kwargs = {"model": args.model} if args.model else {}
    async with manager.client(args.slot) as client:
        output = await client.generate_content(" ".join(args.prompt), **kwargs)
    print(output.text)
    return 0


def _run(args: argparse.Namespace) -> int:
    if args.command == "keygen":
        print(generate_key())
        print("Export it as GEMINI_MANAGER_KEY (keep it safe: it opens your vault).", file=sys.stderr)
        return 0
    if args.command == "install-browser":
        return subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"])

    manager = AccountManager()
    if args.command == "login":
        return asyncio.run(_cmd_login(manager, args))
    if args.command == "list":
        accounts = manager.accounts()
        print(Account.list_to_json(accounts, indent=2)) if args.json else _print_accounts(accounts)
        return 0
    if args.command == "use":
        print(f"Active: {manager.set_active(args.slot)}")
        return 0
    if args.command == "remove":
        account = manager.get(args.slot)
        if not args.yes:
            if not sys.stdin.isatty():
                print("Refusing to remove without -y in a non-interactive session.", file=sys.stderr)
                return 1
            answer = input(f"Remove slot {args.slot} ({account.email or 'unknown'})? [y/N] ")
            if answer.strip().lower() not in {"y", "yes"}:
                print("Aborted.")
                return 1
        manager.remove(args.slot)
        print(f"Slot {args.slot} removed (the session is only forgotten locally).")
        return 0
    if args.command == "proxy":
        manager.set_proxy(args.slot, args.url)
        print(f"Slot {args.slot}: proxy {'set' if args.url else 'cleared'}.")
        return 0
    if args.command == "usage":
        return asyncio.run(_cmd_usage(manager, args))
    if args.command == "ask":
        return asyncio.run(_cmd_ask(manager, args))
    return 2  # pragma: no cover - argparse enforces a valid command


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from gemini_webapi import set_log_level

        set_log_level("WARNING")
    except Exception:  # noqa: BLE001 - logging tweak is optional
        pass
    try:
        return _run(args)
    except GeminiManagerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())

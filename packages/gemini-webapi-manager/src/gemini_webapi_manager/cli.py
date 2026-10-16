"""``gemini-webapi-manager``: спільні команди ядра + ``ask``."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from gemini_vault.cli import build_parser as _core_parser
from gemini_vault.cli import run_main

from .manager import AccountManager


def build_parser() -> argparse.ArgumentParser:
    parser = _core_parser(
        "gemini-webapi-manager",
        "Manage Google accounts for gemini-webapi. Needs GEMINI_MANAGER_KEY in the environment.",
    )
    sub = parser.subcommands
    ask = sub.add_parser("ask", help="send a prompt using the active (or given) account")
    ask.add_argument("prompt", nargs="+")
    ask.add_argument("--slot", type=int)
    ask.add_argument("--model")
    return parser


async def _cmd_ask(args: argparse.Namespace) -> int:
    manager = AccountManager()
    kwargs = {"model": args.model} if args.model else {}
    async with manager.client(args.slot) as client:
        output = await client.generate_content(" ".join(args.prompt), **kwargs)
    print(output.text)
    return 0


def _extra(args: argparse.Namespace) -> int | None:
    if args.command == "ask":
        return asyncio.run(_cmd_ask(args))
    return None


def main(argv: Sequence[str] | None = None) -> int:
    return run_main(build_parser(), _extra, argv)


if __name__ == "__main__":
    sys.exit(main())

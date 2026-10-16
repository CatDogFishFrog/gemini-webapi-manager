"""``gemini-web2api-manager``: шлюз OpenAI-API з автоперемиканням акаунтів + спільні команди ядра."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from gemini_vault import selection
from gemini_vault.cli import build_parser as _core_parser
from gemini_vault.cli import print_accounts, run_main
from gemini_vault.errors import GeminiManagerError
from gemini_vault.registry import AccountRegistry

from . import upstream
from .config import STRATEGIES, GatewayConfig
from .gateway import build_server
from .keeper import SessionKeeper
from .pool import AccountPool


def build_parser() -> argparse.ArgumentParser:
    parser = _core_parser(
        "gemini-web2api-manager",
        "OpenAI-compatible gateway for Gemini with automatic account failover. Needs GEMINI_MANAGER_KEY.",
    )
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))

    serve = sub.add_parser("serve", help="run the gateway")
    serve.add_argument("--config", help="gemini-web2api config.json (+ optional \"manager\" block)")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--api-key", action="append", dest="api_keys", help="accepted Bearer key (repeatable)")
    serve.add_argument("--strategy", choices=STRATEGIES, help="sticky (default) or max-remaining")
    serve.add_argument("--slots", help="comma separated slots to use, e.g. 1,2,3 (default: all)")
    serve.add_argument("--no-persist-active", action="store_true", help="do not write the current account into the vault")
    serve.add_argument("--upstream-path", help="folder with a gemini-web2api checkout")
    serve.add_argument("-v", "--verbose", action="store_true")

    st = sub.add_parser("status", help="show limits per account and which one would be used next (offline)")
    st.add_argument("--config")

    fu = sub.add_parser("fetch-upstream", help="download the tested gemini-web2api sources")
    fu.add_argument("--ref", default=upstream.PINNED_REF, help="git ref (default: the tested commit)")
    return parser


def _serve(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    cfg = GatewayConfig.load(args.config)
    if args.strategy:
        cfg.strategy = args.strategy
    if args.slots:
        cfg.slots = [int(s) for s in args.slots.split(",") if s.strip()]
    if args.no_persist_active:
        cfg.persist_active = False

    up = upstream.load(args.upstream_path)
    up.config.CONFIG.update(cfg.upstream)
    up.config.CONFIG.update({"cookie_file": None, "xsrf_token": None, "auth_user": None})
    if args.host:
        up.config.CONFIG["host"] = args.host
    if args.port:
        up.config.CONFIG["port"] = args.port
    if args.api_keys:
        up.config.CONFIG["api_keys"] = args.api_keys

    registry = AccountRegistry()
    holder: dict[str, SessionKeeper] = {}

    def on_switch(previous: int | None, new: int, reason: str) -> None:
        if cfg.persist_active:
            try:
                registry.set_active(new)
            except GeminiManagerError as exc:
                logging.getLogger(__name__).warning("Could not persist the active slot: %s", exc)

    def on_limit(slot: int) -> None:
        if "k" in holder:
            holder["k"].request_refresh(slot)

    pool = AccountPool(
        strategy=cfg.strategy, default_cooldown=cfg.default_cooldown, rate_cooldown=cfg.rate_cooldown,
        threshold=cfg.switch_threshold, on_switch=on_switch, on_limit=on_limit,
    )
    keeper = SessionKeeper(registry, pool, slots=cfg.slots, refresh_interval=cfg.refresh_interval)
    holder["k"] = keeper
    print("Starting account sessions...", file=sys.stderr)
    keeper.start()
    if not pool.has_usable():
        keeper.stop()
        raise GeminiManagerError(
            "No usable accounts: the vault is empty or every session was rejected. "
            "Add one with `login-console <slot>` (or copy a vault.bin) and run `status`."
        )
    server = build_server(up, cfg, pool, keeper)
    config = up.config.CONFIG
    snapshot = pool.status()
    print(f"gemini-web2api {up.version} + manager | strategy: {cfg.strategy}", file=sys.stderr)
    print(f"  Base URL: http://{config['host']}:{config['port']}/v1   (pool status: /_pool/status)", file=sys.stderr)
    for acc in snapshot["accounts"]:
        mark = "*" if acc["current"] else " "
        state = "ok" if acc["usable"] else "UNUSABLE"
        print(f"  slot {acc['slot']}{mark} {acc['email'] or '?'} [{state}] used={acc['used_percent']}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
    finally:
        server.server_close()
        keeper.stop()
    return 0


def _status(args: argparse.Namespace) -> int:
    cfg = GatewayConfig.load(args.config)
    registry = AccountRegistry()
    accounts = registry.accounts()
    print_accounts(accounts)
    candidates = [
        selection.Candidate(a.slot, a.usage, a.status, a.last_used_at, None, True) for a in accounts
    ]
    print()
    for family in ("pro", "flash"):
        ranked = selection.rank(candidates, family=family, threshold=cfg.switch_threshold)
        order = " > ".join(
            f"#{c.slot} ({selection.remaining_percent(c.usage, family):.0f}% left)"
            if selection.remaining_percent(c.usage, family) is not None else f"#{c.slot} (?)"
            for c in ranked
        )
        print(f"{family:>5}: {order or 'nobody can serve right now'}")
    return 0


def _fetch(args: argparse.Namespace) -> int:
    root = upstream.fetch(args.ref)
    print(f"gemini-web2api sources: {root}")
    return 0


def _extra(args: argparse.Namespace) -> int | None:
    return {"serve": _serve, "status": _status, "fetch-upstream": _fetch}.get(args.command, lambda a: None)(args)


def main(argv: Sequence[str] | None = None) -> int:
    return run_main(build_parser(), _extra, argv)


if __name__ == "__main__":
    sys.exit(main())

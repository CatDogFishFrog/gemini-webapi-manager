#!/usr/bin/env python3
"""Живий димовий тест шлюзу з РЕАЛЬНИМ vault і РЕАЛЬНИМ Gemini (потрібна мережа).

Що перевіряє, по кроках (кожен друкує PASS/FAIL, секрети не друкуються):
  1. vault читається, акаунти є;
  2. шлюз стартує, у пулі є придатні акаунти;
  3. кожен акаунт по черзі відповідає на запит (перевіряє cookie + xsrf + bl із реальними даними);
  4. стрімінг працює;
  5. перемикання: слот, що обслуговує запити, блокується вручну — запит має піти на інший акаунт.

Запуск (GEMINI_MANAGER_KEY і GEMINI_MANAGER_HOME — як зазвичай):
    python tools/live_smoke.py [--port 18080] [--keep-log]
Результат і лог шлюзу зберігаються в ./live-smoke-report/ — це можна віддавати AI-агенту.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


def http(port: int, path: str, payload=None, method=None, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def mask(text: str) -> str:
    return re.sub(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", "<email>", text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--model", default="gemini-3.6-flash")
    args = parser.parse_args()

    report = Path("live-smoke-report")
    report.mkdir(exist_ok=True)
    log_path = report / "gateway.log"
    env = dict(os.environ)
    with log_path.open("w") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "gemini_web2api_manager", "serve", "--port", str(args.port), "--no-persist-active", "-v"],
            stdout=log, stderr=subprocess.STDOUT, env=env,
        )
        try:
            for _ in range(180):
                time.sleep(1)
                if proc.poll() is not None:
                    break
                try:
                    status, body = http(args.port, "/_pool/status", timeout=3)
                    if status == 200:
                        break
                except OSError:
                    continue
            else:
                status = 0
            if not check("gateway started", proc.poll() is None and status == 200, f"see {log_path}"):
                return 1
            pool = json.loads(body)
            usable = [a for a in pool["accounts"] if a["usable"]]
            check("pool has usable accounts", bool(usable), f"{len(usable)}/{len(pool['accounts'])}")
            for acc in pool["accounts"]:
                if not acc["usable"]:
                    check(f"slot {acc['slot']} usable", False, acc.get("last_error") or acc.get("status") or "")

            chat = {"model": args.model, "messages": [{"role": "user", "content": "Reply with exactly one word: pong"}]}
            for acc in usable:
                http(args.port, f"/_pool/use/{acc['slot']}", method="POST")
                status, body = http(args.port, "/v1/chat/completions", chat)
                text = ""
                try:
                    text = json.loads(body)["choices"][0]["message"]["content"] or ""
                except (ValueError, KeyError, IndexError):
                    pass
                check(f"slot {acc['slot']} answers", status == 200 and bool(text.strip()), mask(f"HTTP {status}: {(text or body)[:80]!r}"))

            if usable:
                status, body = http(args.port, "/v1/chat/completions", {**chat, "stream": True})
                check("streaming works", status == 200 and "[DONE]" in body, f"HTTP {status}")

            if len(usable) >= 2:
                first = usable[0]["slot"]
                http(args.port, f"/_pool/use/{first}", method="POST")
                http(args.port, f"/_pool/block/{first}?seconds=300", method="POST")
                status, body = http(args.port, "/v1/chat/completions", chat)
                after = json.loads(http(args.port, "/_pool/status")[1])
                check("failover: blocked slot is skipped", status == 200 and after["current"] != first,
                      f"HTTP {status}, current slot now {after['current']}")
                http(args.port, f"/_pool/unblock/{first}", method="POST")
            else:
                check("failover (needs 2+ accounts)", True, "skipped: only one usable account")

            (report / "pool-status.json").write_text(mask(http(args.port, "/_pool/status")[1]), encoding="utf-8")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
    summary = "\n".join(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}" for name, ok, detail in results)
    (report / "summary.txt").write_text(mask(summary) + "\n", encoding="utf-8")
    print(f"\nReport: {report.resolve()}  (summary.txt, pool-status.json, gateway.log)")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())

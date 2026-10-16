"""Інтеграція: справжній обробник gemini-web2api + пул + фейковий сервер «Gemini».

Фейк віддає ліміт (`BardErrorInfo [1037]`) для вибраних акаунтів; перевіряємо, що запит
прозоро йде на акаунт із найбільшим залишком, що стрім теж перемикається до першого токена,
що вичерпання всіх акаунтів дає 429 + Retry-After, і що секрети не витікають.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest

from gemini_vault.usage import UsageSnapshot, UsageWindow
from gemini_web2api_manager import scope, upstream
from gemini_web2api_manager.config import GatewayConfig
from gemini_web2api_manager.gateway import build_server
from gemini_web2api_manager.pool import AccountPool
from gemini_web2api_manager.scope import Credentials

up_available = True
try:
    UP = upstream.load()
except upstream.UpstreamError:  # pragma: no cover - апстрім не знайдено
    up_available = False

pytestmark = pytest.mark.skipif(not up_available, reason="gemini-web2api sources not found (fetch-upstream / GEMINI_WEB2API_PATH)")


def _line(text: str) -> str:
    inner = [None] * 5
    inner[0] = "x" * 200
    inner[4] = [["rc_1", [text]]]
    return json.dumps([["wrb.fr", None, json.dumps(inner)]])


class FakeGemini:
    """Сервер, що імітує StreamGenerate: поведінка залежить від cookie SID."""

    def __init__(self):
        self.modes: dict[str, str] = {}
        self.log: list[dict] = []
        self.lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
                fields = parse_qs(body)
                cookie = self.headers.get("Cookie", "")
                sid = next((p.split("=", 1)[1] for p in cookie.split("; ") if p.startswith("SID=")), "anon")
                at = (fields.get("at") or [None])[0]
                with outer.lock:
                    outer.log.append({"sid": sid, "at": at, "authorization": bool(self.headers.get("Authorization"))})
                mode = outer.modes.get(sid, "ok")
                if mode == "limit":
                    return self._reply(200, "garbage\n" + 'e,"BardErrorInfo [1037]"')
                if mode == "auth":
                    return self._reply(403, "forbidden")
                if mode == "boom":
                    return self._reply(500, "internal")
                if at != f"xsrf-{sid}":
                    return self._reply(400, "bad xsrf")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for text in (f"A:{sid}", f"A:{sid} done"):
                    data = (_line(text) + "\n").encode()
                    self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                    self.wfile.flush()
                    time.sleep(0.05)
                self.wfile.write(b"0\r\n\r\n")

            def _reply(self, status, text):
                raw = text.encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def sids(self):
        return [e["sid"] for e in self.log]


def creds(n):
    return Credentials(slot=n, cookie_header=f"SID=acct{n}; HSID=h{n}", sapisid=f"sap{n}", xsrf_token=f"xsrf-acct{n}", build_label="bl-test")


def snap(pct):
    return UsageSnapshot(five_hour=UsageWindow(percent=pct, reset_at=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()))


@pytest.fixture
def fake(monkeypatch):
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    f = FakeGemini()
    monkeypatch.setattr(UP.gemini, "_get_url", lambda: f"http://127.0.0.1:{f.port}/_/BardChatUi/data/StreamGenerate?rt=c", raising=True)
    saved = dict(UP.config.CONFIG)
    UP.config.CONFIG.update({"host": "127.0.0.1", "port": 0, "retry_attempts": 2, "retry_delay_sec": 0,
                             "request_timeout_sec": 20, "log_requests": False, "api_keys": [], "default_model": "gemini-3.6-flash"})
    yield f
    f.httpd.shutdown()
    UP.config.CONFIG.clear()
    UP.config.CONFIG.update(saved)


@pytest.fixture
def gateway(fake):
    started = []

    def start(usages=None, *, strategy="sticky", cooldown=1800.0, cfg=None):
        pool = AccountPool(strategy=strategy, default_cooldown=cooldown)
        for slot, pct in (usages if usages is not None else {1: 10, 2: 40, 3: 80}).items():
            pool.update_slot(slot, email=f"u{slot}@x.com", credentials=creds(slot), usage=snap(pct), status="AVAILABLE")
        server = build_server(UP, cfg or GatewayConfig(), pool)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append(server)
        return pool, server.server_address[1]

    yield start
    for s in started:
        s.shutdown()
        s.server_close()


def call(port, path="/v1/chat/completions", payload=None, headers=None, method="POST"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    body = json.dumps(payload) if payload is not None else None
    conn.request(method, path, body=body, headers={"Content-Type": "application/json", **(headers or {})})
    resp = conn.getresponse()
    data = resp.read().decode()
    return resp.status, dict(resp.getheaders()), data


def chat(port, text="hello", **extra):
    return call(port, payload={"model": "gemini-3.6-flash", "messages": [{"role": "user", "content": text}], **extra})


def content(data):
    return json.loads(data)["choices"][0]["message"]["content"]


def sse_text(data):
    text = ""
    for block in data.strip().split("\n\n"):
        if block.startswith("data: ") and block != "data: [DONE]":
            text += json.loads(block[6:])["choices"][0]["delta"].get("content", "") or ""
    return text


# --------------------------------------------------------------------------------- тести
def test_uses_best_account_with_its_own_credentials(fake, gateway):
    pool, port = gateway()
    status, _, data = chat(port)
    assert status == 200 and content(data) == "A:acct1 done"  # найбільший залишок — слот 1
    entry = fake.log[-1]
    assert entry == {"sid": "acct1", "at": "xsrf-acct1", "authorization": True}


def test_limit_fails_over_transparently_to_most_remaining_and_sticks(fake, gateway):
    pool, port = gateway()
    fake.modes["acct1"] = "limit"
    status, _, data = chat(port)
    assert status == 200 and content(data) == "A:acct2 done"  # 2-й: 60% залишку проти 20% у 3-го
    assert "acct1" in fake.sids()
    fake.log.clear()
    assert content(chat(port)[2]) == "A:acct2 done"
    assert set(fake.sids()) == {"acct2"}  # 1-й у паузі, більше його не чіпаємо
    snapshot = pool.status()
    assert snapshot["current"] == 2 and snapshot["accounts"][0]["limit_hits"] == 1
    assert "flash" in snapshot["accounts"][0]["blocked"]


def test_chain_to_third_then_all_exhausted_gives_429_with_retry_after(fake, gateway):
    pool, port = gateway()
    fake.modes.update({"acct1": "limit", "acct2": "limit"})
    assert content(chat(port)[2]) == "A:acct3 done"
    fake.modes["acct3"] = "limit"
    status, headers, data = chat(port)
    assert status == 429 and json.loads(data)["error"]["code"] == "accounts_exhausted"
    assert int(headers["Retry-After"]) > 0
    again, _, _ = chat(port)  # усі в паузі: до Google навіть не ходимо
    n = len(fake.log)
    assert again == 429 and len(fake.log) == n


def test_account_comes_back_after_cooldown(fake, gateway):
    pool, port = gateway(cooldown=1.0)
    fake.modes.update({"acct1": "limit", "acct2": "limit", "acct3": "limit"})
    assert chat(port)[0] == 429
    fake.modes.clear()
    time.sleep(1.3)
    status, _, data = chat(port)
    assert status == 200 and content(data) == "A:acct3 done"  # sticky: лишаємось на поточному, коли він знову придатний


def test_max_remaining_returns_to_the_best_account_after_cooldown(fake, gateway):
    pool, port = gateway(strategy="max-remaining", cooldown=1.0)
    fake.modes["acct1"] = "limit"
    assert content(chat(port)[2]) == "A:acct2 done"
    fake.modes.clear()
    time.sleep(1.3)
    assert content(chat(port)[2]) == "A:acct1 done"  # найбільший залишок знову у 1-го


def test_streaming_fails_over_before_first_token(fake, gateway):
    pool, port = gateway()
    fake.modes["acct1"] = "limit"
    status, headers, data = chat(port, stream=True)
    assert status == 200 and headers["Content-Type"] == "text/event-stream"
    assert sse_text(data) == "A:acct2 done" and data.strip().endswith("data: [DONE]")
    assert data.count("chat.completion.chunk") >= 3  # роль + дельти + фініш: один цілий стрім, без залишків невдалої спроби


def test_auth_error_fails_over_without_marking_quota(fake, gateway):
    pool, port = gateway()
    fake.modes["acct1"] = "auth"
    assert content(chat(port)[2]) == "A:acct2 done"
    assert pool.status()["accounts"][0]["limit_hits"] == 0


def test_non_recoverable_error_is_not_masked_by_failover(fake, gateway):
    pool, port = gateway()
    fake.modes["acct1"] = "boom"
    status, _, data = chat(port)
    assert status == 502 and "upstream error" in data
    assert set(fake.sids()) == {"acct1"}  # інші акаунти не задіяно
    assert pool.status()["accounts"][0]["blocked"] == {}


def test_concurrent_requests_survive_a_limited_account(fake, gateway):
    pool, port = gateway()
    fake.modes["acct1"] = "limit"
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda i: chat(port, f"q{i}"), range(8)))
    assert all(status == 200 for status, _, _ in results)
    assert all(content(data).startswith("A:acct2") for _, _, data in results)


def test_per_family_limit_does_not_block_other_models(fake, gateway):
    pool, port = gateway()
    fake.modes["acct1"] = "limit"
    status, _, data = call(port, payload={"model": "gemini-3.1-pro", "messages": [{"role": "user", "content": "x"}]})
    assert status == 200 and content(data) == "A:acct2 done"
    blocked = pool.status()["accounts"][0]["blocked"]
    assert set(blocked) == {"pro"}
    fake.modes.clear()
    fake.log.clear()
    assert content(chat(port)[2]) == "A:acct2 done"  # sticky не повертається сам
    assert content(call(port, payload={"model": "gemini-3.5-flash", "messages": [{"role": "user", "content": "y"}]})[2]) == "A:acct2 done"


def test_api_key_protects_models_and_pool_status(fake, gateway):
    UP.config.CONFIG["api_keys"] = ["sk-test"]
    pool, port = gateway()
    assert chat(port)[0] == 401
    assert call(port, "/_pool/status", method="GET")[0] == 403
    ok = {"Authorization": "Bearer sk-test"}
    assert call(port, "/_pool/status", headers=ok, method="GET")[0] == 200
    assert call(port, payload={"model": "gemini-3.6-flash", "messages": [{"role": "user", "content": "k"}]}, headers=ok)[0] == 200


def test_pool_status_and_manual_switch_never_leak_secrets(fake, gateway):
    pool, port = gateway()
    status, _, data = call(port, "/_pool/status", method="GET")
    assert status == 200
    for secret in ("HSID", "xsrf-acct", "SID=", "sap1"):
        assert secret not in data
    assert json.loads(data)["accounts"][0]["email"] == "u1@x.com"
    status, _, data = call(port, "/_pool/use/3")
    assert status == 200 and json.loads(data)["current"] == 3
    assert content(chat(port)[2]) == "A:acct3 done"
    assert call(port, "/_pool/use/9")[0] == 404


def test_models_endpoint_still_served_by_upstream(fake, gateway):
    pool, port = gateway()
    status, _, data = call(port, "/v1/models", method="GET")
    assert status == 200 and any(m["id"] == "gemini-3.6-flash" for m in json.loads(data)["data"])


def test_no_usable_account_is_503(fake, gateway):
    pool, port = gateway({})
    status, _, data = chat(port)
    assert status == 503 and json.loads(data)["error"]["code"] == "no_accounts"


def test_scoped_config_is_per_thread():
    base = UP.config.CONFIG
    scoped = UP.gemini.CONFIG
    assert isinstance(scoped, scope.ScopedConfig)
    seen = {}

    def worker(n):
        with scope.use(creds(n)):
            time.sleep(0.05)
            seen[n] = (scoped["xsrf_token"], scoped["gemini_bl"], scoped.get("auth_user"))

    threads = [threading.Thread(target=worker, args=(n,)) for n in (1, 2, 3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert seen == {n: (f"xsrf-acct{n}", "bl-test", None) for n in (1, 2, 3)}
    assert scoped["xsrf_token"] == base["xsrf_token"]  # поза запитом діє базовий CONFIG


def test_manual_block_forces_failover_and_unblock_restores(fake, gateway):
    pool, port = gateway(strategy="max-remaining")
    assert content(chat(port)[2]) == "A:acct1 done"
    assert call(port, "/_pool/block/1?seconds=600")[0] == 200
    assert content(chat(port)[2]) == "A:acct2 done"  # слот 1 виведено з ротації вручну
    assert call(port, "/_pool/unblock/1")[0] == 200
    assert content(chat(port)[2]) == "A:acct1 done"
    assert call(port, "/_pool/block/9")[0] == 404

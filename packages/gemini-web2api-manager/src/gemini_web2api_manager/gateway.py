"""Шлюз: незмінений gemini-web2api + пул акаунтів з автоматичним перемиканням.

Як це працює:

1. ``install`` підміняє в модулях апстріму кілька «швів» (читання cookie, CONFIG, httpx-клієнт,
   токени сторінки, ``generate``/``generate_stream``), щоб кожен запит ішов від імені свого акаунта.
2. ``PooledHandler.do_POST`` на кожен запит бере акаунт у пулі й виконує звичайний обробник апстріму,
   але **відповідь буферизується**, доки не з'явиться перший токен (стрим) або обробник не завершиться.
3. Якщо до того апстрім повідомив про ліміт/автентифікацію (див. errors.classify), буфер скидається,
   акаунт позначається в пулі, а запит повторюється на наступному акаунті — клієнт нічого не помічає.
   Якщо перший токен уже відправлено, перемикання неможливе, і помилка йде як є.
"""

from __future__ import annotations

import io
import json
import logging
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import scope
from .config import GatewayConfig
from .errors import Failure, classify
from .pool import AccountPool, Lease
from .upstream import Upstream

log = logging.getLogger(__name__)


class _Failover(BaseException):
    """Службовий сигнал «спробуй інший акаунт».

    Навмисно ``BaseException``: обробники апстріму ловлять ``Exception`` і перетворюють його
    на HTTP 502 — а нам потрібно, щоб сигнал дійшов до ``PooledHandler.do_POST``.
    """

    def __init__(self, failure: Failure, message: str) -> None:
        super().__init__(message)
        self.failure = failure
        self.message = message


class HoldWriter:
    """Тримає записане у пам'яті, доки не буде ``commit`` (тоді все йде в сокет)."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw
        self._chunks: list[bytes] = []
        self.committed = False

    def write(self, data: bytes) -> int:
        if self.committed:
            return self._raw.write(data)
        self._chunks.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        if self.committed:
            self._raw.flush()

    def commit(self) -> None:
        if self.committed:
            return
        self.committed = True
        for chunk in self._chunks:
            self._raw.write(chunk)
        self._chunks.clear()
        self._raw.flush()

    def discard(self) -> None:
        self._chunks.clear()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


# ---------------------------------------------------------------------------- підміна швів
def install(up: Upstream, cfg: GatewayConfig) -> scope.ScopedConfig:
    """Підготувати апстрім до роботи з кількома акаунтами. Безпечно викликати повторно."""
    base = up.config.CONFIG
    existing = getattr(up.gemini, "CONFIG", None)
    if isinstance(existing, scope.ScopedConfig):
        return existing
    scoped = scope.ScopedConfig(base)
    for module in (up.gemini, up.multimodal, up.server):
        if getattr(module, "CONFIG", None) is base:
            module.CONFIG = scoped

    # 1) cookie запиту
    original_load_cookie = up.gemini.load_cookie

    def load_cookie() -> tuple[str, str | None]:
        creds = scope.current()
        if creds is not None:
            return creds.cookie_header, creds.sapisid
        return original_load_cookie()

    up.gemini.load_cookie = load_cookie
    up.multimodal.load_cookie = load_cookie  # multimodal імпортує ім'я напряму, тож підміняємо й там

    # 2) токени сторінки (push-id, pctx) прив'язані до акаунта
    original_tokens = up.multimodal._get_page_tokens
    token_cache: dict[int | None, tuple[float, dict]] = {}

    def cached_tokens() -> dict:
        creds = scope.current()
        key = creds.slot if creds else None
        stamp, tokens = token_cache.get(key, (0.0, {}))
        if time.time() - stamp > 600:
            tokens = original_tokens()
            token_cache[key] = (time.time(), tokens)
        return tokens

    up.multimodal._cached_page_tokens = cached_tokens

    # 3) httpx-клієнт на кожен проксі (апстрім створює один глобальний)
    clients: dict[str | None, Any] = {}

    def get_client() -> Any:
        if not up.gemini.HAS_HTTPX:
            return None
        proxy = scoped.get("proxy")
        client = clients.get(proxy)
        if client is None:
            httpx = up.gemini.httpx
            transport = httpx.HTTPTransport(proxy=proxy) if proxy else None
            client = httpx.Client(transport=transport, timeout=scoped["request_timeout_sec"], verify=True)
            clients[proxy] = client
        return client

    up.gemini._get_httpx_client = get_client

    # 4) генерація: відмови, що лікуються заміною акаунта, перетворюємо на сигнал _Failover
    original_generate = up.server.generate
    original_stream = up.server.generate_stream

    def generate(*args: Any, **kwargs: Any) -> Any:
        creds = scope.current()
        try:
            text = original_generate(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            failure = classify(exc) if creds is not None else None
            if failure is not None:
                raise _Failover(failure, str(exc)) from exc
            raise
        if creds is not None and cfg.failover_on_empty and not (text or "").strip():
            raise _Failover(Failure.RATE, "empty response")
        return text

    def generate_stream(*args: Any, **kwargs: Any) -> Any:
        creds = scope.current()
        started = False
        try:
            for chunk in original_stream(*args, **kwargs):
                if not started:
                    started = True
                    hold = scope.current_hold()
                    if hold is not None:
                        hold.commit()  # першу порцію віддано: далі переключатися вже пізно
                yield chunk
        except Exception as exc:  # noqa: BLE001
            failure = classify(exc) if (creds is not None and not started) else None
            if failure is not None:
                raise _Failover(failure, str(exc)) from exc
            raise

    up.server.generate = generate
    up.server.generate_stream = generate_stream
    return scoped


# ---------------------------------------------------------------------------- обробник HTTP
def make_handler(up: Upstream, cfg: GatewayConfig, pool: AccountPool, keeper: Any = None) -> type:
    base_handler = up.server.GeminiHandler
    config = up.config.CONFIG

    class PooledHandler(base_handler):  # type: ignore[misc, valid-type]
        # ----- керування пулом (/_pool/...)
        def _admin_allowed(self) -> bool:
            if config.get("api_keys"):
                return self._authorized()
            return (self.client_address[0] if self.client_address else "") in ("127.0.0.1", "::1")

        def _admin(self, method: str) -> None:
            if not self._admin_allowed():
                self.send_json({"error": {"message": "forbidden"}}, 403)
                return
            path = self.path.split("?", 1)[0]
            if method == "GET" and path == "/_pool/status":
                self.send_json(pool.status())
            elif method == "POST" and path == "/_pool/refresh":
                if keeper is not None:
                    keeper.refresh_all()
                self.send_json({"ok": True})
            elif method == "POST" and (m := re.fullmatch(r"/_pool/use/(\d+)", path)):
                try:
                    pool.force_use(int(m.group(1)))
                except KeyError:
                    self.send_json({"error": {"message": "slot unavailable"}}, 404)
                    return
                self.send_json(pool.status())
            elif method == "POST" and (m := re.fullmatch(r"/_pool/(block|unblock)/(\d+)", path)):
                try:
                    if m.group(1) == "block":
                        seconds = float((parse_qs(urlparse(self.path).query).get("seconds") or ["600"])[0])
                        pool.block(int(m.group(2)), seconds)
                    else:
                        pool.unblock(int(m.group(2)))
                except (KeyError, ValueError):
                    self.send_json({"error": {"message": "slot unavailable or bad seconds"}}, 404)
                    return
                self.send_json(pool.status())
            else:
                self.send_json({"error": "not found"}, 404)

        def do_GET(self) -> None:
            if self.path.startswith("/_pool"):
                try:
                    self._admin("GET")
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            super().do_GET()

        # ----- запити до моделі
        def _family(self, body: bytes) -> str | None:
            match = re.search(r"/models/([^/:?]+)", self.path)
            name = match.group(1) if match else None
            if not name:
                try:
                    name = json.loads(body).get("model")
                except (ValueError, AttributeError):
                    name = None
            return cfg.family_for(name or config.get("default_model"))

        def _send_unavailable(self, family: str | None) -> None:
            retry = pool.retry_after(family)
            if not pool.has_usable():
                status, code, message = 503, "no_accounts", "No usable Gemini accounts (log in again)."
            else:
                status, code, message = 429, "accounts_exhausted", "Every Gemini account is out of quota right now."
            payload = {"error": {"message": message, "type": "rate_limit_exceeded" if status == 429 else "server_error", "code": code}}
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            if retry is not None:
                self.send_header("Retry-After", str(int(retry)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            try:
                if self.path.startswith("/_pool"):
                    self._admin("POST")
                    return
                if self.path.startswith("/v1") and not self._authorized():
                    self.send_json({"error": {"message": "invalid api key"}}, 401)
                    return
                body = self._read_request_body()
                family = self._family(body)
                self._dispatch(body, family)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _dispatch(self, body: bytes, family: str | None) -> None:
            real_wfile = self.wfile
            tried: set[int] = set()
            while True:
                lease: Lease | None = pool.acquire(family, exclude=tried)
                if lease is None:
                    self._send_unavailable(family)
                    return
                hold = HoldWriter(real_wfile)
                self.wfile = hold
                self.rfile = io.BytesIO(body)  # тіло вже прочитано: кожна спроба читає його заново
                for header in ("Transfer-Encoding", "Content-Length"):
                    del self.headers[header]
                self.headers["Content-Length"] = str(len(body))
                self._headers_buffer = []
                try:
                    with scope.use(lease.credentials, hold):
                        super().do_POST()
                    hold.commit()
                    pool.report_success(lease)
                    return
                except _Failover as signal:
                    hold.discard()
                    pool.report_failure(lease, signal.failure, signal.message)
                    tried.add(lease.slot)
                finally:
                    self.wfile = real_wfile

    return PooledHandler


def build_server(up: Upstream, cfg: GatewayConfig, pool: AccountPool, keeper: Any = None) -> Any:
    """Готовий HTTP-сервер (не запущений). Хост/порт беруться з CONFIG апстріму."""
    install(up, cfg)
    handler = make_handler(up, cfg, pool, keeper)
    config = up.config.CONFIG
    return up.server.ThreadedServer((config["host"], int(config["port"])), handler)

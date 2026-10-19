"""Коректна зупинка шлюзу (SIGTERM/SIGINT) і безпечні типові адреси прослуховування."""

from __future__ import annotations

import argparse
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gemini_web2api_manager.cli import apply_listen_settings, install_shutdown_handlers
from gemini_web2api_manager.config import GatewayConfig

pytestmark = pytest.mark.skipif(not hasattr(signal, "SIGTERM") or os.name == "nt", reason="POSIX-сигнали")


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_signal_stops_serve_forever_and_restores_handlers(sig):
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    before = signal.getsignal(sig)
    restore = install_shutdown_handlers(server)
    threading.Timer(0.3, lambda: os.kill(os.getpid(), sig)).start()
    finished = threading.Event()

    def serve() -> None:
        server.serve_forever()
        finished.set()

    # serve_forever у побічному потоці: сигнал приходить у головний, як у реальному процесі
    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        assert finished.wait(10), "serve_forever не завершився після сигналу"
    finally:
        restore()
        server.server_close()
    assert signal.getsignal(sig) == before


def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(host=kw.get("host"), port=kw.get("port"))


def test_listen_defaults_are_loopback_8080():
    config = {"host": "0.0.0.0", "port": 8081}  # так виглядають типові значення апстріму
    apply_listen_settings(config, GatewayConfig.from_dict({}), _args())
    assert (config["host"], config["port"]) == ("127.0.0.1", 8080)


def test_listen_settings_from_config_and_arguments():
    config = {"host": "0.0.0.0", "port": 8081}
    cfg = GatewayConfig.from_dict({"host": "10.0.0.5", "port": 9000})
    config.update(cfg.upstream)
    apply_listen_settings(config, cfg, _args())
    assert (config["host"], config["port"]) == ("10.0.0.5", 9000)
    apply_listen_settings(config, cfg, _args(host="0.0.0.0", port=8080))
    assert (config["host"], config["port"]) == ("0.0.0.0", 8080)


def test_real_serve_process_stops_cleanly_on_sigterm(tmp_path):
    """Справжній процес `serve` (з фейковим Gemini): SIGTERM → вихід із кодом 0 і повідомлення про збереження."""
    import socket
    import subprocess
    import sys
    import textwrap
    import time
    import urllib.request
    from pathlib import Path

    from conftest import KEY
    from gemini_vault import AccountRegistry
    from gemini_vault.models import StoredAccount

    home = tmp_path / "home"
    registry = AccountRegistry(home=home, key=KEY)
    with registry.store.transaction() as vault:
        vault.accounts[1] = StoredAccount(
            slot=1, cookies={"__Secure-1PSID": "psid-a", "__Secure-1PSIDTS": "ts"}, created_at="2030-01-01T00:00:00+00:00",
            email="a@x.com",
        )
        vault.active_slot = 1
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    tests_dir = str(Path(__file__).parent)
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {tests_dir!r})
        from conftest import FakeGemini
        import gemini_vault.sessions as sessions
        sessions.GeminiClient = FakeGemini
        from gemini_web2api_manager.cli import main
        sys.exit(main(["serve", "--port", "{port}"]))
    """)
    env = {**os.environ, "GEMINI_MANAGER_KEY": KEY, "GEMINI_MANAGER_HOME": str(home)}
    proc = subprocess.Popen([sys.executable, "-c", script], env=env, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 60
        ready = False
        while time.time() < deadline and proc.poll() is None:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/_pool/status", timeout=1).read()
                ready = True
                break
            except OSError:
                time.sleep(0.3)
        assert ready, "шлюз не запустився"
        proc.send_signal(signal.SIGTERM)
        _, stderr = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, stderr
    assert "Stopped." in stderr


def test_real_serve_without_accounts_uses_env_settings(tmp_path):
    """Контейнерний сценарій: порожнє сховище, ключі й дозвіл на порожній пул — зі змінних середовища."""
    import socket
    import subprocess
    import sys
    import textwrap
    import time
    import urllib.error
    import urllib.request
    from pathlib import Path

    from conftest import KEY

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).parent)!r})
        from gemini_web2api_manager.cli import main
        sys.exit(main(["serve", "--port", "{port}"]))
    """)
    env = {**os.environ, "GEMINI_MANAGER_KEY": KEY, "GEMINI_MANAGER_HOME": str(tmp_path / "empty"),
           "GEMINI_GATEWAY_ALLOW_EMPTY": "1", "GEMINI_GATEWAY_API_KEYS": "sk-one, sk-two"}
    proc = subprocess.Popen([sys.executable, "-c", script], env=env, stderr=subprocess.PIPE, text=True)

    def get(key):
        request = urllib.request.Request(f"http://127.0.0.1:{port}/_pool/status")
        if key:
            request.add_header("Authorization", f"Bearer {key}")
        return urllib.request.urlopen(request, timeout=2).status

    try:
        deadline = time.time() + 60
        while time.time() < deadline and proc.poll() is None:
            try:
                assert get("sk-two") == 200  # ключ із змінної GEMINI_GATEWAY_API_KEYS
                break
            except OSError:
                time.sleep(0.3)
        else:
            raise AssertionError("шлюз не запустився")
        with pytest.raises(urllib.error.HTTPError) as denied:
            get(None)
        assert denied.value.code in (401, 403)
        proc.send_signal(signal.SIGTERM)
        _, stderr = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, stderr
    assert "No usable accounts yet" in stderr


def test_serve_without_accounts_fails_unless_allowed(tmp_path):
    import subprocess
    import sys

    from conftest import KEY

    env = {**os.environ, "GEMINI_MANAGER_KEY": KEY, "GEMINI_MANAGER_HOME": str(tmp_path / "empty")}
    env.pop("GEMINI_GATEWAY_ALLOW_EMPTY", None)
    done = subprocess.run([sys.executable, "-m", "gemini_web2api_manager", "serve", "--port", "0"],
                          env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 1 and "No usable accounts" in done.stderr

"""Пошук (і за потреби завантаження) апстріму ``gemini-web2api`` та перевірка сумісності.

Апстрім не встановлюється через pip (плоский layout із кількома модулями верхнього рівня), тому
він береться з одного з місць: уже імпортований пакет → ``GEMINI_WEB2API_PATH`` → кеш
``~/.cache/gemini-web2api-manager/upstream/<ref>`` (наповнюється командою ``fetch-upstream``).
Код апстріму не змінюється, лише підміняються його функції під час виконання (див. gateway.py).
"""

from __future__ import annotations

import importlib
import io
import os
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

REPO = "Sophomoresty/gemini-web2api"
PINNED_REF = "2bb988bfcbb82a7fab5d2c99aa5560ff40d64f7e"  # версія, на якій проходять тести (v1.1.0 + 3.7-flash)
ENV_PATH = "GEMINI_WEB2API_PATH"


class UpstreamError(RuntimeError):
    pass


@dataclass(frozen=True)
class Upstream:
    root: Path | None
    version: str
    config: ModuleType
    gemini: ModuleType
    models: ModuleType
    multimodal: ModuleType
    server: ModuleType


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "gemini-web2api-manager" / "upstream"


def _package_root(directory: Path) -> Path | None:
    """Тека, що містить пакет gemini_web2api (сама або одна вкладена, як у zip з GitHub)."""
    if (directory / "gemini_web2api" / "__init__.py").exists():
        return directory
    for child in sorted(directory.glob("*")):
        if (child / "gemini_web2api" / "__init__.py").exists():
            return child
    return None


def fetch(ref: str = PINNED_REF, dest: Path | None = None) -> Path:
    """Завантажити вихідний код апстріму з GitHub (потрібен лише доступ до codeload.github.com)."""
    dest = (dest or cache_dir()) / ref
    existing = _package_root(dest) if dest.exists() else None
    if existing:
        return existing
    url = f"https://codeload.github.com/{REPO}/zip/{ref}"
    try:
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - фіксований https-хост
            payload = response.read()
    except OSError as exc:
        raise UpstreamError(f"Could not download {url}: {exc}") from exc
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(dest)
    root = _package_root(dest)
    if root is None:
        raise UpstreamError(f"The archive from {url} does not contain the gemini_web2api package.")
    return root


def _candidates(explicit: str | os.PathLike[str] | None) -> list[Path]:
    paths: list[Path] = []
    for raw in (explicit, os.environ.get(ENV_PATH)):
        if raw:
            root = _package_root(Path(raw).expanduser())
            if root:
                paths.append(root)
    pinned = cache_dir() / PINNED_REF
    if pinned.exists() and (root := _package_root(pinned)):
        paths.append(root)
    return paths


def problems(up: Upstream) -> list[str]:
    """Чого бракує апстріму для підміни. Порожній список — сумісний."""
    wanted = {
        "config": ["CONFIG"],
        "gemini": ["load_cookie", "_get_httpx_client", "_build_headers", "generate", "generate_stream", "HAS_HTTPX"],
        "multimodal": ["load_cookie", "_cached_page_tokens", "_get_page_tokens", "upload_image"],
        "server": ["GeminiHandler", "ThreadedServer", "generate", "generate_stream", "CONFIG"],
        "models": ["MODELS", "resolve_model"],
    }
    missing = [
        f"{mod}.{name}"
        for mod, names in wanted.items()
        for name in names
        if not hasattr(getattr(up, mod), name)
    ]
    handler = getattr(up.server, "GeminiHandler", None)
    for name in ("do_POST", "do_GET", "_authorized", "_read_request_body", "send_json"):
        if handler is not None and not hasattr(handler, name):
            missing.append(f"server.GeminiHandler.{name}")
    return missing


def load(explicit_path: str | os.PathLike[str] | None = None) -> Upstream:
    """Імпортувати апстрім і перевірити його сумісність."""
    try:
        importlib.import_module("gemini_web2api")
    except ImportError:
        for root in _candidates(explicit_path):
            sys.path.insert(0, str(root))
            try:
                importlib.import_module("gemini_web2api")
                break
            except ImportError:
                sys.path.remove(str(root))
        else:
            raise UpstreamError(
                "The gemini-web2api sources were not found. Run `gemini-web2api-manager fetch-upstream` "
                f"(downloads the tested version), or set {ENV_PATH} to a clone of "
                f"https://github.com/{REPO}."
            ) from None
    pkg = importlib.import_module("gemini_web2api")
    up = Upstream(
        root=Path(pkg.__file__).resolve().parent.parent if pkg.__file__ else None,
        version=getattr(pkg, "__version__", "?"),
        config=importlib.import_module("gemini_web2api.config"),
        gemini=importlib.import_module("gemini_web2api.gemini"),
        models=importlib.import_module("gemini_web2api.models"),
        multimodal=importlib.import_module("gemini_web2api.multimodal"),
        server=importlib.import_module("gemini_web2api.server"),
    )
    missing = problems(up)
    if missing:
        raise UpstreamError(
            f"gemini-web2api {up.version} is not compatible with this manager (missing: "
            f"{', '.join(missing)}). Use the tested version: `gemini-web2api-manager fetch-upstream`."
        )
    return up

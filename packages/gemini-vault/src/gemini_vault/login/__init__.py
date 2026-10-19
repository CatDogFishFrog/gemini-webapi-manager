"""Sign-in flows: a browser window (``browser_login``) or the terminal only (``console_login``)."""

from .browser import browser_login
from .common import (
    DEFAULT_TARGET,
    ENV_BROWSER_PATH,
    GEMINI_HOST,
    GEMINI_URL,
    LoginResult,
    LoginTarget,
    import_patchright,
)
from .console import Step, classify, console_login
from .fingerprint import BrowserFingerprint, generate_fingerprint
from .human import HumanAgent, human_delay
from .io import LoginIO, ScriptedIO, TerminalIO

__all__ = [
    "BrowserFingerprint",
    "DEFAULT_TARGET",
    "ENV_BROWSER_PATH",
    "GEMINI_HOST",
    "GEMINI_URL",
    "HumanAgent",
    "LoginIO",
    "LoginResult",
    "LoginTarget",
    "ScriptedIO",
    "Step",
    "TerminalIO",
    "browser_login",
    "classify",
    "console_login",
    "generate_fingerprint",
    "human_delay",
    "import_patchright",
]

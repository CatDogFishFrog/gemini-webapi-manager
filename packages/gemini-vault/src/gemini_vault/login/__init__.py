"""Sign-in flows: a browser window (``browser_login``) or the terminal only (``console_login``)."""

from .browser import browser_login
from .common import (
    DEFAULT_TARGET,
    ENV_BROWSER_PATH,
    GEMINI_HOST,
    GEMINI_URL,
    LoginResult,
    LoginTarget,
)
from .console import Step, classify, console_login
from .io import LoginIO, ScriptedIO, TerminalIO

__all__ = [
    "DEFAULT_TARGET",
    "ENV_BROWSER_PATH",
    "GEMINI_HOST",
    "GEMINI_URL",
    "LoginIO",
    "LoginResult",
    "LoginTarget",
    "ScriptedIO",
    "Step",
    "TerminalIO",
    "browser_login",
    "classify",
    "console_login",
]

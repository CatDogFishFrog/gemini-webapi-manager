"""gemini-webapi-manager: guided login, encrypted multi-account vault and active-account
switching on top of gemini-webapi."""

from .client import ManagedChat, ManagedClient
from .config import ENV_HOME, ENV_KEY, ENV_MAX_SLOTS, Settings
from .crypto import generate_key
from .errors import (
    ClientNotStartedError,
    ConfigError,
    DuplicateAccountError,
    GeminiManagerError,
    InvalidKeyError,
    LoginError,
    LoginTimeoutError,
    MissingKeyError,
    NoActiveAccountError,
    SessionExpiredError,
    SlotEmptyError,
    SlotError,
    SlotOccupiedError,
    VaultCorruptedError,
    WeakKeyError,
)
from .login import LoginResult
from .manager import AccountManager
from .models import Account
from .usage import UsageSnapshot, UsageWindow

__version__ = "0.1.0"

__all__ = [
    "Account",
    "AccountManager",
    "ClientNotStartedError",
    "ConfigError",
    "DuplicateAccountError",
    "ENV_HOME",
    "ENV_KEY",
    "ENV_MAX_SLOTS",
    "GeminiManagerError",
    "InvalidKeyError",
    "LoginError",
    "LoginResult",
    "LoginTimeoutError",
    "ManagedChat",
    "ManagedClient",
    "MissingKeyError",
    "NoActiveAccountError",
    "SessionExpiredError",
    "Settings",
    "SlotEmptyError",
    "SlotError",
    "SlotOccupiedError",
    "UsageSnapshot",
    "UsageWindow",
    "VaultCorruptedError",
    "WeakKeyError",
    "__version__",
    "generate_key",
]

"""gemini-vault: ядро — зашифроване сховище акаунтів Google/Gemini, слоти, вхід (вікно або
лише термінал), сесії через gemini-webapi та політика вибору акаунта за лімітами."""

from .config import ENV_HOME, ENV_KEY, ENV_KEY_FILE, ENV_MAX_SLOTS, Settings
from .crypto import generate_key
from .errors import (
    ClientNotStartedError,
    ConfigError,
    DuplicateAccountError,
    GeminiManagerError,
    InvalidKeyError,
    LoginAbortedError,
    LoginBlockedError,
    LoginChallengeError,
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
from .models import Account
from .registry import AccountRegistry, RemoveResult
from .sessions import ManagedChat, ManagedClient
from .usage import UsageSnapshot, UsageWindow

__version__ = "0.2.0"

__all__ = [
    "Account", "AccountRegistry", "ClientNotStartedError", "ConfigError", "DuplicateAccountError",
    "ENV_HOME", "ENV_KEY", "ENV_KEY_FILE", "ENV_MAX_SLOTS", "GeminiManagerError", "InvalidKeyError",
    "LoginAbortedError", "LoginBlockedError", "LoginChallengeError", "LoginError", "LoginResult",
    "LoginTimeoutError", "ManagedChat", "ManagedClient", "MissingKeyError", "NoActiveAccountError",
    "RemoveResult", "SessionExpiredError", "Settings", "SlotEmptyError", "SlotError",
    "SlotOccupiedError", "UsageSnapshot", "UsageWindow", "VaultCorruptedError", "WeakKeyError",
    "__version__", "generate_key",
]

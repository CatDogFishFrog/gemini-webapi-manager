"""gemini-webapi-manager: бібліотечний API (AccountManager) поверх ядра ``gemini-vault``.

Усе, що експортувалося раніше, доступне тут і далі.
"""

from gemini_vault import (
    ENV_HOME,
    ENV_KEY,
    ENV_MAX_SLOTS,
    Account,
    ClientNotStartedError,
    ConfigError,
    DuplicateAccountError,
    GeminiManagerError,
    InvalidKeyError,
    LoginAbortedError,
    LoginBlockedError,
    LoginChallengeError,
    LoginError,
    LoginResult,
    LoginTimeoutError,
    ManagedChat,
    ManagedClient,
    MissingKeyError,
    NoActiveAccountError,
    RemoveResult,
    SessionExpiredError,
    Settings,
    SlotEmptyError,
    SlotError,
    SlotOccupiedError,
    UsageSnapshot,
    UsageWindow,
    VaultCorruptedError,
    WeakKeyError,
    generate_key,
)

from .manager import AccountManager

__version__ = "0.2.0"

__all__ = [
    "Account", "AccountManager", "ClientNotStartedError", "ConfigError", "DuplicateAccountError",
    "ENV_HOME", "ENV_KEY", "ENV_MAX_SLOTS", "GeminiManagerError", "InvalidKeyError",
    "LoginAbortedError", "LoginBlockedError", "LoginChallengeError", "LoginError", "LoginResult",
    "LoginTimeoutError", "ManagedChat", "ManagedClient", "MissingKeyError", "NoActiveAccountError",
    "RemoveResult", "SessionExpiredError", "Settings", "SlotEmptyError", "SlotError",
    "SlotOccupiedError", "UsageSnapshot", "UsageWindow", "VaultCorruptedError", "WeakKeyError",
    "__version__", "generate_key",
]

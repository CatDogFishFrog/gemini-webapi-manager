"""Exception hierarchy. Everything raised on purpose derives from GeminiManagerError."""

from __future__ import annotations


class GeminiManagerError(Exception):
    """Base class for all errors raised by gemini-vault."""


class ConfigError(GeminiManagerError):
    """Invalid or missing configuration."""


class MissingKeyError(ConfigError):
    """The encryption key was not provided."""


class WeakKeyError(ConfigError):
    """The encryption key is too short to be safe."""


class InvalidKeyError(GeminiManagerError):
    """The vault could not be decrypted: wrong key or tampered file."""


class VaultCorruptedError(GeminiManagerError):
    """The vault file is not in a recognised format."""


class SlotError(GeminiManagerError):
    """A slot number is invalid."""


class SlotOccupiedError(SlotError):
    """Login was requested for a slot that already holds an account."""

    def __init__(self, slot: int, email: str | None = None) -> None:
        self.slot = slot
        self.email = email
        who = f" ({email})" if email else ""
        super().__init__(
            f"Slot {slot} is already occupied{who}. Remove the account from the slot first."
        )


class SlotEmptyError(SlotError):
    """An operation needs an account in a slot that is empty."""

    def __init__(self, slot: int) -> None:
        self.slot = slot
        super().__init__(f"Slot {slot} is empty.")


class NoActiveAccountError(GeminiManagerError):
    """No account is currently selected as active."""

    def __init__(self) -> None:
        super().__init__("No active account. Log in to a slot or call set_active(slot).")


class DuplicateAccountError(GeminiManagerError):
    """The same Google account is already connected in another slot."""

    def __init__(self, email: str, slot: int) -> None:
        self.email = email
        self.slot = slot
        super().__init__(f"Account {email} is already connected in slot {slot}.")


class LoginError(GeminiManagerError):
    """The interactive login did not complete."""


class LoginTimeoutError(LoginError):
    """The user did not finish signing in before the timeout."""


class LoginAbortedError(LoginError):
    """The user cancelled the sign-in (Ctrl+C, empty answer, or chose to abort)."""


class LoginBlockedError(LoginError):
    """Google refused the sign-in (``/signin/rejected``, "this browser may not be secure", ...)."""


class LoginChallengeError(LoginError):
    """The console login met a page it cannot drive (unknown challenge, unsupported step).

    ``debug_dir`` (if set) holds a screenshot and a sanitised page description that can be
    handed to a developer / AI agent to teach the flow the new step.
    """

    def __init__(self, message: str, debug_dir: str | None = None) -> None:
        self.debug_dir = debug_dir
        super().__init__(message)


class SessionExpiredError(GeminiManagerError):
    """Google rejected the stored session; the account must be re-added."""

    def __init__(self, slot: int, email: str | None = None) -> None:
        self.slot = slot
        self.email = email
        who = f" ({email})" if email else ""
        super().__init__(
            f"The session stored in slot {slot}{who} is no longer valid. "
            "Remove the account from the slot and log in again."
        )


class ClientNotStartedError(GeminiManagerError):
    """A ManagedClient was used before start() / outside `async with`."""

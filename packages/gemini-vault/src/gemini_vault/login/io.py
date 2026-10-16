"""Terminal I/O for the console login, behind a tiny interface so it can be scripted in tests."""

from __future__ import annotations

import getpass
import sys
from collections.abc import Callable, Sequence
from typing import Protocol, TextIO

from ..errors import LoginAbortedError


class LoginIO(Protocol):
    """What the console login needs from the outside world."""

    def info(self, message: str) -> None: ...

    def warn(self, message: str) -> None: ...

    def ask(self, prompt: str) -> str:
        """Read a visible line."""

    def ask_secret(self, prompt: str) -> str:
        """Read a line without echo (passwords, one-time codes)."""

    def choose(self, prompt: str, options: Sequence[str]) -> int:
        """Show numbered ``options`` and return the 0-based index picked."""


class TerminalIO:
    """Real terminal. Prompts and messages go to stderr so stdout stays script-friendly."""

    def __init__(self, out: TextIO | None = None) -> None:
        self._out = out or sys.stderr

    def info(self, message: str) -> None:
        print(message, file=self._out, flush=True)

    def warn(self, message: str) -> None:
        print(f"[!] {message}", file=self._out, flush=True)

    def ask(self, prompt: str) -> str:
        try:
            print(prompt, end="", file=self._out, flush=True)
            return input().strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise LoginAbortedError("Login cancelled.") from exc

    def ask_secret(self, prompt: str) -> str:
        try:
            return getpass.getpass(prompt, stream=self._out).strip("\r\n")
        except (EOFError, KeyboardInterrupt) as exc:
            raise LoginAbortedError("Login cancelled.") from exc

    def choose(self, prompt: str, options: Sequence[str]) -> int:
        self.info(prompt)
        for number, option in enumerate(options, start=1):
            self.info(f"  {number}. {option}")
        while True:
            raw = self.ask(f"Choice [1-{len(options)}, q = abort]: ")
            if raw.lower() in {"q", "quit", "abort"}:
                raise LoginAbortedError("Login cancelled.")
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1
            self.warn("Enter one of the numbers above.")


class ScriptedIO:
    """Feeds canned answers and records everything shown; used by the tests.

    ``answers`` may hold strings or callables ``(kind, prompt, options) -> str``. For ``choose``
    an answer is the 1-based number as a string, or a substring of the wanted option.
    """

    def __init__(self, answers: Sequence[str | Callable[[str, str, Sequence[str]], str]]) -> None:
        self._answers = list(answers)
        self.transcript: list[tuple[str, str]] = []

    def _next(self, kind: str, prompt: str, options: Sequence[str] = ()) -> str:
        if not self._answers:
            raise AssertionError(f"ScriptedIO ran out of answers at {kind}: {prompt!r}")
        item = self._answers.pop(0)
        return item(kind, prompt, options) if callable(item) else item

    def info(self, message: str) -> None:
        self.transcript.append(("info", message))

    def warn(self, message: str) -> None:
        self.transcript.append(("warn", message))

    def ask(self, prompt: str) -> str:
        self.transcript.append(("ask", prompt))
        return self._next("ask", prompt)

    def ask_secret(self, prompt: str) -> str:
        self.transcript.append(("secret", prompt))
        return self._next("secret", prompt)

    def choose(self, prompt: str, options: Sequence[str]) -> int:
        self.transcript.append(("choose", prompt + " | " + " / ".join(options)))
        answer = self._next("choose", prompt, options)
        if answer.isdigit():
            return int(answer) - 1
        for index, option in enumerate(options):
            if answer.lower() in option.lower():
                return index
        raise AssertionError(f"No option matching {answer!r} in {list(options)}")

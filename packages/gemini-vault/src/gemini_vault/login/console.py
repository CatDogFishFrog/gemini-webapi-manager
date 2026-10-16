"""Console login: sign in to Google from a terminal, with no window and no X server.

A headless Chromium opens Google's sign-in page and a small state machine drives it. Every
time the page asks for something, the question is relayed to the terminal: e-mail, password,
which verification method to use, the code, a phone number, "tap Yes on your phone", a
consent screen... The password and codes are read without echo and are never logged.

The machine does **not** hard-code Google's markup page by page. It looks at what is on the
screen (visible inputs, buttons, challenge options, error texts) and classifies the page into a
handful of generic steps (see :class:`Step`). That keeps it working when Google rearranges a
page, and unknown pages degrade into a visible menu instead of a crash. When it meets something
it cannot handle it writes a screenshot plus a sanitised description of the page (no values,
e-mails masked) into a debug directory, so a developer or an AI agent can extend the flow.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..errors import (
    LoginAbortedError,
    LoginBlockedError,
    LoginChallengeError,
    LoginError,
    LoginTimeoutError,
)
from .common import (
    DEFAULT_TARGET,
    PSID,
    LoginResult,
    LoginTarget,
    capture_session,
    import_playwright,
    launch_browser,
    normalised_user_agent,
)
from .io import LoginIO, TerminalIO

# --------------------------------------------------------------------------- page observation
_OPTION_SELECTOR = "[data-challengetype], [data-challengeentry], [data-challengeid]"
_BUTTON_SELECTOR = (
    'button, [role="button"], input[type="submit"], input[type="button"], a[href]'
)

OBSERVE_JS = r"""
() => {
  const visible = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && parseFloat(cs.opacity || '1') > 0.05;
  };
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const text = (el) => clean(el.innerText || el.textContent);
  const labelOf = (el) => {
    let t = el.getAttribute('aria-label') || '';
    if (!t && el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) t = text(l);
    }
    if (!t) { const l = el.closest('label'); if (l) t = text(l); }
    if (!t) t = el.getAttribute('placeholder') || '';
    return clean(t);
  };
  const skip = new Set(['hidden','submit','button','checkbox','radio','image','reset','file']);
  const inputs = [...document.querySelectorAll('input, textarea')]
    .filter(e => !skip.has((e.type || '').toLowerCase()) && visible(e))
    .map(e => ({
      id: e.id || '', name: e.name || '', type: (e.type || 'text').toLowerCase(),
      autocomplete: e.getAttribute('autocomplete') || '', label: labelOf(e),
      maxlength: e.maxLength > 0 ? e.maxLength : 0,
    }));
  const buttons = [...document.querySelectorAll(%(buttons)s)].filter(visible).map(e => ({
    tag: e.tagName.toLowerCase(), id: e.id || '',
    text: clean(e.tagName === 'INPUT' ? e.value : text(e)),
    role: e.getAttribute('role') || '',
    href: e.tagName === 'A' ? (e.getAttribute('href') || '').split('?')[0].slice(0, 80) : '',
  })).filter(b => b.text);
  const options = [...document.querySelectorAll(%(options)s)].filter(visible).map(e => ({
    text: text(e), type: e.getAttribute('data-challengetype') || '',
  })).filter(o => o.text);
  const errSel = '[role="alert"], [aria-live="assertive"], [jsname="B34EJ"], .Ekjuhf, .o6cuMc, '
               + '[class*="error" i], [id*="error" i]';
  const errors = [...document.querySelectorAll(errSel)].filter(visible).map(text)
    .filter(t => t && t.length < 300);
  const h = document.querySelector('h1, #headingText, [role="heading"]');
  const body = document.body ? document.body.innerText : '';
  const captcha = !!([...document.querySelectorAll('img')].find(
      i => visible(i) && /captcha/i.test((i.src || '') + ' ' + i.id + ' ' + i.className)))
    || /type the text you hear or see/i.test(body);
  return {
    url: location.href, title: document.title, heading: h ? text(h) : '',
    text: clean(body).slice(0, 2500), inputs, buttons: buttons.slice(0, 40), options,
    errors: [...new Set(errors)].slice(0, 5), captcha,
  };
}
""" % {"buttons": json.dumps(_BUTTON_SELECTOR), "options": json.dumps(_OPTION_SELECTOR)}


@dataclass
class Observation:
    """What is on the screen right now (values of inputs are deliberately never read)."""

    url: str = ""
    title: str = ""
    heading: str = ""
    text: str = ""
    inputs: list[dict[str, Any]] = field(default_factory=list)
    buttons: list[dict[str, Any]] = field(default_factory=list)
    options: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    captcha: bool = False

    @classmethod
    def from_page_data(cls, data: dict[str, Any]) -> "Observation":
        return cls(
            url=str(data.get("url", "")),
            title=str(data.get("title", "")),
            heading=str(data.get("heading", "")),
            text=str(data.get("text", "")),
            inputs=list(data.get("inputs") or []),
            buttons=list(data.get("buttons") or []),
            options=list(data.get("options") or []),
            errors=[str(e) for e in (data.get("errors") or [])],
            captcha=bool(data.get("captcha")),
        )

    @property
    def path(self) -> str:
        return urlparse(self.url).path.lower()

    @property
    def host(self) -> str:
        return urlparse(self.url).hostname or ""

    def signature(self) -> tuple[Any, ...]:
        """Changes when the page moved on or showed an error; used to detect a finished step."""
        return (
            self.url.split("#")[0],
            self.heading,
            tuple(i.get("name") or i.get("id") for i in self.inputs),
            tuple(self.errors),
            len(self.options),
        )


# --------------------------------------------------------------------------- classification
class Step(str, Enum):
    APP = "app"  # back on the application host (success candidate)
    IDENTIFIER = "identifier"
    PASSWORD = "password"
    OPTIONS = "options"  # "choose how to verify"
    INPUT = "input"  # any other text field: code, phone number, recovery e-mail, captcha...
    WAIT_DEVICE = "wait_device"  # "check your phone", nothing to type
    ACTION = "action"  # only buttons: consent / "not now" / continue screens
    CAPTCHA = "captcha"
    REJECTED = "rejected"
    LOADING = "loading"
    UNKNOWN = "unknown"


_REJECTED_RE = re.compile(
    r"couldn.t sign you in|this browser or app may not be secure|"
    r"try using a different browser|unusual activity|account has been (disabled|suspended)",
    re.I,
)
_DEVICE_RE = re.compile(
    r"check your (phone|tablet|device)|tap (yes|the)|approve (the )?(sign|request)|"
    r"google sent a notification|open the .{0,30} app|waiting for (you|approval)",
    re.I,
)
_NOISE_BUTTON_RE = re.compile(
    r"^(help|privacy|terms|privacy policy|terms of service|learn more|about|get help|"
    r"english.*|create account|forgot (email|password)\??|more options|sign in with a passkey)$",
    re.I,
)
_ANOTHER_WAY_RE = re.compile(
    r"try another way|more ways to verify|choose another|use another (method|way)|"
    r"try a different way|verify another way",
    re.I,
)
_NEXT_RE = re.compile(
    r"^(next|continue|verify|confirm|done|submit|sign in|yes|allow|i agree|accept|ok)$", re.I
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def mask_text(value: str) -> str:
    """Hide e-mail addresses in anything that may leave the machine (debug dumps)."""
    return _EMAIL_RE.sub("<email>", value)


def meaningful_buttons(obs: Observation) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for button in obs.buttons:
        text = button["text"]
        if _NOISE_BUTTON_RE.match(text) or text in seen:
            continue
        seen.add(text)
        result.append(button)
    return result


def another_way_button(obs: Observation) -> dict[str, Any] | None:
    return next((b for b in obs.buttons if _ANOTHER_WAY_RE.search(b["text"])), None)


def classify(obs: Observation, target: LoginTarget = DEFAULT_TARGET) -> Step:
    """Decide what kind of step the current page is. Pure function: easy to unit-test."""
    if obs.host == target.app_host:
        return Step.APP
    inputs = obs.inputs
    has_identifier = any(
        i.get("name", "").lower() == "identifier" or i.get("id") == "identifierId" for i in inputs
    )
    if "/signin/rejected" in obs.path or (not inputs and _REJECTED_RE.search(obs.text)):
        return Step.REJECTED
    if obs.captcha and inputs and not has_identifier:
        return Step.CAPTCHA
    if has_identifier:
        return Step.IDENTIFIER
    if len(obs.options) >= 2 or (obs.path.endswith("/challenge/selection") and obs.options):
        return Step.OPTIONS
    if any(i.get("type") == "password" or i.get("name", "").lower() == "passwd" for i in inputs):
        return Step.PASSWORD
    if inputs:
        return Step.INPUT
    if obs.path.endswith("/challenge/selection") and meaningful_buttons(obs):
        return Step.OPTIONS
    if "/challenge/dp" in obs.path or _DEVICE_RE.search(obs.text):
        return Step.WAIT_DEVICE
    if meaningful_buttons(obs):
        return Step.ACTION
    return Step.LOADING if len(obs.text) < 20 else Step.UNKNOWN


def _is_secret_input(info: dict[str, Any]) -> bool:
    """Codes and passwords are read without echo; phone numbers, e-mails and captchas are not."""
    if info.get("type") == "password" or info.get("autocomplete") == "one-time-code":
        return True
    return bool(re.search(r"pin|code|totp|otp", f"{info.get('name', '')} {info.get('id', '')}", re.I))


def sanitise_observation(obs: Observation) -> dict[str, Any]:
    """A description of the page that is safe to share: no values, no e-mails, no query strings."""
    parsed = urlparse(obs.url)
    return {
        "url": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
        "title": mask_text(obs.title),
        "heading": mask_text(obs.heading),
        "text": mask_text(obs.text)[:1200],
        "inputs": [
            {**{k: i.get(k) for k in ("id", "name", "type", "autocomplete", "maxlength")},
             "label": mask_text(str(i.get("label", "")))}
            for i in obs.inputs
        ],
        "buttons": [
            {"tag": b.get("tag"), "id": b.get("id"), "role": b.get("role"),
             "href": b.get("href"), "text": mask_text(b["text"])}
            for b in obs.buttons
        ],
        "options": [{"type": o.get("type"), "text": mask_text(o["text"])} for o in obs.options],
        "errors": [mask_text(e) for e in obs.errors],
        "captcha": obs.captcha,
    }


class DebugRecorder:
    """Writes numbered screenshots + sanitised page descriptions + a transcript of the flow."""

    def __init__(self, directory: Path | None, *, always: bool) -> None:
        self.directory = directory
        self.always = always  # True when the user asked for --debug-dir: dump every step
        self._counter = 0

    def _ensure(self) -> Path:
        if self.directory is None:
            self.directory = Path.cwd() / "gemini-login-debug"
        self.directory.mkdir(parents=True, exist_ok=True)
        return self.directory

    def log(self, line: str) -> None:
        if self.directory is None and not self.always:
            return
        with (self._ensure() / "transcript.txt").open("a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%H:%M:%S')}] {mask_text(line)}\n")

    async def snapshot(self, page: Any, obs: Observation, label: str, *, force: bool = False) -> Path | None:
        if not (force or self.always):
            return None
        folder = self._ensure()
        self._counter += 1
        stem = folder / f"{self._counter:02d}-{label}"
        (stem.with_suffix(".json")).write_text(
            json.dumps(sanitise_observation(obs), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            await page.screenshot(path=str(stem.with_suffix(".png")), full_page=True)
        except Exception:  # noqa: BLE001 - a missing screenshot must not break the login
            pass
        return folder


# --------------------------------------------------------------------------- the flow
@dataclass
class ConsoleLoginOptions:
    email: str | None = None
    idle_timeout: float = 900.0  # reset after every answer the human gives
    device_wait: float = 180.0  # how long to wait for "tap Yes on your phone"
    max_steps: int = 80
    password_attempts: int = 3
    input_attempts: int = 4
    settle_timeout: float = 20.0


class ConsoleLogin:
    def __init__(
        self,
        page: Any,
        context: Any,
        io: LoginIO,
        *,
        target: LoginTarget,
        pw_error: type,
        recorder: DebugRecorder,
        options: ConsoleLoginOptions,
        say: Callable[[str], None],
    ) -> None:
        self.page = page
        self.context = context
        self.io = io
        self.target = target
        self.pw_error = pw_error
        self.rec = recorder
        self.opt = options
        self.say = say
        self.deadline = time.monotonic() + options.idle_timeout
        self._email = options.email
        self._failures: dict[Step, int] = {}
        self._unknown = 0

    # ----------------------------------------------------------- helpers
    def _touch(self) -> None:
        """A human answered: restart the idle clock."""
        self.deadline = time.monotonic() + self.opt.idle_timeout

    def _check_deadline(self) -> None:
        if time.monotonic() > self.deadline:
            raise LoginTimeoutError("Timed out waiting for the sign-in to complete.")

    async def observe(self) -> Observation:
        for _ in range(6):
            try:
                data = await self.page.evaluate(OBSERVE_JS)
                return Observation.from_page_data(data)
            except self.pw_error:
                await asyncio.sleep(0.5)  # navigation in flight: the context was destroyed
        return Observation(url=self.page.url)

    async def settle(self, before: Observation) -> Observation:
        """Wait until the page reacts to what we just submitted (navigates or shows an error)."""
        signature = before.signature()
        end = time.monotonic() + self.opt.settle_timeout
        latest = before
        while time.monotonic() < end:
            await asyncio.sleep(0.4)
            latest = await self.observe()
            if latest.signature() != signature:
                await asyncio.sleep(0.3)  # let the new page finish rendering
                return await self.observe()
        return latest

    def _input_locator(self, info: dict[str, Any]) -> Any:
        if info.get("id"):
            return self.page.locator(f'[id="{info["id"]}"]').first
        if info.get("name"):
            return self.page.locator(f'[name="{info["name"]}"]').first
        return self.page.locator("input:visible, textarea:visible").first

    async def _type(self, info: dict[str, Any], value: str) -> None:
        locator = self._input_locator(info)
        await locator.click()
        await locator.fill("")
        await locator.press_sequentially(value, delay=35)

    async def _submit(self, obs: Observation, info: dict[str, Any]) -> None:
        """Press the page's own "Next"/"Verify" button, or Enter if there is none."""
        primary = next(
            (b for b in obs.buttons if b.get("id", "").endswith("Next")),
            None,
        ) or next((b for b in obs.buttons if _NEXT_RE.match(b["text"])), None)
        if primary is not None:
            try:
                await self._click_button(primary)
                return
            except self.pw_error:
                pass
        await self._input_locator(info).press("Enter")

    async def _click_button(self, button: dict[str, Any]) -> None:
        if button.get("id"):
            await self.page.locator(f'[id="{button["id"]}"]').first.click(timeout=5000)
            return
        pattern = re.compile(r"^\s*" + re.escape(button["text"]) + r"\s*$", re.I)
        locator = (
            self.page.locator(_BUTTON_SELECTOR)
            .filter(visible=True)
            .filter(has_text=pattern)
            .first
        )
        await locator.click(timeout=5000)

    async def _click_option(self, index: int, obs: Observation, texts: list[str]) -> None:
        if obs.options:
            locator = self.page.locator(_OPTION_SELECTOR).filter(visible=True).nth(index)
            await locator.click(timeout=5000)
        else:
            button = [b for b in meaningful_buttons(obs) if b["text"] == texts[index]][0]
            await self._click_button(button)

    def _bump(self, step: Step, limit: int) -> None:
        self._failures[step] = self._failures.get(step, 0) + 1
        if self._failures[step] >= limit:
            raise LoginError(
                "Too many rejected attempts on this step; stopping so the account does not get "
                "locked. Try again later."
            )

    def _show_errors(self, obs: Observation) -> None:
        for error in obs.errors:
            self.io.warn(error)

    async def _dump_failure(self, obs: Observation, label: str) -> str | None:
        folder = await self.rec.snapshot(self.page, obs, label, force=True)
        return str(folder) if folder else None

    # ----------------------------------------------------------- main loop
    async def run(self) -> None:
        """Drive the sign-in until the browser is back on the application with a session."""
        previous: Observation | None = None
        for _ in range(self.opt.max_steps):
            self._check_deadline()
            obs = await self.observe()
            step = classify(obs, self.target)
            self.rec.log(f"step={step.value} url={mask_text(obs.url.split('?')[0])}")
            await self.rec.snapshot(self.page, obs, step.value)

            if step is Step.APP:
                if await self._has_session():
                    return
                await asyncio.sleep(1.0)
                continue
            if step is Step.LOADING:
                await asyncio.sleep(1.0)
                self._unknown += 1
                if self._unknown > 40:
                    raise LoginChallengeError("The page stays empty.", await self._dump_failure(obs, "empty"))
                continue
            self._unknown = 0

            handler = {
                Step.IDENTIFIER: self._step_identifier,
                Step.PASSWORD: self._step_password,
                Step.OPTIONS: self._step_options,
                Step.INPUT: self._step_input,
                Step.CAPTCHA: self._step_captcha,
                Step.WAIT_DEVICE: self._step_wait_device,
                Step.ACTION: self._step_action,
                Step.REJECTED: self._step_rejected,
                Step.UNKNOWN: self._step_unknown,
            }[step]
            await handler(obs)
            previous = obs
        raise LoginChallengeError(
            "The sign-in did not finish within the step limit.",
            await self._dump_failure(previous or Observation(), "step-limit"),
        )

    async def _has_session(self) -> bool:
        end = time.monotonic() + 15.0
        while time.monotonic() < end:
            names = {c["name"] for c in await self.context.cookies(self.target.app_url)}
            if PSID in names:
                return True
            await asyncio.sleep(1.0)
        raise LoginError("Reached the application but Google set no session cookie.")

    # ----------------------------------------------------------- steps
    async def _step_identifier(self, obs: Observation) -> None:
        self._show_errors(obs)
        if obs.errors:
            self._bump(Step.IDENTIFIER, 3)
            self._email = None  # the previous address was refused: ask again
        if not self._email:
            self._email = self.io.ask("Google account e-mail or phone: ")
            self._touch()
        if not self._email:
            raise LoginAbortedError("No account given.")
        info = next(
            i for i in obs.inputs
            if i.get("name", "").lower() == "identifier" or i.get("id") == "identifierId"
        )
        self.rec.log("typed identifier")
        await self._type(info, self._email)
        await self._submit(obs, info)
        await self.settle(obs)

    async def _step_password(self, obs: Observation) -> None:
        if obs.errors:
            self._show_errors(obs)
            self._bump(Step.PASSWORD, self.opt.password_attempts)
        who = f" for {self._email}" if self._email else ""
        password = self.io.ask_secret(f"Password{who} (input hidden, '!' = another method): ")
        self._touch()
        if password == "!":
            await self._go_another_way(obs)
            return
        if not password:
            raise LoginAbortedError("No password entered.")
        info = next(
            i for i in obs.inputs
            if i.get("type") == "password" or i.get("name", "").lower() == "passwd"
        )
        self.rec.log("typed password [hidden]")
        await self._type(info, password)
        await self._submit(obs, info)
        await self.settle(obs)

    async def _step_input(self, obs: Observation) -> None:
        if obs.errors:
            self._show_errors(obs)
            self._bump(Step.INPUT, self.opt.input_attempts)
        info = obs.inputs[0]
        self._describe(obs)
        label = info.get("label") or info.get("name") or "value"
        hint = " ('!' = another method)" if another_way_button(obs) else ""
        ask = self.io.ask_secret if _is_secret_input(info) else self.io.ask
        answer = ask(f"{label}{hint}: ")
        self._touch()
        if answer == "!" and another_way_button(obs):
            await self._go_another_way(obs)
            return
        if not answer:
            raise LoginAbortedError("Nothing entered.")
        self.rec.log("typed verification input [hidden]")
        await self._type(info, answer)
        await self._submit(obs, info)
        await self.settle(obs)

    async def _step_captcha(self, obs: Observation) -> None:
        folder = await self.rec.snapshot(self.page, obs, "captcha", force=True)
        self.io.warn("Google shows a CAPTCHA.")
        if folder:
            self.io.warn(
                f"A screenshot is in {folder} (copy it to a machine where you can view it, "
                "e.g. with scp), then type the characters below."
            )
        await self._step_input(obs)

    async def _step_options(self, obs: Observation) -> None:
        texts = [o["text"] for o in obs.options] or [b["text"] for b in meaningful_buttons(obs)]
        self.io.info(f"\n{obs.heading or 'Verify it is you'}")
        index = self.io.choose("How do you want to verify?", texts)
        self._touch()
        self.rec.log(f"chose method #{index + 1}")
        await self._click_option(index, obs, texts)
        await self.settle(obs)

    async def _step_wait_device(self, obs: Observation) -> None:
        self._describe(obs)
        numbers = sorted(set(re.findall(r"(?<![\w@.:])\d{1,3}(?![\w@.:])", obs.text)))
        if numbers:
            self.io.info(f"Number(s) shown on the page (pick it on your phone if asked): {', '.join(numbers)}")
        self.io.info(f"Waiting up to {int(self.opt.device_wait)}s for you to approve on the device...")
        end = time.monotonic() + self.opt.device_wait
        signature = obs.signature()
        while time.monotonic() < end:
            await asyncio.sleep(2.0)
            latest = await self.observe()
            if latest.signature() != signature:
                return
        options = ["Keep waiting"]
        if another_way_button(obs):
            options.append("Try another way")
        options.append("Abort")
        picked = options[self.io.choose("No approval yet.", options)]
        self._touch()
        if picked == "Keep waiting":
            return
        if picked == "Try another way":
            await self._go_another_way(obs)
            return
        raise LoginAbortedError("Login cancelled.")

    async def _step_action(self, obs: Observation) -> None:
        self._describe(obs)
        buttons = meaningful_buttons(obs)
        labels = [b["text"] for b in buttons]
        index = self.io.choose("Google offers these actions:", labels)
        self._touch()
        self.rec.log(f"clicked action #{index + 1}")
        await self._click_button(buttons[index])
        await self.settle(obs)

    async def _step_rejected(self, obs: Observation) -> None:
        folder = await self._dump_failure(obs, "rejected")
        raise LoginBlockedError(
            "Google refused this sign-in" + (f" ({mask_text(obs.heading)})" if obs.heading else "")
            + ". Typical causes: the server's IP/datacenter is flagged, the browser looks "
            "automated, or the account needs a manual security check. Sign in on a normal "
            "computer instead (`login` with a window) and copy vault.bin to the server."
            + (f" Debug files: {folder}" if folder else "")
        )

    async def _step_unknown(self, obs: Observation) -> None:
        self._unknown += 1
        folder = await self._dump_failure(obs, "unknown")
        self.io.warn("This page is not recognised by the console login.")
        self.io.info(f"{obs.heading or obs.title}\n{obs.text[:400]}")
        if folder:
            self.io.info(f"Debug files (screenshot + page description): {folder}")
        buttons = meaningful_buttons(obs)
        options = ["Look at the page again"] + [f"Click: {b['text']}" for b in buttons] + ["Abort"]
        index = self.io.choose("What now?", options)
        self._touch()
        if index == 0:
            if self._unknown >= 3:
                raise LoginChallengeError("Unknown page, giving up.", folder)
            return
        if index == len(options) - 1:
            raise LoginChallengeError("Unknown page, aborted by the user.", folder)
        await self._click_button(buttons[index - 1])
        await self.settle(obs)

    # ----------------------------------------------------------- shared bits
    def _describe(self, obs: Observation) -> None:
        if obs.heading:
            self.io.info(f"\n{mask_text(obs.heading)}")
        body = mask_text(obs.text)
        if obs.heading and body.startswith(obs.heading):
            body = body[len(obs.heading):].strip()
        if body:
            self.io.info(body[:350] + ("..." if len(body) > 350 else ""))

    async def _go_another_way(self, obs: Observation) -> None:
        button = another_way_button(obs)
        if button is None:
            self.io.warn("This page offers no other method.")
            return
        await self._click_button(button)
        await self.settle(obs)


async def console_login(
    io: LoginIO | None = None,
    *,
    email: str | None = None,
    timeout: float = 900.0,
    headless: bool = True,
    browser: str = "auto",
    proxy: str | None = None,
    executable_path: str | None = None,
    debug_dir: str | Path | None = None,
    device_wait: float = 180.0,
    target: LoginTarget = DEFAULT_TARGET,
    on_status: Callable[[str], None] | None = None,
) -> LoginResult:
    """Sign in to Google through the terminal and return the session.

    ``timeout`` is an *idle* timeout: it restarts every time the human answers a question.
    ``debug_dir`` makes every step dump a screenshot and a sanitised page description there;
    without it these files are written (to ``./gemini-login-debug``) only when something
    unexpected happens.
    """
    async_playwright, PlaywrightError = import_playwright()
    io = io or TerminalIO()
    say = on_status or (lambda _message: None)
    recorder = DebugRecorder(Path(debug_dir) if debug_dir else None, always=debug_dir is not None)
    options = ConsoleLoginOptions(email=email, idle_timeout=timeout, device_wait=device_wait)

    async with async_playwright() as pw:
        browser_obj = await launch_browser(
            pw, browser, headless, proxy, PlaywrightError, executable_path=executable_path
        )
        try:
            context = await browser_obj.new_context(
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                user_agent=await normalised_user_agent(browser_obj),
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            page = await context.new_page()
            say("Opening Google sign-in in a headless browser...")
            try:
                await page.goto(target.sign_in_url, wait_until="domcontentloaded", timeout=60_000)
            except PlaywrightError as exc:
                raise LoginError(f"Could not open the Google sign-in page: {exc}") from exc

            flow = ConsoleLogin(
                page, context, io, target=target, pw_error=PlaywrightError,
                recorder=recorder, options=options, say=say,
            )
            await flow.run()
            say("Signed in. Collecting session data...")
            deadline = time.monotonic() + 60.0
            return await capture_session(
                context, page, target=target, deadline=deadline, pw_error=PlaywrightError, say=say
            )
        except PlaywrightError as exc:
            raise LoginError(f"Browser error during login: {exc}") from exc
        finally:
            try:
                await browser_obj.close()
            except PlaywrightError:
                pass

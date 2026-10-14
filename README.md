# gemini-webapi-manager

Companion for [`gemini-webapi`](https://github.com/HanaokaYuzu/Gemini-API): sign in to a Google
account through a browser window, keep the session in an **encrypted vault**, hold several
accounts in numbered **slots**, switch the **active** one, see usage, and call `gemini-webapi`
through the active account. It wraps the library instead of replacing it: every method it does
not override is forwarded to `gemini_webapi.GeminiClient`, so library updates keep working.

No more copying `__Secure-1PSID` / `__Secure-1PSIDTS` by hand.

## Install

```bash
pip install .            # or: pip install -e .[dev]
gemini-webapi-manager install-browser   # only if you have no Chrome/Edge installed
```

Python 3.11+. Login uses your installed Chrome/Edge when present, otherwise Playwright's Chromium.

## Encryption key

Everything is stored in one file, `vault.bin` (AES-256-GCM, key derived with scrypt), inside
`~/.gemini-webapi-manager` (override with `GEMINI_MANAGER_HOME`).

```bash
export GEMINI_MANAGER_KEY="$(gemini-webapi-manager keygen)"   # keep this safe, min. 16 chars
```

**Moving sessions to another machine:** copy `vault.bin` into that machine's home dir and set the
same `GEMINI_MANAGER_KEY`. The salt lives inside the file, so nothing else is needed.

| Variable | Meaning | Default |
|---|---|---|
| `GEMINI_MANAGER_KEY` | passphrase for the vault (required) | – |
| `GEMINI_MANAGER_HOME` | directory with `vault.bin` | `~/.gemini-webapi-manager` |
| `GEMINI_MANAGER_MAX_SLOTS` | number of slots | `10` |

## Python API

```python
import asyncio
from gemini_webapi_manager import AccountManager

async def main():
    async with AccountManager() as mgr:
        await mgr.login(1)                 # opens a window, waits for you to sign in
        await mgr.login(2)                 # SlotOccupiedError if slot 2 already has an account

        for acc in mgr.accounts():         # list[Account]
            print(acc)                     # "#1* me@gmail.com (42%)"
            print(acc.to_json(indent=2))   # also: acc.to_dict(), Account.list_to_json(...)

        mgr.set_active(2)
        out = await mgr.generate_content("Hello!")      # uses the active account
        print(out.text)

        async with mgr.client(1) as client:             # full gemini-webapi API, specific slot
            chat = client.start_chat()
            print((await chat.send_message("Hi")).text)

        await mgr.refresh_usage()          # active account; refresh_all_usage() for all
        mgr.remove(2)                      # frees the slot (must be done before logging in again)

asyncio.run(main())
```

`Account` (never contains cookies or proxy URLs): `slot`, `is_active`, `email`, `display_name`,
`usage_percent`, `usage` (tier, 5-hour and weekly windows with reset times, AI credits, per-model
quotas), `status`, `has_proxy`, `created_at`, `last_used_at`, `last_limit_hit_at`.

Rules worth knowing:

* `login(slot)` fails fast if the slot is occupied: `remove(slot)` first.
* The first account added becomes active. Removing the active account leaves **no** active account
  until you choose one, so requests never silently move to a different account.
* The same Google account cannot be added to two slots (`DuplicateAccountError`).
* `mgr.generate_content()` follows `set_active()` automatically. Do not keep the object returned by
  `active_client()` across a switch. Use `mgr.client(slot)` for chats and long-lived work.
* A rejected session raises `SessionExpiredError`: `remove(slot)`, then `login(slot)` again.

## CLI

```bash
gemini-webapi-manager login 1            # also: --browser chrome|msedge|chromium, --proxy URL
gemini-webapi-manager list [--json]
gemini-webapi-manager use 2
gemini-webapi-manager usage [SLOT]       # refresh from Gemini and show
gemini-webapi-manager ask "Hello"        # --slot N, --model NAME
gemini-webapi-manager proxy 1 http://host:port
gemini-webapi-manager remove 1 [-y]
```

## Usage numbers

Limits come from `gemini-webapi` itself (`usage_info` / `quotas`): the rolling 5-hour window, the
weekly window and per-model quotas. `usage_percent` is the highest window. Values are snapshots,
refreshed whenever a client starts and on `refresh_usage()`, so `list` works offline but may be
stale. When a request hits the limit, `last_limit_hit_at` is recorded and the client recovers on
the next call.

## Security model

* Cookies exist unencrypted only in memory. `gemini-webapi` writes a plain-JSON cookie cache; while
  a managed client runs, that cache is redirected to a private temp directory that is deleted
  afterwards, and rotated cookies are copied back into the vault. If you export
  `GEMINI_COOKIE_PATH` yourself, it is respected, and then the plain cache is yours to protect.
* The vault is written atomically and guarded by a file lock (safe across processes). On POSIX the
  files are `0600`/`0700`.
* Login runs in an isolated browser context, so your everyday browser session is untouched.
* `remove` forgets the session locally only. To revoke it on Google's side, sign out of the device
  in your Google account security settings.
* This builds on a reverse-engineered, unofficial web client. Use it at your own risk and within
  Google's terms.

## Layout

```
src/gemini_webapi_manager/
  manager.py   AccountManager (public entry point)
  client.py    ManagedClient / ManagedChat (transparent wrapper, cookie + usage sync)
  login.py     Playwright login flow
  storage.py   encrypted, locked, atomic vault     crypto.py  scrypt + AES-GCM
  models.py    Account (public) / StoredAccount     usage.py   usage snapshots
  cache.py     private cookie-cache sandbox         cli.py     command line
tests/         offline tests (fake GeminiClient / fake browser)
```

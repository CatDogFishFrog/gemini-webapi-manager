# gemini-webapi-manager

Python-API та CLI поверх ядра [`gemini-vault`](../gemini-vault): кілька акаунтів Google в зашифрованому сховищі,
перемикання активного акаунта, ліміти й виклики [`gemini-webapi`](https://github.com/HanaokaYuzu/Gemini-API) від імені
обраного акаунта.

## Встановлення

```bash
pip install "gemini-webapi-manager @ git+https://github.com/CatDogFishFrog/gemini-webapi-manager.git#subdirectory=packages/gemini-webapi-manager"
pip install "gemini-webapi-manager[login] @ git+https://github.com/CatDogFishFrog/gemini-webapi-manager.git#subdirectory=packages/gemini-webapi-manager"   # якщо вхід виконується тут
```

Пакет залежить від `gemini-vault>=0.2.0,<0.3`. Extra `[login]` додає Playwright для входу.
Налаштування сховища (`GEMINI_MANAGER_KEY`, `GEMINI_MANAGER_HOME`, `GEMINI_MANAGER_MAX_SLOTS`) описано в
[README ядра](../gemini-vault/README.md).

## Швидкий старт

```python
import asyncio
from gemini_webapi_manager import AccountManager

async def main():
    async with AccountManager() as mgr:                 # ключ з GEMINI_MANAGER_KEY
        await mgr.login(1, method="console", email="me@gmail.com")   # або method="browser"
        mgr.set_active(1)
        reply = await mgr.generate_content("Привіт!")
        print(reply.text)

asyncio.run(main())
```

## `AccountManager`

`AccountManager` — це `gemini_vault.AccountRegistry` (усі його методи доступні) плюс спільний клієнт активного акаунта.

```python
mgr = AccountManager()                              # або AccountManager(home=..., key=..., max_slots=...)
```

| Група | Методи |
| --- | --- |
| Огляд (синхронні) | `accounts()`, `get(slot)`, `active()`, `free_slots()` |
| Слоти | `set_active(slot)`, `remove(slot)`, `await sign_out(slot)`, `set_proxy(slot, url)` |
| Вхід | `await login(slot, method="browser"\|"console", replace=False, ...)`, `login_sync(...)` |
| Ліміти | `await refresh_usage(slot=None)`, `await refresh_all_usage()`, `mark_limit_hit(slot)` |
| Виклики Gemini | `await generate_content(prompt, ...)`, `generate_content_stream(prompt, ...)` — на активному акаунті |
| Клієнти | `await active_client()` — спільний `ManagedClient` активного акаунта; `client(slot=None)` — окремий клієнт слота (`async with`) |
| Життєвий цикл | `await aclose()`; `async with AccountManager() as mgr` |

Спільний клієнт створюється при першому виклику й після кожного перемикання активного акаунта (або заміни акаунта в слоті)
відкривається заново; не зберігайте об'єкт, який повернув `active_client()`, між викликами `set_active`.

```python
# повний API gemini-webapi: чати, файли, Gems
async with mgr.client(2) as client:
    chat = client.start_chat()
    print((await chat.send_message("Привіт")).text)

# потік
async for chunk in mgr.generate_content_stream("Розкажи історію"):
    print(chunk.text_delta, end="")
```

Ліміти: `acc = await mgr.refresh_usage()`; `acc.usage.five_hour.percent`, `acc.usage.weekly`, `acc.usage_percent`
(найвище з відомих вікон). Ротовані cookie зберігаються у сховищі автоматично.

## Командний рядок

```bash
gemini-webapi-manager ask "Привіт" [--slot N] [--model MODEL]
```

`ask` відправляє запит активним (або вказаним) акаунтом і друкує відповідь. Решта команд — спільні з ядром:
`keygen`, `login`, `login-console`, `list`, `use`, `usage`, `remove [--revoke]`, `proxy`, `install-browser`, `doctor`;
їхній опис — у [README ядра](../gemini-vault/README.md#командний-рядок).

## Помилки

Усі винятки успадковують `GeminiManagerError` і експортуються з `gemini_webapi_manager` (список і дії — у
[README ядра](../gemini-vault/README.md#помилки)). Для цього пакета важливі:

- `NoActiveAccountError` — активний акаунт не обрано (`set_active`);
- `SessionExpiredError` — сесія відхилена Google; повторіть вхід: `await mgr.login(slot, replace=True, ...)`;
- `gemini_webapi.UsageLimitExceededError` — вичерпано ліміт акаунта: обгортка записує цей факт і сама відновлює клієнт,
  але запит доведеться повторити (інший акаунт — `set_active`). Автоматичне перемикання за лімітами є у
  [`gemini-web2api-manager`](../gemini-web2api-manager).

## Сумісність зі старими імпортами

Модулі, що колись були частиною цього пакета, тепер живуть у `gemini-vault`, а тут лишилися тонкі перенаправлення, тож
старий код працює без змін: `gemini_webapi_manager.crypto`, `.config`, `.storage`, `.models`, `.usage`, `.errors`,
`.cache`, `.login`, `.client`. Для нового коду використовуйте `gemini_vault.*`. Формат `vault.bin` не змінювався.

## Обмеження

- Один спільний клієнт відповідає одному активному акаунту; для паралельної роботи з кількома акаунтами користуйтеся
  `client(slot)`.
- Перемикання за лімітами в цьому пакеті автоматичного немає.
- Пакет спирається на неофіційний веб-клієнт Gemini: поведінка Google може змінюватися.

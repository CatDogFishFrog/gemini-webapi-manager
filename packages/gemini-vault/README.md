# gemini-vault

Ядро інструментів для Gemini: зашифроване сховище акаунтів Google, слоти, вхід (у вікні браузера або лише з терміналу),
сесії для [gemini-webapi](https://github.com/HanaokaYuzu/Gemini-API), облік лімітів і політика вибору акаунта.
Поверх ядра працюють [`gemini-webapi-manager`](../gemini-webapi-manager) (Python-API) і
[`gemini-web2api-manager`](../gemini-web2api-manager) (OpenAI-шлюз).

## Встановлення

```bash
pip install "gemini-vault[login] @ git+https://github.com/CatDogFishFrog/gemini-webapi-manager.git#subdirectory=packages/gemini-vault"
```

Python 3.11+. Залежності: `gemini-webapi>=2.1.1,<3`, `cryptography`, `filelock`. Extra `[login]` додає Playwright;
він потрібен лише на машині, де виконується вхід. Серверу з готовим `vault.bin` Playwright не потрібен.

## Концепції

| Поняття | Опис |
| --- | --- |
| Сховище | один зашифрований файл `vault.bin` з усіма акаунтами: cookie, проксі, знімок лімітів, час останнього використання |
| Слот | номер `1…N` (типово `N = 10`); в одному слоті — один акаунт, один e-mail — один слот |
| Активний акаунт | слот, який використовують за замовчуванням; перший доданий акаунт стає активним; видалення активного нового не обирає |
| Ключ шифрування | пароль-фраза з `GEMINI_MANAGER_KEY` (щонайменше 16 символів). З її допомогою scrypt виводить ключ AES-256-GCM. Сіль зберігається у файлі, тому `vault.bin` відкривається на будь-якій машині за тим самим ключем |

Файл можна створити на одній машині (наприклад, вхід у вікні на Windows) і скопіювати на сервер.

### Змінні середовища

| Змінна | Призначення | Типово |
| --- | --- | --- |
| `GEMINI_MANAGER_KEY` | ключ шифрування (обов'язковий) | — |
| `GEMINI_MANAGER_HOME` | тека зі `vault.bin` | `~/.gemini-webapi-manager` |
| `GEMINI_MANAGER_MAX_SLOTS` | кількість слотів | `10` |
| `GEMINI_VAULT_BROWSER_PATH` | шлях до Chromium/Chrome для входу | пошук системного браузера |
| `GEMINI_VAULT_DEBUG` | `1` — показувати traceback замість короткого повідомлення про помилку | — |

### Файли й доступ

У теці `GEMINI_MANAGER_HOME` лежать `vault.bin` (права `0600`) і `vault.lock` (файл блокування); сама тека отримує права
`0700`. Запис атомарний: дані пишуться у тимчасовий файл у тій самій теці й підміняють `vault.bin` через `os.replace`, тому
тека має бути доступна на запис. Міжпроцесне блокування (`filelock`) надійне на локальній файловій системі; на мережевих
(NFS/SMB) на нього покладатися не можна. Читання не блокується.

## Командний рядок

```bash
gemini-vault keygen                              # новий випадковий ключ
gemini-vault login 1                             # вхід у вікні браузера
gemini-vault login-console 2 --email me@gmail.com
gemini-vault list [--json]                       # слоти, ліміти, статус (останні відомі дані)
gemini-vault usage [SLOT] [--json]               # оновити ліміти з Gemini
gemini-vault use 2                               # зробити слот активним
gemini-vault proxy 1 http://user:pass@host:3128  # проксі слота; без URL — прибрати
gemini-vault remove 1 [-y] [--revoke]            # видалити; --revoke ще й виходить з акаунта на боці Google
gemini-vault install-browser [--with-deps]       # Chromium від Playwright
gemini-vault doctor                              # перевірка середовища
```

Спільні команди мають також `gemini-webapi-manager` і `gemini-web2api-manager`.

`login` і `login-console` приймають: `--email`, `--proxy`, `--timeout`, `--browser {auto,chrome,msedge,chromium}`,
`--browser-path`, `--headless` (режим вікна), `--headed` (режим консолі), `--no-verify`, `--replace`, `--debug-dir`.
`remove` без `-y` питає підтвердження і відмовляється працювати в неінтерактивній сесії.
Звичайний `remove` лише забуває сесію локально; `--revoke` додатково просить Google завершити її й перевіряє, що cookie
більше не приймаються (результат: «підтверджено» / «запит прийнято, але не підтверджено» / «Google недосяжний»).

## Вхід

| | Вікно (`login`) | Консоль (`login-console`) |
| --- | --- | --- |
| Де | машина з екраном | будь-яка, зокрема сервер без графічної оболонки |
| Як | відкривається браузер, користувач входить сам, програма чекає (до 300 с) | headless-браузер; програма питає в терміналі пароль, метод підтвердження, коди (до 900 с бездіяльності) |
| Браузер | системні Chrome/Edge за замовчуванням | Chromium: системний або від Playwright |

Докладно про консольний вхід, його кроки та діагностику — [docs/console-login.md](../../docs/console-login.md).

Після входу сесію перевіряють у Gemini (пропустити: `--no-verify`), знімок лімітів зберігається разом з акаунтом.
Пароль і коди ніколи не зберігаються й не логуються. Якщо Google відхиляє автоматизований вхід (`LoginBlockedError`),
скористайтеся входом у вікні на звичайному комп'ютері та перенесіть `vault.bin`.

## Python API

```python
from gemini_vault import AccountRegistry

reg = AccountRegistry()                       # GEMINI_MANAGER_KEY / GEMINI_MANAGER_HOME з середовища
# або AccountRegistry(home="/data", key="...", max_slots=5)

await reg.login(1, method="console", email="me@gmail.com")
for acc in reg.accounts():                    # list[Account]; без cookie та URL проксі
    print(acc, acc.usage_percent)

async with reg.client(1) as client:           # ManagedClient: повний API gemini-webapi
    out = await client.generate_content("Привіт")
    chat = client.start_chat()
    print((await chat.send_message("Ще одне запитання")).text)
```

### `AccountRegistry`

| Метод | Що робить |
| --- | --- |
| `accounts()`, `get(slot)`, `active()`, `free_slots()` | огляд (синхронно, без мережі) |
| `set_active(slot)`, `remove(slot)`, `set_proxy(slot, url)` | керування слотами |
| `await sign_out(slot, confirm=True)` | вийти на боці Google, потім видалити слот; повертає `RemoveResult(account, revoke_requested, revoke_confirmed)` |
| `await login(slot, method="browser"\|"console", replace=False, proxy=None, verify=True, email=None, timeout=None, headless=None, browser="auto", executable_path=None, debug_dir=None, on_status=None, io=None)` | вхід; зайнятий слот без `replace=True` дає `SlotOccupiedError`; із `replace=True` акаунт замінюється атомарно, проксі й статус активного зберігаються |
| `login_sync(slot, **kwargs)` | блокуючий варіант для скриптів |
| `client(slot=None, **init_options)` | `ManagedClient` слота (за замовчуванням активного); `init_options` йдуть у `GeminiClient.init` |
| `await refresh_usage(slot=None)`, `await refresh_all_usage()` | оновити ліміти |
| `mark_limit_hit(slot)` | записати, що акаунт уперся в ліміт |
| `settings`, `store` | `Settings` і `VaultStore` (для інтеграцій) |

### `ManagedClient` / `ManagedChat`

Тонка обгортка над `gemini_webapi.GeminiClient`: усе, чого вона не перевизначає, передається клієнту (`raw` — сам клієнт).
Додатково вона записує ротовані cookie назад у сховище (приблизно раз на 5 хвилин і при закритті), оновлює знімок лімітів
після `init` і на вимогу (`refresh_usage()`), веде облік останнього використання та помилок ліміту й прозоро повторно
ініціалізує клієнт після `UsageLimitExceededError`. Використовуйте як `async with` або через `start()`/`close()`.
Якщо слот видалили чи замінили під час роботи, клієнт не воскрешає його запис.

### `selection`

Чиста політика вибору акаунта (без введення-виведення): `pick`, `rank`, `is_eligible`, `remaining_percent`,
`used_percent`, `recovery_time`, `earliest_available`, структура `Candidate`. Правила:

1. Акаунт придатний, якщо сесія дійсна, статус не ворожий (`AVAILABLE`, `ACCOUNT_UNTRUSTED` або невідомий), немає паузи після
   нещодавнього ліміту й використання нижче порога (типово 100 %).
2. Знімок лімітів старіє: вікно, час скидання якого вже минув, вважається порожнім.
3. Серед придатних перемагає акаунт із найбільшим залишком (при рівності — давніше використаний, потім менший слот).
   Із `prefer` (режим sticky) зберігається обраний акаунт, доки він придатний. Залишок рахується по найгіршому вікну:
   5 годин, тиждень і квота моделі потрібної «родини» (`pro`/`flash`).

### Дані акаунта

`Account` — незмінний знімок: `slot`, `is_active`, `email`, `display_name`, `status`, `usage` (`UsageSnapshot`: `tier`,
`five_hour`, `weekly`, `model_quotas`, `ai_credits_remaining`, `updated_at`), `has_proxy`, `created_at`, `last_used_at`,
`last_limit_hit_at`, `usage_percent`; `to_dict()`/`to_json()`. Cookie, паролі й URL проксі в ньому ніколи не з'являються.

## Помилки

Усе, що пакет викликає навмисно, успадковує `GeminiManagerError`.

| Виняток | Коли | Що робити |
| --- | --- | --- |
| `MissingKeyError`, `WeakKeyError`, `ConfigError` | ключ не задано / коротший за 16 символів / хибна конфігурація | задайте `GEMINI_MANAGER_KEY` (`gemini-vault keygen`) |
| `InvalidKeyError` | файл не розшифровується: хибний ключ або файл змінено | використайте ключ, яким створено файл |
| `VaultCorruptedError` | файл не схожий на сховище або має невідому версію | відновіть із резервної копії |
| `SlotError`, `SlotOccupiedError`, `SlotEmptyError` | хибний номер слота / слот зайнятий / слот порожній | `remove`, `--replace` або інший слот |
| `NoActiveAccountError` | активний акаунт не обрано | `use SLOT` |
| `DuplicateAccountError` | e-mail уже підключено в іншому слоті | видаліть дублікат |
| `SessionExpiredError` | Google відхилив збережену сесію | повторний вхід у той самий слот: `login … --replace` |
| `LoginError` і похідні (`LoginTimeoutError`, `LoginAbortedError`, `LoginBlockedError`, `LoginChallengeError`) | вхід не завершився | див. [docs/console-login.md](../../docs/console-login.md) |
| `ClientNotStartedError` | `ManagedClient` використано без `async with`/`start()` | запустіть клієнт |

## Безпека й обмеження

- Ключ відкриває усе сховище; зберігайте його окремо від `vault.bin` і робіть його резервну копію.
- Сесії лежать у файлі зашифрованими, але в пам'яті працюючого процесу — відкритим текстом.
- Для швидкого оновлення лімітів пакет викликає приватні методи `gemini-webapi`; якщо вони зникнуть, працює повільніший
  запасний шлях (перевірка — `gemini-vault doctor`).
- Автоматичного відновлення прострочених сесій немає: потрібен повторний вхід людини (пароль, 2FA).
- Автоматизований вхід може бути відхилений Google; CAPTCHA розв'язується вручну.

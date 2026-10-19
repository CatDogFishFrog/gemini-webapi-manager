# Gemini accounts

Інструменти навколо неофіційного веб-клієнта Gemini: кілька акаунтів Google в одному зашифрованому сховищі,
вхід без ручного копіювання cookie, облік лімітів і OpenAI-сумісний шлюз, що сам переходить на інший акаунт,
коли в поточного закінчується ліміт.

| Пакет | Що це |
| --- | --- |
| [`gemini-vault`](packages/gemini-vault) | **Ядро.** Зашифроване сховище `vault.bin`, слоти, вхід у Google (у вікні браузера або лише з терміналу), сесії, ліміти, політика вибору акаунта, CLI `gemini-vault` |
| [`gemini-webapi-manager`](packages/gemini-webapi-manager) | Python-API `AccountManager` для [gemini-webapi](https://github.com/HanaokaYuzu/Gemini-API): активний акаунт, `generate_content`, чати, ліміти |
| [`gemini-web2api-manager`](packages/gemini-web2api-manager) | OpenAI-сумісний шлюз на базі [gemini-web2api](https://github.com/Sophomoresty/gemini-web2api) з пулом акаунтів і автоперемиканням |

## Можливості

- **Кілька акаунтів у одному файлі.** Слоти `1…N`, активний акаунт, окреме проксі для кожного слота. Файл шифрується
  (AES-256-GCM, ключ виводиться через scrypt) і однаково відкривається на будь-якій машині за тим самим ключем.
- **Вхід двома способами.** У вікні браузера або повністю з терміналу — для серверів без графічної оболонки: пароль,
  вибір методу підтвердження, код з Authenticator/SMS, підтвердження на телефоні.
- **Ліміти.** Залишок 5-годинного та тижневого вікон і квоти моделей (`pro`/`flash`) для кожного акаунта.
- **Автоперемикання.** Шлюз тримається поточного акаунта, а коли ліміт вичерпано, повторює той самий запит на акаунті
  з найбільшим залишком; клієнт цього не помічає.
- **Сервер.** Скрипти керування для Windows і Linux, образ Docker, unit для systemd — див. [`deploy/`](deploy).

## Встановлення

Корінь репозиторію не є Python-пакетом: кожен пакет ставиться зі своєї підтеки.

```bash
REPO=https://github.com/CatDogFishFrog/gemini-webapi-manager.git
pip install "gemini-vault[login] @ git+$REPO#subdirectory=packages/gemini-vault"
pip install "gemini-webapi-manager @ git+$REPO#subdirectory=packages/gemini-webapi-manager"
pip install "gemini-web2api-manager @ git+$REPO#subdirectory=packages/gemini-web2api-manager"
```

`[login]` додає Patchright — він потрібен лише там, де відбувається вхід. Серверу з готовим `vault.bin` він не потрібен.
Вимоги: Python 3.11+.

Імпорти `gemini_webapi_manager.*`, що існували до розділення на пакети (`crypto`, `config`, `client`, `login` тощо), працюють
як і раніше; формат `vault.bin` не змінювався. Деталі — у [CHANGELOG](CHANGELOG.md).

## Швидкий старт

**Сервер без графічної оболонки (Linux):**

```bash
git clone https://github.com/CatDogFishFrog/gemini-webapi-manager.git && cd gemini-webapi-manager
./scripts/manage-vault.sh        # створить venv, поставить пакети, згенерує ключ, покаже меню
```

**Windows (вхід у вікні браузера):** `scripts\manage-vault.ps1` у PowerShell 7 — меню, інструкції та відправлення
`vault.bin` на сервер.

**З командного рядка:**

```bash
export GEMINI_MANAGER_KEY="$(gemini-vault keygen)"      # збережіть ключ: без нього vault.bin не відкрити
gemini-vault login-console 1 --email me@gmail.com       # або: gemini-vault login 1
gemini-vault list
gemini-vault usage
```

**З Python:**

```python
from gemini_webapi_manager import AccountManager

async with AccountManager() as mgr:                      # ключ береться з GEMINI_MANAGER_KEY
    print((await mgr.generate_content("Привіт")).text)
```

**Шлюз OpenAI API:**

```bash
gemini-web2api-manager fetch-upstream
gemini-web2api-manager serve --port 8080 --api-key sk-me
```

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="sk-me")
print(client.chat.completions.create(model="gemini-3.6-flash", messages=[{"role": "user", "content": "Привіт"}]))
```

## Документація

- [`gemini-vault`](packages/gemini-vault/README.md) — сховище, слоти, ключ, CLI, вхід, API, помилки;
  [вхід з консолі: сценарії та діагностика](docs/console-login.md)
- [`gemini-webapi-manager`](packages/gemini-webapi-manager/README.md) — `AccountManager`, CLI, сумісність
- [`gemini-web2api-manager`](packages/gemini-web2api-manager/README.md) — шлюз, конфіг, правила перемикання, адмін-ендпоінти
- [`deploy/`](deploy/README.md) — Docker, systemd, експлуатація сховища на сервері
- [`example/`](example) — інтерактивний консольний приклад

## Безпека й обмеження

- Ключ `GEMINI_MANAGER_KEY` відкриває весь `vault.bin`. Не додавайте `.env` і `vault.bin` до git (див. `.gitignore`).
- Пароль і коди підтвердження не зберігаються й не потрапляють у журнали.
- Шлюз не слід відкривати назовні без `api_keys`.
- Google може відхилити автоматизований вхід (CAPTCHA, «цей браузер може бути небезпечним»), особливо з IP дата-центру.
  Тоді увійдіть на звичайному комп'ютері й перенесіть `vault.bin` на сервер.
- Це неофіційні клієнти веб-версії Gemini; їх використання може суперечити умовам Google. Краще користуватися
  окремим тестовим акаунтом.

## Розробка

```bash
pip install -e "packages/gemini-vault[dev]" -e packages/gemini-webapi-manager -e packages/gemini-web2api-manager httpx
gemini-web2api-manager fetch-upstream
GEMINI_VAULT_BROWSER_PATH=/path/to/chromium python -m pytest -q   # без змінної браузерні тести пропускаються
```

Ліцензія — [MIT](LICENSE).

# Журнал змін

## 0.2.0 — розділення на пакети

Репозиторій став монорепозиторієм із трьома пакетами (`packages/`).

**Що змінилося для користувачів**

- Корінь репозиторію більше не є Python-пакетом, тому
  `pip install git+https://github.com/CatDogFishFrog/gemini-webapi-manager.git` не працює. Встановлюйте потрібний пакет
  із підтеки: `pip install "gemini-vault[login] @ git+<repo>.git#subdirectory=packages/gemini-vault"`
  (так само `gemini-webapi-manager` і `gemini-web2api-manager`).
- Модулі сховища, входу й сесій переїхали в пакет `gemini-vault` (`gemini_vault.crypto`, `.config`, `.storage`, `.models`,
  `.usage`, `.cache`, `.errors`, `.sessions`, `.login`). Старі імпорти `gemini_webapi_manager.crypto`, `.config`, `.storage`,
  `.models`, `.usage`, `.errors`, `.cache`, `.login`, `.client` працюють і далі.
- Формат `vault.bin` і змінні `GEMINI_MANAGER_KEY`, `GEMINI_MANAGER_HOME`, `GEMINI_MANAGER_MAX_SLOTS` не змінилися;
  теку за замовчуванням `~/.gemini-webapi-manager` збережено.

**Нове**

- Вхід з терміналу (`gemini-vault login-console`) — без вікна браузера й X-сервера.
- `AccountManager`/`AccountRegistry`: повторний вхід у слот (`replace=True`), вихід на стороні Google (`sign_out`, `remove --revoke`).
- Пакет `gemini-web2api-manager`: OpenAI-сумісний шлюз з пулом акаунтів і автоперемиканням.
- Скрипт `scripts/manage-vault.sh` для Linux, каталог `deploy/` (Docker, systemd), перевірка `tools/live_smoke.py`.

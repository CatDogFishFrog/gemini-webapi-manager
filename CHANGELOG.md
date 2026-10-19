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
- `gemini-vault rekey` / `AccountRegistry.rekey`: зміна ключа шифрування без повторного входу; змінна `GEMINI_MANAGER_KEY_FILE` для ключа з файла-секрета.
- Шлюз підхоплює заміну акаунта в слоті й зміну проксі, коректно зупиняється за `SIGTERM`, типово слухає лише `127.0.0.1:8080`.
- `deploy/` самодостатній: `docker compose up` із теки `deploy/`, дані в іменованому томі, налаштування змінними середовища (`GEMINI_GATEWAY_API_KEYS`, `GEMINI_GATEWAY_CONFIG`, `GEMINI_GATEWAY_ALLOW_EMPTY`), окремий сервіс `vault-admin` з браузером для входу в акаунти; шлюз може стартувати без акаунтів (`--allow-empty`).
- Виправлено вхід з консолі: заголовок сторінки («Welcome») більше не вважається помилкою (раніше передчасно спрацьовував захист «забагато спроб»), а введення йде у видиме поле, а не в приховане з тим самим id.
- Скрипт `scripts/manage-vault.sh` для Linux, каталог `deploy/` (Docker, systemd), перевірка `tools/live_smoke.py`.

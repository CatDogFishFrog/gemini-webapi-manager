# Шлюз Gemini у Docker

Ця тека **самодостатня**: усе, що потрібно для запуску, лежить тут. Нічого з батьківських тек (`scripts/` тощо) під час
роботи не використовується; дані зберігаються в іменованому томі Docker, а не в теках хоста.

Потрібні лише Docker з Compose v2 (`docker compose`) і доступ до Інтернету під час збірки (завантажуються образ Python, пакети
та код апстріму `gemini-web2api`). Контекстом збірки є корінь репозиторію, бо звідти копіюються пакети; після збірки
репозиторій для роботи не потрібен.

Усі команди нижче виконуються **в цій теці** (`deploy/`).

## Швидкий старт

```bash
cp .env.example .env
docker compose run --rm --no-deps --entrypoint gemini-vault gateway keygen     # згенерує ключ шифрування
nano .env                                                                       # впишіть ключ і ключі клієнтів
docker compose up -d --build
```

У `.env` потрібні:

| Змінна | Призначення |
| --- | --- |
| `GEMINI_MANAGER_KEY` | ключ шифрування `vault.bin` (мін. 16 символів). **Збережіть окремо**: без нього файл не відкрити |
| `GEMINI_GATEWAY_API_KEYS` | ключі клієнтів шлюзу (`Authorization: Bearer …`), через кому. Порожнє значення — шлюз без автентифікації |
| `GATEWAY_BIND`, `GATEWAY_PORT` | необов'язково: на якому інтерфейсі й порту **хоста** опублікувати шлюз (типово `127.0.0.1` і `8080`); змініть, якщо порт зайнятий |

Шлюз стартує навіть без акаунтів: до додавання першого він відповідає `503`. Додайте акаунт одним зі способів нижче — він
з'явиться у роботі за кілька секунд, без перезапуску.

## Додавання акаунтів

### А. Вхід просто в контейнері (без браузера на хості)

```bash
docker compose run --rm vault-admin login-console 1 --email you@gmail.com
```

Перший запуск збирає образ `vault-admin` з Chromium (кілька сотень МБ). Програма питає в терміналі пароль і код підтвердження.
Google може відхиляти автоматизований вхід з IP дата-центру; тоді скористайтеся способом Б. Допомога по діагностиці —
[`docs/console-login.md`](../docs/console-login.md) у репозиторії.

### Б. Перенести готовий `vault.bin`

Файл, створений деінде тим самим ключем (`GEMINI_MANAGER_KEY`), кладеться в том:

```bash
chmod a+r vault.bin        # контейнер працює від іншого користувача; після копіювання файл можна видалити
docker compose run --rm --no-deps -v "$(pwd)/vault.bin:/import/vault.bin:ro" --entrypoint sh gateway \
  -c 'cp /import/vault.bin /data/vault.bin && chmod 600 /data/vault.bin'
```

Ключ у `.env` має збігатися з тим, яким зашифровано файл.

## Щоденна робота

| Що | Команда |
| --- | --- |
| Стан пулу | `curl -H "Authorization: Bearer <ключ>" http://127.0.0.1:8080/_pool/status` (порт — `GATEWAY_PORT`) |
| Журнал | `docker compose logs -f gateway` |
| Зупинка / запуск | `docker compose stop` / `docker compose up -d` |
| Список акаунтів і ліміти | `docker compose run --rm --entrypoint gemini-vault gateway list` ; `… usage` |
| Додати/замінити акаунт | `docker compose run --rm vault-admin login-console N [--replace]` — шлюз підхоплює зміну без перезапуску |
| Видалити акаунт | `docker compose run --rm --entrypoint gemini-vault gateway remove N` |
| Оновлення | `git pull && docker compose up -d --build` (том із даними не чіпається) |
| Усе видалити разом із даними | `docker compose down -v` |

Клієнт: `OpenAI(base_url="http://<хост>:<GATEWAY_PORT>/v1", api_key="<ключ з GEMINI_GATEWAY_API_KEYS>")`.
Порт опубліковано лише на `127.0.0.1`; щоб відкрити його назовні, змініть `GATEWAY_BIND` і поставте перед ним зворотний
проксі з TLS. Не вимикайте ключі клієнтів.

## Зміна ключа шифрування

Працюючий шлюз тримає старий ключ у пам'яті, тому порядок завжди такий:

```bash
docker compose stop gateway
docker compose run --rm --entrypoint gemini-vault gateway rekey --generate     # друкує новий ключ; поруч у томі лишається копія vault.bin.bak.<час>
nano .env                                                                       # замініть GEMINI_MANAGER_KEY на новий ключ
docker compose up -d
```

Акаунти заново входити не потрібно. Стару копію `vault.bin.bak.*` видаліть, коли переконаєтеся, що все працює.

## Резервна копія даних

```bash
docker run --rm -v gemini-gateway_gemini-data:/data -v "$(pwd)":/backup alpine tar czf /backup/gemini-data.tgz -C /data .
```

Назва тому — `<name з compose>_gemini-data` (`docker volume ls`). Зберігайте архів окремо від ключа.

## Як це влаштовано

- **Том `gemini-data`** змонтований у `/data`; там лежать `vault.bin` і файл блокування. Шлюз **пише** в нього (ротовані cookie,
  ліміти, активний слот), тому том не можна монтувати лише на читання. Образ створює `/data` для користувача `gemini`
  (uid 1000), тож іменований том одразу отримує правильного власника.
- **Ключі й порт** передаються змінними середовища, а не аргументами команди (аргументи видно в `docker inspect`). Строгіший
  варіант — файл-секрет: змонтуйте файл з ключем і задайте `GEMINI_MANAGER_KEY_FILE=/шлях/до/файла` замість `GEMINI_MANAGER_KEY`.
- **Додаткові налаштування шлюзу** (стратегія вибору акаунта, паузи, моделі) — у JSON-конфігу, як описано в
  [README пакета](../packages/gemini-web2api-manager/README.md#конфігурація): змонтуйте теку з файлом у `gateway` і задайте
  `GEMINI_GATEWAY_CONFIG=/шлях/до/gateway.config.json`.
- **Зупинка.** `docker compose stop` надсилає `SIGTERM`; шлюз закриває сесії, записує cookie й завершується з кодом 0
  (`stop_grace_period: 40s`).

## Без Docker

`gemini-gateway.service` — приклад unit-файла systemd із власними шляхами та інструкцією встановлення в коментарях; змінні ті
самі (`GEMINI_MANAGER_KEY`, `GEMINI_GATEWAY_API_KEYS`).

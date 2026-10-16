#!/usr/bin/env bash
# =============================================================================
# manage-vault.sh — керування сховищем акаунтів Gemini на Linux-сервері БЕЗ графічного інтерфейсу.
# Аналог scripts/manage-vault.ps1, але вхід в акаунт Google відбувається лише через термінал
# (e-mail, пароль, вибір методу підтвердження, код) у headless-браузері.
#
# Інтерактивне меню:   ./manage-vault.sh
# Пряма команда:       ./manage-vault.sh login-console 1 --email me@gmail.com
#                      ./manage-vault.sh list | usage | status | serve | ...
#                      (будь-яка команда gemini-web2api-manager; див. `./manage-vault.sh --help`)
#
# Змінні середовища (необов'язково):
#   GVM_VENV        тека віртуального середовища   (типово: <скрипт>/.venv-gemini-vault)
#   GVM_VAULT_DIR   тека зі сховищем vault.bin     (типово: <скрипт>/vault)
#   GVM_PYTHON      інтерпретатор Python >= 3.11   (типово: python3)
#   GEMINI_VAULT_BROWSER_PATH  шлях до Chromium/Chrome (якщо не ставити браузер Playwright)
# =============================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
VENV_DIR="${GVM_VENV:-$SCRIPT_DIR/.venv-gemini-vault}"
VAULT_DIR="${GVM_VAULT_DIR:-$SCRIPT_DIR/vault}"
VAULT_FILE="$VAULT_DIR/vault.bin"
PYTHON_BIN="${GVM_PYTHON:-python3}"
GATEWAY_CFG="$SCRIPT_DIR/gateway.config.json"
GITHUB_URL="https://github.com/CatDogFishFrog/gemini-webapi-manager"

# Тека репозиторію з packages/: або батьківська (скрипт у scripts/), або клон поруч.
REPO_DIR=""
if [ -d "$SCRIPT_DIR/../packages/gemini-vault" ]; then
    REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
elif [ -d "$SCRIPT_DIR/gemini-webapi-manager/packages/gemini-vault" ]; then
    REPO_DIR="$SCRIPT_DIR/gemini-webapi-manager"
fi

# ----------------------------------------------------------------------------- вивід
if [ -t 1 ]; then
    C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_CYAN=$'\033[36m'; C_DIM=$'\033[90m'; C_OFF=$'\033[0m'
else
    C_RED=""; C_GREEN=""; C_YELLOW=""; C_CYAN=""; C_DIM=""; C_OFF=""
fi
header()  { printf '\n%s== %s ==%s\n\n' "$C_CYAN" "$1" "$C_OFF"; }
ok()      { printf '%s%s%s\n' "$C_GREEN" "$1" "$C_OFF"; }
warn()    { printf '%s[!] %s%s\n' "$C_YELLOW" "$1" "$C_OFF" >&2; }
err()     { printf '%s[x] %s%s\n' "$C_RED" "$1" "$C_OFF" >&2; }
dim()     { printf '%s%s%s\n' "$C_DIM" "$1" "$C_OFF"; }
pause()   { [ -t 0 ] && { printf '\n'; read -r -p "Натисніть Enter, щоб продовжити..." _; }; }
ask()     { local prompt="$1" default="${2:-}" reply; read -r -p "$prompt" reply; printf '%s' "${reply:-$default}"; }
confirm() { local reply; read -r -p "$1 [y/N] " reply; [[ "$reply" =~ ^[YyТт]$ ]]; }

# ----------------------------------------------------------------------------- .env
load_env() {
    [ -f "$ENV_FILE" ] || return 0
    local line key value
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
        key="${BASH_REMATCH[1]}"; value="${BASH_REMATCH[2]}"
        value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
        export "$key=$value"   # без eval: значення не виконується як код
    done < "$ENV_FILE"
}

set_env_var() {   # set_env_var KEY VALUE — замінити або додати рядок у .env
    local key="$1" value="$2" tmp
    touch "$ENV_FILE"; chmod 600 "$ENV_FILE"
    tmp="$(mktemp)"
    grep -v "^${key}=" "$ENV_FILE" > "$tmp" || true
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
    cat "$tmp" > "$ENV_FILE"; rm -f "$tmp"
    chmod 600 "$ENV_FILE"
}

# ----------------------------------------------------------------------------- середовище
venv_python() { printf '%s/bin/python' "$VENV_DIR"; }
cli() { "$(venv_python)" -m gemini_web2api_manager "$@"; }

check_python() {
    if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
        err "Не знайдено '$PYTHON_BIN'. Встановіть Python 3.11+ (наприклад: sudo apt install python3 python3-venv)."
        return 1
    fi
    if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
        err "Потрібен Python 3.11+, а знайдено: $("$PYTHON_BIN" --version 2>&1)."
        return 1
    fi
}

install_packages() {
    local py; py="$(venv_python)"
    if [ -n "$REPO_DIR" ]; then
        dim "Встановлення з локальної теки $REPO_DIR (режим розробки, -e)..."
        "$py" -m pip install -q -e "$REPO_DIR/packages/gemini-vault[login]" \
            -e "$REPO_DIR/packages/gemini-webapi-manager" \
            -e "$REPO_DIR/packages/gemini-web2api-manager" || return 1
    else
        command -v git >/dev/null 2>&1 || { err "Потрібен git (встановлення підпапок монорепозиторію з GitHub)."; return 1; }
        dim "Встановлення з GitHub: $GITHUB_URL ..."
        "$py" -m pip install -q --upgrade \
            "gemini-vault[login] @ git+${GITHUB_URL}.git#subdirectory=packages/gemini-vault" \
            "gemini-webapi-manager @ git+${GITHUB_URL}.git#subdirectory=packages/gemini-webapi-manager" \
            "gemini-web2api-manager @ git+${GITHUB_URL}.git#subdirectory=packages/gemini-web2api-manager" || return 1
    fi
}

ensure_environment() {
    check_python || return 1
    if [ ! -x "$(venv_python)" ]; then
        dim "Створення віртуального середовища: $VENV_DIR"
        "$PYTHON_BIN" -m venv "$VENV_DIR" || { err "Не вдалося створити venv (sudo apt install python3-venv?)."; return 1; }
        "$(venv_python)" -m pip install -q --upgrade pip
    fi
    if ! "$(venv_python)" -c 'import gemini_vault, gemini_web2api_manager' 2>/dev/null; then
        install_packages || { err "Встановлення пакетів не вдалося."; return 1; }
    fi
    mkdir -p "$VAULT_DIR"; chmod 700 "$VAULT_DIR"
    load_env
    export GEMINI_MANAGER_HOME="$VAULT_DIR"
    ensure_key
}

ensure_key() {
    [ -n "${GEMINI_MANAGER_KEY:-}" ] && return 0
    warn "Ключ шифрування GEMINI_MANAGER_KEY не знайдено в $ENV_FILE."
    if [ -f "$VAULT_FILE" ]; then
        warn "Сховище вже існує: без його ключа воно нечитне. Вставте наявний ключ (або Ctrl+C)."
        local key; read -r -s -p "Ключ: " key; printf '\n'
        [ -n "$key" ] || { err "Порожній ключ."; return 1; }
        set_env_var GEMINI_MANAGER_KEY "$key"
    elif confirm "Згенерувати новий ключ і записати в .env?"; then
        local key; key="$("$(venv_python)" -m gemini_vault keygen 2>/dev/null)"
        [ -n "$key" ] || { err "Не вдалося згенерувати ключ."; return 1; }
        set_env_var GEMINI_MANAGER_KEY "$key"
        ok "Ключ збережено в $ENV_FILE (права 600). Зробіть його резервну копію: без нього vault.bin не відкрити."
    else
        return 1
    fi
    load_env
}

# ----------------------------------------------------------------------------- дії меню
act_list()  { header "АКАУНТИ У СХОВИЩІ"; dim "Сховище: $VAULT_FILE"; echo; cli list; }
act_usage() {
    header "ЛІМІТИ (оновлення з Gemini)"
    local slot; slot="$(ask "Номер слота (Enter — усі): ")"
    if [ -n "$slot" ]; then cli usage "$slot"; else cli usage; fi
}

act_login_console() {
    header "ДОДАВАННЯ АКАУНТА — ВХІД ЧЕРЕЗ КОНСОЛЬ"
    local slot; slot="$(ask "Номер слота (1-10): ")"
    [[ "$slot" =~ ^[0-9]+$ ]] || { err "Номер слота має бути числом."; return; }
    local args=(login-console "$slot")
    local email; email="$(ask "E-mail акаунта Google (Enter — запитати пізніше): ")"
    [ -n "$email" ] && args+=(--email "$email")
    local proxy; proxy="$(ask "URL проксі (Enter — без проксі): ")"
    [ -n "$proxy" ] && args+=(--proxy "$proxy")
    if [ -z "${GEMINI_VAULT_BROWSER_PATH:-}" ] && ! command -v chromium >/dev/null 2>&1 \
       && ! command -v google-chrome >/dev/null 2>&1 && ! command -v chromium-browser >/dev/null 2>&1; then
        warn "Системного Chrome/Chromium не знайдено. Потрібен браузер Playwright (пункт 10 меню)."
    fi
    if confirm "Замінити акаунт, якщо слот зайнятий (зберігає проксі й статус активного)?"; then args+=(--replace); fi
    if confirm "Зберігати діагностику кроків (знімки + опис сторінок) у ./gemini-login-debug?"; then
        args+=(--debug-dir "$PWD/gemini-login-debug")
    fi
    echo
    dim "Зараз програма питатиме: пароль (ввід прихований), метод підтвердження та код."
    dim "На кожному кроці '!' = інший метод; 'q' у списку = скасувати."
    echo
    cli "${args[@]}"
}

act_use()    { header "ЗМІНА АКТИВНОГО СЛОТА"; cli list; echo; local s; s="$(ask "Номер слота: ")"; [ -n "$s" ] && cli use "$s"; }
act_remove() {
    header "ВИДАЛЕННЯ АКАУНТА"; cli list; echo
    local s; s="$(ask "Номер слота для видалення: ")"; [ -n "$s" ] || return
    if confirm "Також вийти з акаунта на стороні Google (інакше сесія лишиться дійсною)?"; then
        cli remove "$s" --revoke
    else
        cli remove "$s"
    fi
}
act_proxy()  { header "ПРОКСІ ДЛЯ СЛОТА"; cli list; echo; local s u; s="$(ask "Номер слота: ")"; [ -n "$s" ] || return; u="$(ask "URL проксі (Enter — прибрати): ")"; if [ -n "$u" ]; then cli proxy "$s" "$u"; else cli proxy "$s"; fi; }

gateway_running() { pgrep -f "gemini[-_]web2api[-_]manager.* serve" >/dev/null 2>&1; }

rekey_vault() {   # rekey_vault generate | own — перевести vault.bin на інший ключ
    if gateway_running; then
        warn "Схоже, шлюз запущено. Зі старим ключем він не зможе читати перешифроване сховище і перестане зберігати cookie."
        warn "Зупиніть його (а для контейнера — docker compose stop) і повторіть."
        confirm "Все одно продовжити?" || return
    fi
    local new_key=""
    if [ "$1" = "generate" ]; then
        new_key="$(cli rekey --generate)" || { err "Перешифрування не вдалося, сховище не змінено."; return 1; }
        ok "Новий ключ: $new_key"
        dim "Збережіть його: без нього vault.bin не відкрити."
    else
        local k; read -r -s -p "Новий ключ (мін. 16 символів): " k; printf '\n'
        [ -n "$k" ] || { err "Порожній ключ."; return 1; }
        GVM_NEW_KEY="$k" cli rekey --new-key-env GVM_NEW_KEY || { err "Перешифрування не вдалося, сховище не змінено."; return 1; }
        new_key="$k"
    fi
    local reply; read -r -p "Записати новий ключ у $ENV_FILE? [Y/n] " reply
    if [[ "$reply" =~ ^[NnНн]$ ]]; then
        warn "Ключ у .env НЕ оновлено: інструменти не відкриють сховище, доки GEMINI_MANAGER_KEY не зміниться."
    else
        set_env_var GEMINI_MANAGER_KEY "$new_key"; load_env; ok "Ключ записано в $ENV_FILE (права 600)."
    fi
}

act_key() {
    header "КЛЮЧ ШИФРУВАННЯ"
    local masked="(не задано)"
    [ -n "${GEMINI_MANAGER_KEY:-}" ] && masked="${GEMINI_MANAGER_KEY:0:4}…${GEMINI_MANAGER_KEY: -4} (довжина ${#GEMINI_MANAGER_KEY})"
    echo "Поточний ключ: $masked"
    echo "1. Показати ключ повністю"
    echo "2. Вказати ключ, яким зашифровано vault.bin (наприклад, той самий, що на Windows) — записати в .env"
    if [ -f "$VAULT_FILE" ]; then
        echo "3. Перевести vault.bin на інший ключ: згенерувати новий (акаунти зберігаються)"
        echo "4. Перевести vault.bin на інший ключ: ввести власний (акаунти зберігаються)"
    else
        echo "3. Згенерувати новий ключ (vault.bin ще немає)"
    fi
    echo "0. Назад"
    case "$(ask "Вибір: ")" in
        1) printf '%s\n' "${GEMINI_MANAGER_KEY:-}";;
        2) local k; read -r -s -p "Ключ: " k; printf '\n'; [ -n "$k" ] && { set_env_var GEMINI_MANAGER_KEY "$k"; load_env; ok "Збережено."; };;
        3) if [ -f "$VAULT_FILE" ]; then rekey_vault generate
           else set_env_var GEMINI_MANAGER_KEY "$("$(venv_python)" -m gemini_vault keygen 2>/dev/null)"; load_env; ok "Новий ключ збережено."; fi;;
        4) [ -f "$VAULT_FILE" ] && rekey_vault own;;
    esac
}

act_import_vault() {
    header "ІМПОРТ vault.bin З ІНШОЇ МАШИНИ"
    dim "Наприклад, після входу на Windows: scp vault.bin user@server:/шлях/до/файлу"
    dim "Ключ у .env на сервері має збігатися з ключем, яким файл зашифровано."
    local src; src="$(ask "Шлях до vault.bin: ")"
    [ -f "$src" ] || { err "Файл не знайдено: $src"; return; }
    if [ -f "$VAULT_FILE" ]; then
        local backup="$VAULT_FILE.bak.$(date +%Y%m%d-%H%M%S)"
        cp -p "$VAULT_FILE" "$backup" && dim "Старе сховище збережено: $backup"
    fi
    install -m 600 "$src" "$VAULT_FILE" && ok "Імпортовано в $VAULT_FILE"
    echo; cli list
}

act_gateway() {
    header "ШЛЮЗ gemini-web2api (OpenAI API з автоперемиканням акаунтів)"
    echo "1. Запустити шлюз (на передньому плані, Ctrl+C — зупинити)"
    echo "2. Показати ліміти й чергу вибору (status)"
    echo "3. Завантажити/оновити код gemini-web2api (перевірена версія)"
    echo "0. Назад"
    case "$(ask "Вибір: ")" in
        1)
            cli fetch-upstream >/dev/null 2>&1 || warn "Не вдалося завантажити апстрім (можливо, він уже є через GEMINI_WEB2API_PATH)."
            local args=(serve); [ -f "$GATEWAY_CFG" ] && args+=(--config "$GATEWAY_CFG")
            [ -f "$GATEWAY_CFG" ] || dim "Конфіг $GATEWAY_CFG не знайдено — типові налаштування (127.0.0.1:8080, без ключів)."
            local port; port="$(ask "Порт (Enter — з конфігу/типовий): ")"; [ -n "$port" ] && args+=(--port "$port")
            cli "${args[@]}";;
        2) cli status;;
        3) cli fetch-upstream;;
    esac
}

act_install_browser() {
    header "БРАУЗЕР ДЛЯ ВХОДУ"
    echo "Варіанти: (a) системний Chromium/Chrome (задайте GEMINI_VAULT_BROWSER_PATH),"
    echo "          (b) Chromium від Playwright (~150 МБ) разом із системними бібліотеками (потрібен root/sudo)."
    if confirm "Встановити Chromium Playwright разом із залежностями (--with-deps)?"; then
        if [ "$(id -u)" -eq 0 ]; then cli install-browser --with-deps
        else sudo -E env "PATH=$PATH" "$(venv_python)" -m gemini_vault install-browser --with-deps; fi
    elif confirm "Лише завантажити Chromium без системних бібліотек?"; then
        cli install-browser
    fi
}

act_update()  { header "ОНОВЛЕННЯ ПАКЕТІВ"; if [ -n "$REPO_DIR" ] && [ -d "$REPO_DIR/.git" ] && confirm "Виконати git pull у $REPO_DIR?"; then git -C "$REPO_DIR" pull --ff-only; fi; install_packages && ok "Готово."; }
act_doctor()  { header "ДІАГНОСТИКА СЕРЕДОВИЩА"; cli doctor; }

# ----------------------------------------------------------------------------- меню
menu() {
    while true; do
        header "GEMINI VAULT — СЕРВЕР (без GUI)"
        dim "Сховище: $VAULT_FILE"
        cat <<'MENU'
 1. Список акаунтів
 2. Додати акаунт (вхід через консоль)
 3. Змінити активний слот
 4. Ліміти (оновити з Gemini)
 5. Видалити акаунт
 6. Проксі для слота
 7. Ключ шифрування
 8. Імпорт vault.bin з іншої машини
 9. Шлюз gemini-web2api (запуск / статус)
10. Браузер для входу (встановлення)
11. Оновити пакети
12. Діагностика середовища
 0. Вихід
MENU
        local choice; choice="$(ask $'\nВибір: ')"
        case "$choice" in
            1) act_list;; 2) act_login_console;; 3) act_use;; 4) act_usage;; 5) act_remove;;
            6) act_proxy;; 7) act_key;; 8) act_import_vault;; 9) act_gateway;; 10) act_install_browser;;
            11) act_update;; 12) act_doctor;; 0|q|Q) return 0;;
            *) warn "Невірний вибір.";;
        esac
        pause
    done
}

usage() {
    sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

main() {
    case "${1:-}" in -h|--help|help) usage; return 0;; esac
    ensure_environment || return 1
    if [ $# -gt 0 ]; then cli "$@"; return $?; fi   # пряма команда без меню
    menu
}

main "$@"

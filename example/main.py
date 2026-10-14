"""Інтерактивний приклад використання gemini-webapi-manager.

Демонстрація всіх функцій бібліотеки:
- Керування зашифрованим сховищем (vault.bin)
- Авторизація через браузер (login)
- Перемикання активного облікового запису
- Моніторинг та оновлення лімітів використання
- Одноразові запити та діалоговий чат із пам'яттю
- Налаштування проксі
- Генерація ключа шифрування
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Завантажуємо .env файл із поточної директорії прикладу
try:
    from dotenv import load_dotenv

    env_file = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=env_file)
except ImportError:
    pass

# Якщо GEMINI_MANAGER_HOME не задано, за замовчуванням зберігаємо сховище у ./data
if not os.environ.get("GEMINI_MANAGER_HOME"):
    default_home = Path(__file__).resolve().parent / "data"
    default_home.mkdir(parents=True, exist_ok=True)
    os.environ["GEMINI_MANAGER_HOME"] = str(default_home)

from gemini_webapi_manager import (
    Account,
    AccountManager,
    DuplicateAccountError,
    GeminiManagerError,
    InvalidKeyError,
    LoginError,
    MissingKeyError,
    NoActiveAccountError,
    SessionExpiredError,
    SlotEmptyError,
    SlotError,
    SlotOccupiedError,
    VaultCorruptedError,
    WeakKeyError,
    generate_key,
)


def _clean_input(prompt: str = "") -> str:
    """Зчитує введення та очищає від можливих артефактів кодування (наприклад, BOM п»ї у Windows)."""
    raw = input(prompt)
    return raw.replace("п»ї", "").strip().lstrip("\ufeff")


def _ensure_manager() -> AccountManager | None:
    """Ініціалізує AccountManager або повертає None з підказкою, якщо ключ відсутній."""
    try:
        return AccountManager()
    except (MissingKeyError, WeakKeyError) as exc:
        print("\n[!] Помилка конфігурації ключа шифрування:")
        print(f"    {exc}")
        print("    Встановіть змінну GEMINI_MANAGER_KEY у файлі .env (пункт [9] для генерації).")
        return None
    except (InvalidKeyError, VaultCorruptedError) as exc:
        print("\n[!] Помилка доступу до сховища vault.bin:")
        print(f"    {exc}")
        print("    Можливо, вказано невірний GEMINI_MANAGER_KEY для існуючого файлу сховища.")
        return None
    except Exception as exc:
        print(f"\n[!] Не вдалося ініціалізувати менеджер сховища: {exc}")
        return None


def _format_window(window: object) -> str:
    if not window:
        return "н/д"
    pct = getattr(window, "percent", None)
    reset = getattr(window, "reset_at", None)
    pct_str = f"{pct:.0f}%" if pct is not None else "н/д"
    if reset:
        return f"{pct_str} (скидання: {reset})"
    return pct_str


def _print_account_table(accounts: list[Account], free_slots: list[int]) -> None:
    if not accounts:
        print("\nУ сховищі наразі немає збережених акаунтів.")
        if free_slots:
            print(f"Вільні слоти: {', '.join(map(str, free_slots))}")
        return

    print("\n" + "=" * 80)
    print(f"{'Слот':<6} {'Активний':<10} {'Email':<30} {'Статус':<14} {'5h ліміт':<15} {'Проксі'}")
    print("-" * 80)

    for acc in accounts:
        active_mark = " * ТАК *" if acc.is_active else " ні"
        email_str = acc.email or acc.display_name or "Невідомо"
        status_str = acc.status or "OK"
        usage_5h = "н/д"
        if acc.usage and acc.usage.five_hour:
            usage_5h = _format_window(acc.usage.five_hour)
        elif acc.usage_percent is not None:
            usage_5h = f"{acc.usage_percent:.0f}%"

        proxy_str = "налаштовано" if acc.has_proxy else "пряме"
        print(f"#{acc.slot:<5} {active_mark:<10} {email_str:<30} {status_str:<14} {usage_5h:<15} {proxy_str}")

    print("=" * 80)
    if free_slots:
        print(f"Вільні слоти: {', '.join(map(str, free_slots))}")


async def action_list_accounts(mgr: AccountManager) -> None:
    """[1] Список підключених акаунтів та вільних слотів."""
    accounts = mgr.accounts()
    free = mgr.free_slots()
    _print_account_table(accounts, free)

    # Детальна інформація про квоти
    active = mgr.active()
    if active and active.usage:
        print("\nДеталі активного акаунта:")
        print(f"  - Рівень (Tier): {active.usage.tier or 'Стандартний'}")
        if active.usage.five_hour:
            print(f"  - 5-годинне вікно: {_format_window(active.usage.five_hour)}")
        if active.usage.weekly:
            print(f"  - Тижневе вікно: {_format_window(active.usage.weekly)}")
        if active.usage.ai_credits_remaining is not None:
            print(f"  - Залишок AI-кредитів: {active.usage.ai_credits_remaining}")


async def action_login(mgr: AccountManager) -> None:
    """[2] Авторизація через браузер та запис у слот."""
    free = mgr.free_slots()
    if not free:
        print("\n[!] Немає вільних слотів. Спочатку видаліть непотрібний акаунт (пункт [8]).")
        return

    slot_str = _clean_input(f"Оберіть вільний номер слота ({', '.join(map(str, free))}): ")
    try:
        slot = int(slot_str)
    except ValueError:
        print("[!] Некоректний номер слота.")
        return

    browser = _clean_input("Браузер для входу (auto / chrome / msedge / chromium) [auto]: ") or "auto"
    proxy = _clean_input("Проксі для сесії (наприклад http://127.0.0.1:8080) [Enter, якщо без проксі]: ") or None

    print(f"\nЗапуск браузера ({browser}) для авторизації в Google...")
    print("Увійдіть у свій обліковий запис у вікні браузера, що відкриється.")
    print("Очікування завершення входу (таймаут 5 хв)...")

    def on_status(msg: str) -> None:
        print(f" -> {msg}")

    try:
        acc = await mgr.login(
            slot=slot,
            browser=browser,
            proxy=proxy,
            on_status=on_status,
        )
        print(f"\n[+] Успішно підключено: {acc.email or 'акаунт'} у слот #{acc.slot}!")
        if acc.is_active:
            print("    Цей акаунт призначено активним.")
    except SlotOccupiedError as exc:
        print(f"[!] Слот зайнятий: {exc}")
    except DuplicateAccountError as exc:
        print(f"[!] Такий акаунт вже є в іншому слоті: {exc}")
    except LoginError as exc:
        print(f"[!] Помилка авторизації: {exc}")
    except GeminiManagerError as exc:
        print(f"[!] Помилка менеджера: {exc}")
    except Exception as exc:
        print(f"[!] Непередбачена помилка: {exc}")


async def action_set_active(mgr: AccountManager) -> None:
    """[3] Встановлення активного акаунта."""
    accounts = mgr.accounts()
    if not accounts:
        print("\n[!] Немає підключених акаунтів.")
        return

    slot_str = _clean_input("Введіть номер слота, який зробити активним: ")
    try:
        slot = int(slot_str)
        acc = mgr.set_active(slot)
        print(f"[+] Слот #{acc.slot} ({acc.email or 'невідомо'}) тепер є активним.")
    except (ValueError, SlotError, SlotEmptyError) as exc:
        print(f"[!] Помилка: {exc}")


async def action_refresh_usage(mgr: AccountManager) -> None:
    """[4] Оновлення квот і лімітів."""
    accounts = mgr.accounts()
    if not accounts:
        print("\n[!] Немає підключених акаунтів для оновлення.")
        return

    choice = _clean_input("Оновити ліміти для всіх акаунтів (Enter) або введіть конкретний слот: ")
    print("Запит лімітів у Gemini...")
    try:
        if not choice:
            updated = await mgr.refresh_all_usage()
            print(f"[+] Оновлено квоти для {len(updated)} акаунтів.")
        else:
            slot = int(choice)
            acc = await mgr.refresh_usage(slot)
            print(f"[+] Оновлено дані для слота #{acc.slot}: {acc.email}")
            if acc.usage and acc.usage.five_hour:
                print(f"    5h ліміт: {_format_window(acc.usage.five_hour)}")
    except (ValueError, SlotError, SlotEmptyError) as exc:
        print(f"[!] Некоректний слот: {exc}")
    except SessionExpiredError as exc:
        print(f"[!] Сесія застаріла: {exc}. Потрібно перелогінитись.")
    except Exception as exc:
        print(f"[!] Помилка оновлення лімітів: {exc}")


async def action_ask(mgr: AccountManager) -> None:
    """[5] Одноразовий запит до Gemini."""
    accounts = mgr.accounts()
    if not accounts:
        print("\n[!] Немає підключених акаунтів. Спочатку виконайте вхід (пункт [2]).")
        return

    slot_str = _clean_input("Номер слота [Enter для активного акаунта]: ")
    slot: int | None = None
    if slot_str:
        try:
            slot = int(slot_str)
        except ValueError:
            print("[!] Некоректний номер слота.")
            return

    prompt = _clean_input("Введіть запит для Gemini: ")
    if not prompt:
        print("[!] Запит не може бути порожнім.")
        return

    print("\nНадсилання запиту...")
    try:
        if slot is None:
            response = await mgr.generate_content(prompt)
        else:
            async with mgr.client(slot) as client:
                response = await client.generate_content(prompt)

        print("\n" + "-" * 40 + " Відповідь Gemini " + "-" * 40)
        print(response.text)
        print("-" * 98)
    except NoActiveAccountError:
        print("[!] Немає активного акаунта. Оберіть активний (пункт [3]) або вкажіть слот.")
    except SessionExpiredError as exc:
        print(f"[!] Сесію відхилено Google: {exc}. Потрібно виконати повторний вхід.")
    except Exception as exc:
        print(f"[!] Помилка запиту: {exc}")


async def action_chat(mgr: AccountManager) -> None:
    """[6] Інтерактивний діалоговий чат із підтримкою контексту."""
    accounts = mgr.accounts()
    if not accounts:
        print("\n[!] Немає підключених акаунтів.")
        return

    slot_str = _clean_input("Номер слота для чату [Enter для активного акаунта]: ")
    slot: int | None = None
    if slot_str:
        try:
            slot = int(slot_str)
        except ValueError:
            print("[!] Некоректний номер слота.")
            return

    try:
        async with mgr.client(slot) as client:
            chat = client.start_chat()
            target_acc = mgr.get(client.slot)
            print(f"\n[+] Чат відкрито через слот #{target_acc.slot} ({target_acc.email or 'акаунт'}).")
            print("Введіть ваше повідомлення. Для повернення в меню введіть 'exit' або порожній рядок.\n")

            while True:
                try:
                    user_msg = _clean_input("Ви > ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    break

                if not user_msg or user_msg.lower() in {"exit", "quit"}:
                    print("Завершення сесії чату.")
                    break

                print("Gemini друкує відповідь...")
                try:
                    res = await chat.send_message(user_msg)
                    print(f"\nGemini:\n{res.text}\n")
                except SessionExpiredError as exc:
                    print(f"\n[!] Сесія застаріла під час бесіди: {exc}")
                    break
                except Exception as exc:
                    print(f"\n[!] Помилка отримання відповіді: {exc}")
    except NoActiveAccountError:
        print("[!] Не обрано активний акаунт.")
    except (SlotError, SlotEmptyError) as exc:
        print(f"[!] Помилка слота: {exc}")
    except Exception as exc:
        print(f"[!] Не вдалося запустити чат: {exc}")


async def action_set_proxy(mgr: AccountManager) -> None:
    """[7] Налаштування проксі для слота."""
    slot_str = _clean_input("Введіть номер слота: ")
    try:
        slot = int(slot_str)
    except ValueError:
        print("[!] Некоректний номер слота.")
        return

    proxy = _clean_input("Введіть URL проксі (або Enter, щоб очистити): ") or None
    try:
        acc = mgr.set_proxy(slot, proxy)
        if proxy:
            print(f"[+] Проксі для слота #{acc.slot} встановлено: {proxy}")
        else:
            print(f"[+] Проксі для слота #{acc.slot} успішно очищено.")
    except (SlotError, SlotEmptyError) as exc:
        print(f"[!] Помилка: {exc}")


async def action_remove_account(mgr: AccountManager) -> None:
    """[8] Видалення акаунта зі слота."""
    slot_str = _clean_input("Введіть номер слота для видалення: ")
    try:
        slot = int(slot_str)
        acc = mgr.get(slot)
    except (ValueError, SlotError, SlotEmptyError) as exc:
        print(f"[!] Не знайдено акаунт у слоті: {exc}")
        return

    confirm = _clean_input(f"Ви дійсно бажаєте видалити слот #{slot} ({acc.email or 'акаунт'}) зі сховища? [y/N]: ")
    if confirm.lower() not in {"y", "yes", "так"}:
        print("Видалення скасовано.")
        return

    removed = mgr.remove(slot)
    print(f"[+] Слот #{removed.slot} ({removed.email or 'акаунт'}) видалено з локального сховища.")
    print("    (Зверніть увагу: сесія на серверах Google залишається дійсною до ручного відклику).")


def action_keygen() -> None:
    """[9] Генерація нового ключа шифрування."""
    new_key = generate_key()
    print("\n" + "=" * 60)
    print("Згенеровано новий безпечний ключ шифрування:")
    print(f"  {new_key}")
    print("=" * 60)
    print("Додайте або замініть його у файлі .env:")
    print(f'GEMINI_MANAGER_KEY="{new_key}"')

    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        create = _clean_input(f"\nФайл .env відсутній. Створити його автоматично з цим ключем? [Y/n]: ")
        if create.lower() in {"", "y", "yes", "так"}:
            env_content = (
                f'GEMINI_MANAGER_KEY="{new_key}"\n'
                f'GEMINI_MANAGER_HOME="./data"\n'
                f'GEMINI_MANAGER_MAX_SLOTS="10"\n'
            )
            env_path.write_text(env_content, encoding="utf-8")
            os.environ["GEMINI_MANAGER_KEY"] = new_key
            print(f"[+] Файл {env_path.name} успішно створено!")


def _print_menu() -> None:
    print("\n" + "=" * 45)
    print("   gemini-webapi-manager: Головне меню")
    print("=" * 45)
    print(" [1] Список акаунтів (слоти та ліміти)")
    print(" [2] Авторизація (вхід через браузер)")
    print(" [3] Встановити активний акаунт")
    print(" [4] Оновити ліміти (Usage)")
    print(" [5] Одноразовий запит (Ask Gemini)")
    print(" [6] Інтерактивний діалоговий чат")
    print(" [7] Налаштування проксі для слота")
    print(" [8] Видалити акаунт зі слота")
    print(" [9] Згенерувати новий ключ шифрування")
    print(" [0] Вихід")
    print("-" * 45)


async def main_async() -> None:
    # Вимикаємо надлишковий логінг зовнішньої бібліотеки
    try:
        from gemini_webapi import set_log_level

        set_log_level("WARNING")
    except Exception:
        pass

    print("Ініціалізація прикладу gemini-webapi-manager...")
    vault_home = os.environ.get("GEMINI_MANAGER_HOME", "./data")
    print(f"Директорія сховища: {Path(vault_home).resolve()}")

    while True:
        _print_menu()
        try:
            choice = _clean_input("Оберіть дію [0-9]: ")
        except (KeyboardInterrupt, EOFError):
            print("\nРоботу програми завершено.")
            break

        if not choice:
            continue
        if choice == "0":
            print("Вихід.")
            break
        elif choice == "9":
            action_keygen()
            continue
        elif choice not in {"1", "2", "3", "4", "5", "6", "7", "8"}:
            print("[!] Невідомий пункт меню. Будь ласка, введіть число від 0 до 9.")
            continue

        # Для пунктів 1-8 потрібен діючий AccountManager
        mgr = _ensure_manager()
        if mgr is None:
            continue

        try:
            if choice == "1":
                await action_list_accounts(mgr)
            elif choice == "2":
                await action_login(mgr)
            elif choice == "3":
                await action_set_active(mgr)
            elif choice == "4":
                await action_refresh_usage(mgr)
            elif choice == "5":
                await action_ask(mgr)
            elif choice == "6":
                await action_chat(mgr)
            elif choice == "7":
                await action_set_proxy(mgr)
            elif choice == "8":
                await action_remove_account(mgr)
        finally:
            await mgr.aclose()


def main() -> None:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\nРоботу програми перервано користувачем.")
        sys.exit(0)


if __name__ == "__main__":
    main()

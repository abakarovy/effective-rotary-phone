"""Entry point: run only 3.3 tasks from test3.xlsx."""
import sys
import traceback

from browser import create_driver, wait_for_login
from run_33 import run_3_3


def _prompt_gender() -> str:
    """Спрашивает пол для подстановки вместо маркера <G> в ячейках Excel."""
    print(
        "Подстановка <G> в ответах из Excel:\n"
        "  1 — Мужской\n"
        "  2 — Женский"
    )
    while True:
        raw = (input("Введите 1 или 2: ").strip() or "").lower()
        if raw in ("1", "м", "муж", "мужской", "m"):
            return "Мужской"
        if raw in ("2", "ж", "жен", "женский", "f"):
            return "Женский"
        print("Нужно ввести 1 (Мужской) или 2 (Женский).")


def main() -> None:
    gender = _prompt_gender()
    print(f"Выбран пол: {gender} — все вхождения <G> в ответах будут заменены на это значение.\n")
    print("Starting Chrome. Log in when the page opens, then press Enter in this terminal.")
    driver = create_driver()
    try:
        wait_for_login(driver)
        run_3_3(driver, gender=gender)
        print("Done.")
    except Exception:
        traceback.print_exc()
    finally:
        input("Press Enter to close the browser...")
        driver.quit()


if __name__ == "__main__":
    main()
    sys.exit(0)

"""Entry point: run only 3.3 tasks from test3.xlsx."""
import sys
import traceback

from browser import create_driver, wait_for_login
from run_33 import run_3_3


def main() -> None:
    print("Starting Chrome. Log in when the page opens, then press Enter in this terminal.")
    driver = create_driver()
    try:
        wait_for_login(driver)
        run_3_3(driver)
        print("Done.")
    except Exception:
        traceback.print_exc()
    finally:
        input("Press Enter to close the browser...")
        driver.quit()


if __name__ == "__main__":
    main()
    sys.exit(0)

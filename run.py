"""Main entry: open Chrome, wait for login, open all videos in new tabs, run 3.1 then 3.2."""
import sys
import time
import traceback
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from browser import create_driver, wait_for_login
from config import VIDEO_LINKS_FILE
from run_31 import run_3_1
from run_32 import run_3_2
from run_33 import run_3_3

# Video timeline: seek to this fraction (0.95 = 95% to the end)
VIDEO_SEEK_FRACTION = 0.95
PLAYER_TIMELINE_SELECTOR = "input[data-testid='player-timeline-slider']"

# Flaws in the seek approach (when re-enabling):
# - Tries every tab with 8s wait each; first tab is often the login page (no player) -> wasted time.
# - Player may be inside an iframe; we never switch to it, so the slider is never found.
# - Setting input.value + dispatchEvent may not trigger the site's actual seek logic (custom players
#   often keep state in JS and only sync the range input for display).
# - No wait for video page/player to finish loading before looking for the slider (only 0.5s per tab).


def _read_video_links(path: str) -> list[str]:
    """Read video URLs: one per line, skip empty and # comments."""
    p = Path(path)
    if not p.exists():
        return []
    return [
        line.strip() for line in p.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _seek_video_tab_to_percent(driver, fraction: float = VIDEO_SEEK_FRACTION) -> bool:
    """Find player timeline slider in current tab and set it to fraction of max (e.g. 0.95 = 95%)."""
    try:
        slider = WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, PLAYER_TIMELINE_SELECTOR))
        )
        max_val = float(slider.get_attribute("max") or "1")
        seek_to = fraction * max_val
        driver.execute_script(
            """
            var el = arguments[0];
            var val = arguments[1];
            el.value = val;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            """,
            slider,
            seek_to,
        )
        return True
    except Exception:
        return False


def _seek_all_video_tabs_to_95(driver) -> None:
    """Switch to each tab that has the video player and seek timeline to 95%."""
    handles = driver.window_handles
    if len(handles) <= 1:
        return
    current = driver.current_window_handle
    for h in handles:
        try:
            driver.switch_to.window(h)
            time.sleep(0.5)
            if _seek_video_tab_to_percent(driver, VIDEO_SEEK_FRACTION):
                print(f"[video] Seeked to {int(VIDEO_SEEK_FRACTION * 100)}% in one tab")
        except Exception:
            pass
    try:
        driver.switch_to.window(current)
    except Exception:
        if handles:
            driver.switch_to.window(handles[0])


def main() -> None:
    print("Starting Chrome. Log in when the page opens, then press Enter in this terminal.")
    driver = create_driver()
    try:
        wait_for_login(driver)
        video_links = _read_video_links(VIDEO_LINKS_FILE)
        if video_links:
            print(f"[video] Opening {len(video_links)} video(s) in new tabs...")
            for url in video_links:
                if url:
                    driver.execute_script("window.open(arguments[0], '_blank');", url)
            time.sleep(3)
            # Video seek disabled: slow (8s timeout per tab), often doesn't find element or actually seek.
            # print(f"[video] Seeking to {int(VIDEO_SEEK_FRACTION * 100)}% in video tabs...")
            # _seek_all_video_tabs_to_95(driver)
        run_3_1(driver)
        run_3_2(driver)
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

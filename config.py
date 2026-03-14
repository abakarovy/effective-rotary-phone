"""Configuration. Override via environment or edit defaults."""
import os
import sys
from pathlib import Path

# When running as .exe, data files are bundled and extracted to _MEIPASS
def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # bundled data extracted here
    return Path(__file__).resolve().parent

_BASE = _base_dir()

BASE_URL = os.environ.get("KBC_BASE_URL", "https://kb.cifrium.ru")

# Paths to Excel files (3.1 = classworks/homeworks, 3.2 = lessons)
EXCEL_3_1 = os.environ.get("KBC_EXCEL_31", str(_BASE / "test1.xlsx"))
EXCEL_3_2 = os.environ.get("KBC_EXCEL_32", str(_BASE / "test2.xlsx"))

# Optional: course_id for building teacher task page URL for 3.1 (if needed)
COURSE_ID = os.environ.get("KBC_COURSE_ID", "")

# Selenium
HEADLESS = os.environ.get("KBC_HEADLESS", "").lower() in ("1", "true", "yes")
IMPLICIT_WAIT_SEC = 10
# Pause after opening browser so user can log in
LOGIN_WAIT_SEC = int(os.environ.get("KBC_LOGIN_WAIT_SEC", "60"))

# Answer matching: min ratio (0..1) to accept Excel answer as match for radio option text
ANSWER_SIMILARITY_THRESHOLD = float(os.environ.get("KBC_ANSWER_SIMILARITY", "0.7"))

# Optional: X-Device-Id header (from browser DevTools). If empty, a random one is used.
X_DEVICE_ID = os.environ.get("KBC_X_DEVICE_ID", "")

# Video links: one URL per line; opened in new tab before each task group (3.1 then 3.2, in order).
VIDEO_LINKS_FILE = os.environ.get("KBC_VIDEO_LINKS_FILE", str(_BASE / "videos.txt"))

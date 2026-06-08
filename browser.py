"""Selenium browser session and cookie/CSRF extraction for API calls."""
import json
import time
from urllib.parse import urlparse
from typing import Any

import requests
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

from config import BASE_URL, HEADLESS, IMPLICIT_WAIT_SEC, LOGIN_WAIT_SEC, TASK_PAGE_WAIT_SEC, X_DEVICE_ID


def create_driver() -> webdriver.Chrome:
    opts = Options()
    if HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "OFF"})
    driver = webdriver.Chrome(options=opts)
    driver.implicitly_wait(IMPLICIT_WAIT_SEC)
    return driver


def drain_performance_logs(driver: webdriver.Chrome) -> None:
    """Clear buffered Chrome performance log entries."""
    try:
        driver.get_log("performance")
    except Exception:
        pass


def wait_for_network_url(
    driver: webdriver.Chrome,
    url_fragment: str,
    timeout: float | None = None,
    min_status: int = 200,
    max_status: int = 399,
) -> bool:
    """
    Poll Chrome performance log until Network.responseReceived for url_fragment
    with HTTP status in [min_status, max_status].
    """
    if not url_fragment:
        return False
    deadline = time.time() + (timeout if timeout is not None else TASK_PAGE_WAIT_SEC)
    fragment = url_fragment.lower()
    while time.time() < deadline:
        try:
            entries = driver.get_log("performance")
        except Exception:
            entries = []
        for entry in entries:
            try:
                msg = json.loads(entry.get("message", "{}")).get("message", {})
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue
            if msg.get("method") != "Network.responseReceived":
                continue
            response = msg.get("params", {}).get("response", {})
            url = (response.get("url") or "").lower()
            status = int(response.get("status") or 0)
            if fragment in url and min_status <= status <= max_status:
                return True
        time.sleep(0.2)
    return False


def _wait_for_task_dom(driver: webdriver.Chrome, timeout: float = 8) -> None:
    """After API response, wait until task UI markup appears in page source."""
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: any(
                marker in (d.page_source or "")
                for marker in ("taskForm", "questions[", "LinkTask", "cm-editor")
            )
        )
    except TimeoutException:
        pass


def load_page_and_wait_for_api(
    driver: webdriver.Chrome,
    page_url: str,
    api_url_fragment: str | None = None,
    timeout: float | None = None,
) -> str | None:
    """
    Open page_url and wait for the SPA task API call (e.g. /api/trainings/1/tasks/2).
    Uses Chrome performance / Network logs instead of a fixed short sleep.
    """
    wait_sec = timeout if timeout is not None else TASK_PAGE_WAIT_SEC
    drain_performance_logs(driver)
    try:
        driver.set_page_load_timeout(max(int(wait_sec), 10))
        try:
            driver.get(page_url)
        except TimeoutException:
            # SPA pages may not fire classic load; API wait below still applies.
            pass
        if api_url_fragment:
            ok = wait_for_network_url(driver, api_url_fragment, timeout=wait_sec)
            if not ok:
                print(f"[browser] API wait timeout ({wait_sec}s): {api_url_fragment}")
            else:
                _wait_for_task_dom(driver, timeout=min(8, wait_sec / 2))
        else:
            time.sleep(1.0)
        return driver.page_source or None
    finally:
        try:
            driver.switch_to.default_content()
            driver.set_page_load_timeout(300)
        except Exception:
            pass


def wait_for_login(driver: webdriver.Chrome) -> None:
    """Open base URL and wait so user can log in manually."""
    driver.get(BASE_URL)
    try:
        WebDriverWait(driver, LOGIN_WAIT_SEC).until(
            lambda d: "kb.cifrium" in (d.current_url or "")
        )
    except Exception:
        pass
    input("Log in in the browser, then press Enter here to continue...")


def copy_cookies(from_driver: webdriver.Chrome, to_driver: webdriver.Chrome) -> None:
    """Copy cookies from one driver to another so to_driver is logged in the same way."""
    to_driver.get(BASE_URL)
    for c in from_driver.get_cookies():
        try:
            to_driver.add_cookie(c)
        except Exception:
            pass
    to_driver.refresh()


def get_session_cookies(driver: webdriver.Chrome) -> list[dict[str, Any]]:
    """Get cookies from current driver for use in requests."""
    return driver.get_cookies()


def get_csrf_from_cookies(cookies: list[dict[str, Any]]) -> str:
    """Extract _csrf_token value from cookie list (URL-decoded)."""
    import urllib.parse
    for c in cookies:
        if c.get("name") == "_csrf_token":
            return urllib.parse.unquote(c.get("value", "") or "")
    return ""


def cookies_to_requests_dict(cookies: list[dict[str, Any]]) -> dict[str, str]:
    """Convert Selenium cookie list to dict for requests.get/post cookies=."""
    return {c["name"]: c.get("value", "") for c in cookies}


def refresh_session_from_driver(driver: webdriver.Chrome, session: requests.Session) -> None:
    """Update session cookies and CSRF from current driver state (e.g. after driver.get())."""
    cookies = get_session_cookies(driver)
    default_domain = urlparse(BASE_URL).netloc or "kb.cifrium.ru"
    for c in cookies:
        name = c.get("name")
        value = c.get("value") or ""
        if not name:
            continue
        domain = (c.get("domain") or default_domain).lstrip(".")
        path = c.get("path") or "/"
        session.cookies.set(name, value, domain=domain, path=path)
    csrf = get_csrf_from_cookies(cookies)
    if csrf:
        session.headers["X-CSRF-Token"] = csrf


def make_session(driver: webdriver.Chrome) -> requests.Session:
    """Build requests.Session with same cookies as driver and required headers."""
    from urllib.parse import unquote
    cookies = get_session_cookies(driver)
    csrf = get_csrf_from_cookies(cookies)
    sess = requests.Session()
    default_domain = urlparse(BASE_URL).netloc or "kb.cifrium.ru"
    # Set cookies using each cookie's domain so they are sent (browser may use .kb.cifrium.ru)
    for c in cookies:
        name = c.get("name")
        value = c.get("value") or ""
        if not name:
            continue
        domain = c.get("domain") or default_domain
        path = c.get("path") or "/"
        sess.cookies.set(name, value, domain=domain.lstrip("."), path=path)
    sess.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": BASE_URL,
        "User-Agent": driver.execute_script("return navigator.userAgent;") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "X-CSRF-Token": csrf,
        "X-Requested-With": "XMLHttpRequest",
        "X-Skip-Error-Notification": "true",
    })
    if X_DEVICE_ID:
        sess.headers["X-Device-Id"] = X_DEVICE_ID
    else:
        import uuid
        sess.headers["X-Device-Id"] = uuid.uuid4().hex
    return sess

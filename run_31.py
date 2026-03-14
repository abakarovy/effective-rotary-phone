"""Run format 3.1: classworks/homeworks. Submit each answer via API (no video lookup on classworks question page)."""
import traceback
from pathlib import Path

from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from api_submit import submit_answer_3_1
from browser import make_session, refresh_session_from_driver
from config import BASE_URL, EXCEL_3_1, ANSWER_SIMILARITY_THRESHOLD
from excel_loader import load_3_1
from page_parser import get_csrf_from_page, parse_question_page
from similarity import best_match


def run_3_1(driver: WebDriver) -> None:
    path = Path(EXCEL_3_1)
    if not path.exists():
        print(f"[3.1] File not found: {path} (place test1.xlsx next to the script/exe)")
        return
    data = load_3_1(path)
    if not data:
        print(f"[3.1] No data in {EXCEL_3_1} (file exists but no rows; expect 3 columns, no header row)")
        return

    for classwork_id, rows in data.items():
        if not rows:
            continue
        print(f"[3.1] Classwork {classwork_id}: submitting {len(rows)} answers")
        try:
            session = make_session(driver)
        except Exception:
            traceback.print_exc()
            continue
        for question_id, answer in rows:
            print(f"[3.1] Question {question_id} ...", flush=True)
            question_url = f"{BASE_URL}/classworks/{classwork_id}/tasks/{question_id}?page=1"
            try:
                driver.set_page_load_timeout(20)
                driver.get(question_url)
                print(f"[3.1] Page loaded, waiting for form...", flush=True)
                form_selector = "input[name^='questions[']"
                try:
                    WebDriverWait(driver, 2).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, form_selector))
                    )
                    page_html = driver.page_source
                except Exception:
                    # Form may be inside an iframe
                    iframes = driver.find_elements(By.TAG_NAME, "iframe")
                    page_html = None
                    for idx, ifr in enumerate(iframes):
                        try:
                            driver.switch_to.frame(ifr)
                            WebDriverWait(driver, 2).until(
                                EC.presence_of_element_located((By.CSS_SELECTOR, form_selector))
                            )
                            page_html = driver.execute_script("return document.documentElement.outerHTML")
                            driver.switch_to.default_content()
                            break
                        except Exception:
                            driver.switch_to.default_content()
                            continue
                    if not page_html:
                        raise TimeoutError("Form not found in page or in any iframe")
            except Exception:
                print(f"[3.1] GET question {question_id} failed:")
                traceback.print_exc()
                continue
            finally:
                try:
                    driver.switch_to.default_content()
                    driver.set_page_load_timeout(300)
                except Exception:
                    pass
            if "questions[" not in page_html:
                print(f"[3.1] Page has no form (questions[...]), question {question_id}")
                continue
            refresh_session_from_driver(driver, session)
            page_csrf = get_csrf_from_page(page_html)
            if page_csrf:
                session.headers["X-CSRF-Token"] = page_csrf
            parsed = parse_question_page(page_html)
            form_key = parsed.get("question_form_key")
            if not form_key:
                print(f"[3.1] Could not find form question key for question {question_id}")
                continue
            if parsed.get("is_text_input"):
                answer_value = str(answer).strip() if answer is not None else ""
            else:
                options = parsed.get("options") or []
                answer_value = best_match(answer, options, threshold=ANSWER_SIMILARITY_THRESHOLD)
                if answer_value is None:
                    print(f"[3.1] No matching option for answer '{answer}' (question {question_id}), skip.")
                    continue
            try:
                resp = submit_answer_3_1(session, classwork_id, question_id, form_key, answer_value)
            except Exception:
                print(f"[3.1] Submit question {question_id} failed:")
                traceback.print_exc()
                continue
            if resp.status_code in (200, 201, 204):
                preview = (resp.text or "")[:200].replace("\n", " ")
                print(f"[3.1] Submit classwork={classwork_id} question={question_id} -> {resp.status_code} {preview}")
            else:
                body = (resp.text or (resp.content.decode(errors="replace") if resp.content else ""))[:500]
                print(f"[3.1] Submit classwork={classwork_id} question={question_id} -> err {resp.status_code}: {body}")

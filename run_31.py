"""Run format 3.1: classworks/homeworks. Submit each answer via API (no page load)."""
import traceback
from pathlib import Path

from selenium.webdriver.chrome.webdriver import WebDriver
from api_submit import submit_answer_3_1, submit_answer_3_1_drag
from browser import make_session, refresh_session_from_driver
from config import EXCEL_3_1, ANSWER_SIMILARITY_THRESHOLD
from drag_utils import build_drag_payload_from_api, parse_drag_pairs_from_excel
from excel_loader import load_3_1
from similarity import best_match
from task_api import fetch_homework_task, parse_task_from_api, task_has_links_question


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
            refresh_session_from_driver(driver, session)
        except Exception:
            traceback.print_exc()
            continue
        for question_id, answer in rows:
            print(f"[3.1] Question {question_id} ...", flush=True)
            try:
                task_json = fetch_homework_task(session, classwork_id, question_id)
            except Exception:
                print(f"[3.1] API GET question {question_id} failed:")
                traceback.print_exc()
                continue
            if not task_json:
                print(f"[3.1] Could not load task API for question {question_id}")
                continue

            parsed = parse_task_from_api(task_json, api_format="homework")
            is_drag = parsed.get("is_drag") or task_has_links_question(task_json)

            if is_drag:
                drag_qid, drag_map = build_drag_payload_from_api(
                    task_json,
                    str(answer or ""),
                    threshold=ANSWER_SIMILARITY_THRESHOLD,
                )
                if not drag_map:
                    pairs = parse_drag_pairs_from_excel(str(answer or ""))
                    print(
                        f"[3.1] Drag question {question_id}: "
                        f"parsed {len(pairs)} pair(s) from Excel, matched 0 (check text vs API options)"
                    )
                    continue
                try:
                    resp = submit_answer_3_1_drag(
                        session,
                        classwork_id,
                        question_id,
                        drag_qid,
                        drag_map,
                    )
                except Exception:
                    print(f"[3.1] Submit drag question {question_id} failed:")
                    traceback.print_exc()
                    continue
                if resp.status_code in (200, 201, 204):
                    preview = (resp.text or "")[:200].replace("\n", " ")
                    print(
                        f"[3.1] Submit drag classwork={classwork_id} question={question_id} "
                        f"-> {resp.status_code} {preview}"
                    )
                else:
                    body = (
                        resp.text or (resp.content.decode(errors="replace") if resp.content else "")
                    )[:500]
                    print(
                        f"[3.1] Submit drag classwork={classwork_id} question={question_id} "
                        f"-> err {resp.status_code}: {body}"
                    )
                continue

            form_key = parsed.get("question_form_key")
            if not form_key:
                print(f"[3.1] Could not parse task API for question {question_id}")
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

"""Run training format: /trainings/{training_id}/tasks/{task_id}."""
import json
import re
import time
import traceback
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from selenium.webdriver.chrome.webdriver import WebDriver
from api_submit import (
    finish_training,
    start_training,
    submit_answer_training,
    submit_answer_training_multiple,
    submit_answer_training_code,
    submit_answer_training_drag,
    submit_answer_training_raw,
)
from browser import make_session, refresh_session_from_driver
from http_retry import request_with_retry
from config import ANSWER_SIMILARITY_THRESHOLD, BASE_URL, EXCEL_3_3
from excel_loader import load_3_3
from drag_utils import build_drag_payload_from_api, parse_drag_pairs_from_excel
from similarity import best_match, best_matches_multiple, similarity
from task_api import fetch_training_task, parse_task_from_api, task_has_links_question

SUBMIT_VERIFY_TRIES = 3
SUBMIT_VERIFY_DELAY_SEC = 0.35

# Задания trainings, для которых нужен точный multipart (не из Excel).
# Ключ: (training_id, task_id) как строки — как в первых двух колонках таблицы.
TRAINING_TASK_FIXED_MULTIPART: dict[tuple[str, str], list[tuple[str, str]]] = {
    ("2988", "1208284"): [
        ("questions[1262804][]", "10386453"),
        ("questions[1262804][]", "10386455"),
        ("questions[1262805][]", "Мне нравятся технологии"),
    ],
    # Два radio/multiselect + одно текстовое поле
    ("2989", "1209399"): [
        ("questions[1264138][]", "10392152"),
        ("questions[1264138][]", "10392153"),
        ("questions[1264139][]", "asd"),
    ],
}


def _apply_gender_marker(raw: Any, gender: str | None) -> Any:
    """Заменяет <G> на выбранный пол (Мужской/Женский). Значение без маркера возвращается как есть."""
    if gender is None or raw is None:
        return raw
    text = str(raw)
    if "<G>" not in text:
        return raw
    return text.replace("<G>", gender)


def _apply_gender_to_str(s: str, gender: str | None) -> str:
    if gender is None:
        return s
    return s.replace("<G>", gender)

def _is_training_task_already_answered(html: str) -> bool:
    """
    Более строгая проверка solved для training-задач.
    Не используем общий эвристический детектор, чтобы не получать ложные skip.
    """
    if not html:
        return False
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="taskForm")
    if not form:
        return False
    # Если есть кнопка "Ответить" внутри формы — задача точно не завершена.
    submit_btn = form.find("button", string=re.compile(r"Ответить"))
    if submit_btn is not None and submit_btn.get("disabled") is None:
        return False
    # Иначе считаем solved только при явных признаках solved-состояния.
    html_low = html.lower()
    if "actions_solved" in html_low or "tasksolved" in html_low or "alreadysolved" in html_low:
        return True
    # Если кнопки "Ответить" в taskForm нет, чаще всего это solved-экран.
    return submit_btn is None


def _extract_training_and_task_from_form_action(html: str) -> tuple[int | None, int | None]:
    """Extract canonical ids from form action /trainings/<id>/tasks/<id>."""
    if not html:
        return None, None
    try:
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form", id="taskForm")
        if not form:
            return None, None
        action = (form.get("action") or "").strip()
        m = re.search(r"/trainings/(\d+)/tasks/(\d+)", action)
        if not m:
            return None, None
        return int(m.group(1)), int(m.group(2))
    except Exception:
        return None, None


def _parse_drag_payload(answer_text: str) -> tuple[int | None, list[tuple[int, int]]]:
    """
    Parse drag payload copied from curl in Excel.
    Supports both plain:
      name="questions[1207774][10162110]"
    and escaped-with-caret form:
      name=^\^"questions^[1207774^]^[10162110^]^\^"^
    """
    if not answer_text:
        return None, []
    lines = answer_text.splitlines()
    qid = None
    mappings: list[tuple[int, int]] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].replace("^", "")
        m = re.search(r"questions\[(\d+)\]\[(\d+)\]", line)
        if not m:
            i += 1
            continue
        cur_qid = int(m.group(1))
        from_id = int(m.group(2))
        if qid is None:
            qid = cur_qid
        j = i + 1
        to_id = None
        while j < n:
            candidate = lines[j].replace("^", "").strip()
            j += 1
            if not candidate:
                continue
            m_num = re.search(r"^\d+$", candidate)
            if m_num:
                to_id = int(candidate)
            break
        if to_id is not None:
            mappings.append((from_id, to_id))
        i = j
    if qid is None or not mappings:
        return None, []
    return qid, mappings


def _parse_multipart_fields_from_payload(answer_text: str) -> list[tuple[str, str]]:
    """
    Parse generic multipart-like payload copied from curl and return [(field_name, value), ...].
    Supports normal and caret-escaped Windows-curl snippets.
    """
    if not answer_text:
        return []
    lines = [ln.replace("^", "") for ln in answer_text.splitlines()]
    fields: list[tuple[str, str]] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = re.search(r'name="([^"]+)"', line)
        if not m:
            i += 1
            continue
        field_name = m.group(1).strip()

        # Move to payload body: after header line and optional blank separators.
        j = i + 1
        while j < n and lines[j].strip() == "":
            j += 1

        # Collect all body lines until multipart boundary.
        value_lines: list[str] = []
        while j < n:
            raw = lines[j]
            stripped = raw.strip()
            if stripped.startswith("------WebKitFormBoundary") or stripped.startswith("--"):
                break
            value_lines.append(raw.rstrip("\r"))
            j += 1

        # Keep exact text body, but trim only trailing empty lines.
        while value_lines and value_lines[-1].strip() == "":
            value_lines.pop()
        value = "\n".join(value_lines)
        fields.append((field_name, value))
        i = j
    return fields


def _get_training_code_ids_from_api(session, training_id, task_id) -> dict | None:
    """Try GET training task API JSON and extract code_question_id and test_case_execution_id."""
    url = f"{BASE_URL}/api/trainings/{training_id}/tasks/{task_id}"
    try:
        resp = request_with_retry(
            session, "GET", url, headers={"Accept": "application/json"}, timeout=10, label="training code ids"
        )
        if resp.status_code != 200:
            return None
        data = resp.json() if getattr(resp, "json", None) else json.loads(resp.text)
        qid = None
        tceid = None
        if isinstance(data, dict):
            task = data.get("task") or data
            qid = data.get("question_id") or data.get("id") or task.get("question_id")
            tceid = data.get("test_case_execution_id") or task.get("test_case_execution_id")
            if isinstance(task.get("questions"), list):
                for q in task["questions"]:
                    if not isinstance(q, dict):
                        continue
                    if qid is None:
                        qid = q.get("question_id") or q.get("id")
                    if tceid is None:
                        tceid = q.get("test_case_execution_id")
            if tceid is None and isinstance(data.get("test_case_executions"), list) and data["test_case_executions"]:
                first = data["test_case_executions"][0]
                tceid = first.get("id") if isinstance(first, dict) else first
        if qid is None:
            return None
        return {
            "code_question_id": int(qid) if not isinstance(qid, int) else qid,
            "test_case_execution_id": str(tceid) if tceid is not None else None,
        }
    except Exception:
        return None


def _extract_question_ids(task_json: dict | None) -> set[int]:
    ids: set[int] = set()
    if not isinstance(task_json, dict):
        return ids
    for q in (task_json.get("questions") or []):
        if isinstance(q, dict) and q.get("id") is not None:
            try:
                ids.add(int(q.get("id")))
            except Exception:
                pass
    return ids


def _has_non_empty_user_answer(task_json: dict | None, question_ids: set[int]) -> bool:
    if not isinstance(task_json, dict):
        return False
    ua = task_json.get("user_answer")
    if ua is None:
        return False
    if isinstance(ua, dict):
        if not ua:
            return False
        if not question_ids:
            return True
        for qid in question_ids:
            if str(qid) in ua or qid in ua:
                val = ua.get(str(qid), ua.get(qid))
                if val not in (None, "", [], {}):
                    return True
        # sometimes backend uses another shape, but non-empty dict is still a signal
        return True
    if isinstance(ua, list):
        return len(ua) > 0
    return bool(str(ua).strip())


def _verify_training_submit(
    session,
    training_id,
    task_id,
    expected_question_ids: set[int],
    pre_user_answer_id,
) -> bool:
    """Poll task API briefly and ensure answer actually persisted."""
    for _ in range(SUBMIT_VERIFY_TRIES):
        time.sleep(SUBMIT_VERIFY_DELAY_SEC)
        cur = fetch_training_task(session, training_id, task_id)
        if not isinstance(cur, dict):
            continue
        cur_ua_id = cur.get("user_answer_id")
        if pre_user_answer_id is not None and cur_ua_id != pre_user_answer_id:
            return True
        if _has_non_empty_user_answer(cur, expected_question_ids):
            return True
    return False


def _get_first_training_question_id(task_json: dict) -> int | None:
    if not isinstance(task_json, dict):
        return None
    questions = task_json.get("questions") or []
    if not isinstance(questions, list):
        return None
    for q in questions:
        if isinstance(q, dict) and q.get("id") is not None:
            try:
                return int(q.get("id"))
            except Exception:
                continue
    return None


def _training_start_succeeded(start_resp) -> bool:
    """True if training is started or was already in progress (finish may still work)."""
    if start_resp is None:
        return False
    if start_resp.status_code in (200, 201, 204):
        return True
    if start_resp.status_code in (400, 409, 422):
        body = (start_resp.text or "").lower()
        if any(
            s in body
            for s in ("already", "started", "in_progress", "progress", "начат", "уже")
        ):
            return True
    return False


def run_3_3(driver: WebDriver, gender: str | None = None) -> None:
    path = Path(EXCEL_3_3)
    if not path.exists():
        print(f"[3.3] File not found: {path} (place test3.xlsx next to the script/exe)")
        return
    data = load_3_3(path)
    if not data:
        print(f"[3.3] No data in {EXCEL_3_3} (expect 3 columns: training_id, task_id, answer)")
        return

    for training_id, rows in data.items():
        if not rows:
            continue
        print(f"[3.3] Training {training_id}: submitting {len(rows)} answers")
        try:
            session = make_session(driver)
            refresh_session_from_driver(driver, session)
        except Exception:
            traceback.print_exc()
            continue

        # Start training before submitting attempts (per requirements curl)
        start_resp = None
        try:
            start_resp = start_training(session, training_id)
            if start_resp.status_code in (200, 201, 204):
                preview = (start_resp.text or "")[:200].replace("\n", " ")
                print(f"[3.3] Start training={training_id} -> {start_resp.status_code} {preview}")
            else:
                body = (start_resp.text or (start_resp.content.decode(errors="replace") if start_resp.content else ""))[:500]
                print(f"[3.3] Start training={training_id} -> err {start_resp.status_code}: {body}")
        except Exception:
            print(f"[3.3] Start training {training_id} failed:")
            traceback.print_exc()

        training_started = _training_start_succeeded(start_resp)
        submit_failures = 0

        for task_id, answer in rows:
            answer = _apply_gender_marker(answer, gender)
            print(f"[3.3] Task {task_id} ...", flush=True)
            submit_training_id = training_id
            submit_task_id = task_id
            task_json = fetch_training_task(session, submit_training_id, submit_task_id)
            if not task_json:
                print(f"[3.3] Could not load task API for task {task_id}")
                continue

            parsed = parse_task_from_api(task_json, api_format="training")
            form_key = parsed.get("question_form_key")
            is_drag_task = parsed.get("is_drag") or task_has_links_question(task_json)
            expected_qids = _extract_question_ids(task_json)
            pre_user_answer_id = task_json.get("user_answer_id") if isinstance(task_json, dict) else None

            def _submit_with_verify(submit_callable):
                last_resp = None
                for attempt in range(2):
                    last_resp = submit_callable()
                    if last_resp is None:
                        continue
                    if last_resp.status_code not in (200, 201, 204):
                        return last_resp
                    if _verify_training_submit(
                        session,
                        submit_training_id,
                        submit_task_id,
                        expected_qids,
                        pre_user_answer_id,
                    ):
                        return last_resp
                    print(f"[3.3] Task {task_id}: submit accepted but not persisted yet, retry {attempt + 1}/1 ...")
                return last_resp

            # Универсальный обходной путь: если в Excel в ответе лежит payload (curl multipart),
            # отправляем его как есть (подходит для text/radio/drag/code-like полей).
            tid_key = (str(training_id).strip(), str(task_id).strip())
            if tid_key in TRAINING_TASK_FIXED_MULTIPART:
                raw_fields = [
                    (k, _apply_gender_to_str(v, gender))
                    for k, v in TRAINING_TASK_FIXED_MULTIPART[tid_key]
                ]
            else:
                raw_fields = []

            # Drag-and-drop (links): до generic multipart, т.к. Excel часто в формате [слева] -> [справа].
            if is_drag_task:
                drag_qid, drag_mappings = _parse_drag_payload(str(answer))
                if (drag_qid is None or not drag_mappings) and task_json:
                    drag_qid, drag_mappings = build_drag_payload_from_api(
                        task_json,
                        str(answer or ""),
                        threshold=ANSWER_SIMILARITY_THRESHOLD,
                    )
                if drag_qid is not None and drag_mappings:
                    try:
                        resp = _submit_with_verify(
                            lambda: submit_answer_training_drag(
                                session,
                                submit_training_id,
                                submit_task_id,
                                drag_qid,
                                drag_mappings,
                            )
                        )
                    except Exception:
                        print(f"[3.3] Submit drag task {task_id} failed:")
                        traceback.print_exc()
                        continue
                    if resp.status_code in (200, 201, 204):
                        preview = (resp.text or "")[:200].replace("\n", " ")
                        print(
                            f"[3.3] Submit training={submit_training_id} task={submit_task_id} "
                            f"-> {resp.status_code} {preview}"
                        )
                    else:
                        body = (
                            resp.text
                            or (resp.content.decode(errors="replace") if resp.content else "")
                        )[:500]
                        print(
                            f"[3.3] Submit training={submit_training_id} task={submit_task_id} "
                            f"-> err {resp.status_code}: {body}"
                        )
                    time.sleep(0.2)
                    continue
                n_pairs = len(parse_drag_pairs_from_excel(str(answer or "")))
                print(
                    f"[3.3] Drag task {task_id}: {n_pairs} pair(s) in Excel, "
                    f"0 matched API options — check spelling/extra spaces"
                )
                continue

            if not raw_fields:
                raw_fields = _parse_multipart_fields_from_payload(str(answer))
            if raw_fields:
                try:
                    resp = _submit_with_verify(
                        lambda: submit_answer_training_raw(
                            session,
                            submit_training_id,
                            submit_task_id,
                            raw_fields,
                        )
                    )
                except Exception:
                    print(f"[3.3] Submit task {task_id} with raw payload failed:")
                    traceback.print_exc()
                    continue
                if resp.status_code in (200, 201, 204):
                    preview = (resp.text or "")[:200].replace("\n", " ")
                    print(f"[3.3] Submit training={submit_training_id} task={submit_task_id} -> {resp.status_code} {preview}")
                else:
                    body = (resp.text or (resp.content.decode(errors="replace") if resp.content else ""))[:500]
                    print(f"[3.3] Submit training={submit_training_id} task={submit_task_id} -> err {resp.status_code}: {body}")
                time.sleep(0.2)
                continue

            if parsed.get("is_code"):
                code_id = parsed.get("code_question_id")
                if not code_id:
                    print(f"[3.3] Code task {task_id}: missing code question id, skip.")
                    continue
                source_code = str(answer or "").strip()
                if not source_code:
                    print(f"[3.3] Code task {task_id}: empty code in Excel, skip.")
                    continue
                try:
                    resp = _submit_with_verify(
                        lambda: submit_answer_training_code(
                            session,
                            submit_training_id,
                            submit_task_id,
                            code_id,
                            language="python3",
                            source_code=source_code,
                            test_case_execution_id=parsed.get("test_case_execution_id"),
                        )
                    )
                except Exception:
                    print(f"[3.3] Submit code task {task_id} failed:")
                    traceback.print_exc()
                    continue
            else:
                if not form_key and parsed.get("is_text_input") and task_json:
                    api_qid = _get_first_training_question_id(task_json)
                    if api_qid is not None:
                        form_key = f"questions[{api_qid}][]"
                if not form_key:
                    print(f"[3.3] Could not find form question key for task {task_id}")
                    continue
                if parsed.get("is_text_input"):
                    answer_raw = str(answer) if answer is not None else ""
                    # If Excel contains several text values (one per line), send repeated form fields.
                    text_values = [v.strip() for v in answer_raw.splitlines() if v.strip()]
                    if len(text_values) > 1:
                        try:
                            files = [(form_key, (None, v)) for v in text_values]
                            url = f"{BASE_URL}/api/trainings/{submit_training_id}/tasks/{submit_task_id}/answer_attempts"
                            resp = _submit_with_verify(
                                lambda: request_with_retry(
                                    session, "POST", url, files=files, label="3.3 multi-text"
                                )
                            )
                        except Exception:
                            print(f"[3.3] Submit task {task_id} failed:")
                            traceback.print_exc()
                            continue
                    else:
                        answer_value = answer_raw.strip()
                        if not answer_value:
                            print(f"[3.3] Text task {task_id}: empty answer in Excel, skip.")
                            continue
                        try:
                            resp = _submit_with_verify(
                                lambda: submit_answer_training(
                                    session, submit_training_id, submit_task_id, form_key, answer_value
                                )
                            )
                        except Exception:
                            print(f"[3.3] Submit task {task_id} failed:")
                            traceback.print_exc()
                            continue
                elif parsed.get("is_multiple_choice"):
                    options = parsed.get("options") or []
                    answer_values = best_matches_multiple(
                        answer, options, threshold=ANSWER_SIMILARITY_THRESHOLD
                    )
                    if not answer_values:
                        print(f"[3.3] No matching option(s) for answer '{answer}' (task {task_id}), skip.")
                        continue
                    try:
                        resp = _submit_with_verify(
                            lambda: submit_answer_training_multiple(
                                session,
                                submit_training_id,
                                submit_task_id,
                                form_key,
                                answer_values,
                            )
                        )
                    except Exception:
                        print(f"[3.3] Submit task {task_id} failed:")
                        traceback.print_exc()
                        continue
                else:
                    options = parsed.get("options") or []
                    answer_value = best_match(answer, options, threshold=ANSWER_SIMILARITY_THRESHOLD)
                    if answer_value is None:
                        print(f"[3.3] No matching option for answer '{answer}' (task {task_id}), skip.")
                        continue
                    qtypes = [
                        (q.get("type") or "?")
                        for q in (task_json.get("questions") or [])
                        if isinstance(q, dict)
                    ]
                    kind = "select" if parsed.get("is_select") else "radio/choice"
                    print(
                        f"[3.3] Task {task_id}: {kind} submit {form_key}={answer_value} "
                        f"(api types: {', '.join(qtypes)})"
                    )
                    try:
                        resp = _submit_with_verify(
                            lambda: submit_answer_training(
                                session, submit_training_id, submit_task_id, form_key, answer_value
                            )
                        )
                    except Exception:
                        print(f"[3.3] Submit task {task_id} failed:")
                        traceback.print_exc()
                        continue

            if resp is not None and resp.status_code in (200, 201, 204):
                preview = (resp.text or "")[:200].replace("\n", " ")
                print(f"[3.3] Submit training={submit_training_id} task={submit_task_id} -> {resp.status_code} {preview}")
            elif resp is not None:
                submit_failures += 1
                body = (resp.text or (resp.content.decode(errors="replace") if resp.content else ""))[:500]
                print(f"[3.3] Submit training={submit_training_id} task={submit_task_id} -> err {resp.status_code}: {body}")

            time.sleep(0.2)

        if not training_started:
            print(
                f"[3.3] Warning: start training={training_id} unclear; "
                f"attempting finish anyway."
            )
        if submit_failures:
            print(
                f"[3.3] Finish training={training_id} after {submit_failures} failed submit(s) "
                f"(server may return 500 if attempts are incomplete)."
            )
        try:
            finish_resp = finish_training(session, training_id)
            if finish_resp.status_code in (200, 201, 204):
                preview = (finish_resp.text or "")[:200].replace("\n", " ")
                print(f"[3.3] Finish training={training_id} -> {finish_resp.status_code} {preview}")
            else:
                body = (finish_resp.text or (finish_resp.content.decode(errors="replace") if finish_resp.content else ""))[:500]
                print(f"[3.3] Finish training={training_id} -> err {finish_resp.status_code}: {body}")
        except Exception:
            print(f"[3.3] Finish training {training_id} failed:")
            traceback.print_exc()

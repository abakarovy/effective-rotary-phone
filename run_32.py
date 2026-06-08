"""Run format 3.2: lessons. Submit each answer via API (no video). Load question page in browser to get form HTML."""
import json
import re
import time
import traceback
from pathlib import Path

from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By

from api_submit import (
    submit_answer_3_2,
    submit_answer_3_2_code,
    submit_answer_3_2_multiple,
    submit_answer_3_2_drag,
    submit_code_test_execution,
)
from browser import make_session, refresh_session_from_driver
from http_retry import request_with_retry
from config import BASE_URL, EXCEL_3_2, ANSWER_SIMILARITY_THRESHOLD
from excel_loader import load_3_2
from drag_utils import build_drag_payload_from_api, parse_drag_pairs_from_excel
from similarity import best_match, best_matches_multiple, similarity
from task_api import fetch_lesson_task, parse_task_from_api, task_has_links_question

FORM_SELECTOR = "input[name^='questions[']"

def _normalize_answer(answer) -> str:
    """
    Нормализует ответ из Excel.

    Для drag-and-drop типа (3.2) ответы записаны в формате:
      [item] -> [item]
    по одному соответствию на строку.
    Нам оттуда важна правая часть (то, что реально «выбрано»),
    поэтому превращаем такой текст в список правых элементов,
    разделённых переводами строк.
    Для остальных случаев возвращаем строку как есть.
    """
    if answer is None:
        return ""
    text = str(answer)
    if "->" not in text:
        return text
    lines = []
    for raw_line in str(text).splitlines():
        if "->" not in raw_line:
            continue
        left, right = raw_line.split("->", 1)
        right = right.strip()
        # убираем возможные квадратные скобки вокруг элемента
        if right.startswith("[") and right.endswith("]"):
            right = right[1:-1].strip()
        if right:
            lines.append(right)
    # если в итоге ничего не разобрали — вернём исходный текст,
    # чтобы не ломать поведение в неожиданных форматах
    return "\n".join(lines) if lines else text


def _parse_drag_payload(answer_text: str) -> tuple[int | None, list[tuple[int, int]]]:
    """
    Разбирает текст из Excel, если он содержит payload в стиле curl для drag-and-drop.

    Ищет блоки вида:
      Content-Disposition: form-data; name="questions[1207774][10162110]"

    <пустые строки>
    10162111

    Возвращает (question_id, [(from_id, to_id), ...]).
    Если ничего похожего не найдено — (None, []).
    """
    if not answer_text:
        return None, []
    lines = answer_text.splitlines()
    pattern = re.compile(r'name="questions\[(\d+)\]\[(\d+)\]"')
    question_id: int | None = None
    mappings: list[tuple[int, int]] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = pattern.search(line)
        if not m:
            i += 1
            continue
        qid = int(m.group(1))
        from_id = int(m.group(2))
        if question_id is None:
            question_id = qid
        # Пропускаем до первой непустой строки с числом — это to_id
        j = i + 1
        to_id: int | None = None
        while j < n:
            val_line = lines[j].strip()
            j += 1
            if not val_line:
                continue
            if val_line.isdigit():
                to_id = int(val_line)
            break
        if to_id is not None:
            mappings.append((from_id, to_id))
        i = j
    if question_id is None or not mappings:
        return None, []
    return question_id, mappings


def _get_code_ids_from_api(session, lesson_id, task_id) -> dict | None:
    """Try GET task API JSON and extract code_question_id and test_case_execution_id."""
    url = f"{BASE_URL}/api/lessons/{lesson_id}/tasks/{task_id}"
    try:
        resp = request_with_retry(
            session, "GET", url, headers={"Accept": "application/json"}, timeout=10, label="lesson code ids"
        )
        if resp.status_code != 200:
            return None
        data = resp.json() if getattr(resp, "json", None) else json.loads(resp.text)
        qid = None
        tceid = None
        if isinstance(data, dict):
            task = data.get("task") or data
            # IMPORTANT: for code tasks, question id is usually in questions[].id
            # (root data.id is task_id and must NOT be used as question id).
            qid = data.get("question_id") or task.get("question_id")
            tceid = data.get("test_case_execution_id") or task.get("test_case_execution_id")
            if not tceid and isinstance(data.get("test_case_executions"), list) and data["test_case_executions"]:
                tceid = data["test_case_executions"][0].get("id") if isinstance(data["test_case_executions"][0], dict) else data["test_case_executions"][0]
            if isinstance(task.get("questions"), list):
                for q in task["questions"]:
                    if not isinstance(q, dict):
                        continue
                    # Prefer programming question entry
                    if q.get("type") == "wk_programming" or qid is None:
                        if qid is None:
                            qid = q.get("question_id") or q.get("id") or qid
                        if tceid is None:
                            tceid = q.get("test_case_execution_id")
                    if qid is not None and tceid is not None:
                        break
        if qid is not None:
            return {
                "code_question_id": int(qid) if not isinstance(qid, int) else qid,
                "test_case_execution_id": str(tceid) if tceid is not None else None,
            }
        return None
    except Exception:
        return None


def run_3_2(driver: WebDriver) -> None:
    path_32 = Path(EXCEL_3_2)
    if not path_32.exists():
        print(f"[3.2] File not found: {path_32} (place test2.xlsx next to the script/exe)")
        return
    data = load_3_2(path_32)
    if not data:
        print(f"[3.2] No data in {EXCEL_3_2} (file exists but no rows; expect 3 columns, no header row)")
        return

    for lesson_id, rows in data.items():
        if not rows:
            continue
        print(f"[3.2] Lesson {lesson_id}: submitting {len(rows)} answers")
        try:
            session = make_session(driver)
            refresh_session_from_driver(driver, session)
        except Exception:
            traceback.print_exc()
            continue
        for task_id, answer in rows:
            # Приводим ответ из Excel к удобному текстовому виду
            # (в том числе вытаскиваем правые части пар для drag-and-drop).
            normalized_answer = _normalize_answer(answer)

            # Особый случай: в ячейке лежит payload для drag-and-drop (скопирован из curl).
            drag_question_id, drag_mappings = _parse_drag_payload(str(answer))
            if drag_question_id is not None and drag_mappings:
                print(
                    f"[3.2] Task {task_id}: using explicit drag-and-drop payload from Excel "
                    f"(question_id={drag_question_id}, {len(drag_mappings)} mapping(s))"
                )
                try:
                    resp = submit_answer_3_2_drag(
                        session,
                        lesson_id,
                        task_id,
                        drag_question_id,
                        drag_mappings,
                    )
                except Exception:
                    print(f"[3.2] Submit drag-and-drop task {task_id} failed:")
                    traceback.print_exc()
                    continue
                if resp.status_code in (200, 201, 204):
                    preview = (resp.text or "")[:200].replace("\n", " ")
                    print(f"[3.2] Submit lesson={lesson_id} task={task_id} -> {resp.status_code} {preview}")
                else:
                    body = (resp.text or (resp.content.decode(errors="replace") if resp.content else ""))[:500]
                    print(f"[3.2] Submit lesson={lesson_id} task={task_id} -> err {resp.status_code}: {body}")
                # payload уже отправлен — дальше обычная логика не нужна
                continue

            resp = None
            print(f"[3.2] Task {task_id} ...", flush=True)
            task_json = fetch_lesson_task(session, lesson_id, task_id)
            if not task_json:
                print(f"[3.2] Could not load task API for task {task_id}")
                continue
            parsed = parse_task_from_api(task_json, api_format="lesson")
            form_key = parsed.get("question_form_key")
            is_drag_task = parsed.get("is_drag") or task_has_links_question(task_json)
            if is_drag_task and task_json:
                drag_qid, drag_map = build_drag_payload_from_api(
                    task_json, str(answer or ""), threshold=ANSWER_SIMILARITY_THRESHOLD
                )
                if drag_qid is not None and drag_map:
                    try:
                        resp = submit_answer_3_2_drag(session, lesson_id, task_id, drag_qid, drag_map)
                    except Exception:
                        print(f"[3.2] Submit drag-and-drop task {task_id} failed:")
                        traceback.print_exc()
                        continue
                    if resp.status_code in (200, 201, 204):
                        preview = (resp.text or "")[:200].replace("\n", " ")
                        print(f"[3.2] Submit lesson={lesson_id} task={task_id} -> {resp.status_code} {preview}")
                    else:
                        body = (resp.text or (resp.content.decode(errors="replace") if resp.content else ""))[:500]
                        print(f"[3.2] Submit lesson={lesson_id} task={task_id} -> err {resp.status_code}: {body}")
                    continue
            if not form_key:
                # Drag-and-drop задачи (LinkTask) не имеют стандартного form_key в HTML.
                if is_drag_task:
                    print(f"[3.2] Task {task_id}: detected drag-and-drop type without form key (requires explicit payload).")
                    continue
                print(f"[3.2] Could not find form question key for task {task_id}")
                continue
            if parsed.get("is_code"):
                code_id = parsed.get("code_question_id")
                if not code_id:
                    print(f"[3.2] Code task {task_id}: missing code_question_id")
                    continue
                source_code = (normalized_answer if isinstance(normalized_answer, str) else str(normalized_answer or "")).strip()
                if not source_code:
                    print(f"[3.2] Code task {task_id}: empty code in Excel, skip.")
                    continue
                # Сначала запускаем тесты через /api/wk/test_case_executions
                test_question_id = parsed.get("code_question_id")
                if test_question_id:
                    try:
                        resp_test = submit_code_test_execution(
                            session,
                            test_question_id,
                            language="python3",
                            source_code=source_code,
                            lesson_id=lesson_id,
                            task_id=task_id,
                        )
                        print(
                            f"[3.2] Code task {task_id}: test execution -> "
                            f"{resp_test.status_code} {(resp_test.text or '')[:120].replace(chr(10), ' ')}"
                        )
                    except Exception:
                        print(f"[3.2] Code task {task_id}: test execution request failed:")
                        traceback.print_exc()
                try:
                    resp = submit_answer_3_2_code(
                        session,
                        lesson_id,
                        task_id,
                        code_id,
                        language="python3",
                        source_code=source_code,
                    )
                except Exception:
                    print(f"[3.2] Submit code task {task_id} failed:")
                    traceback.print_exc()
                    continue
            elif parsed.get("is_text_input"):
                answer_value = str(normalized_answer).strip() if normalized_answer is not None else ""
                try:
                    resp = submit_answer_3_2(session, lesson_id, task_id, form_key, answer_value)
                except Exception:
                    print(f"[3.2] Submit task {task_id} failed:")
                    traceback.print_exc()
                    continue
            else:
                options = parsed.get("options") or []
                referer_task = f"/teacher/lessons/{lesson_id}/tasks/{task_id}"
                if options:
                    qid = parsed.get("question_id")
                    if parsed.get("is_multiple_choice"):
                        answer_values = best_matches_multiple(
                            normalized_answer, options, threshold=ANSWER_SIMILARITY_THRESHOLD
                        )
                        if not answer_values:
                            answer_values = best_matches_multiple(
                                normalized_answer, options, threshold=0.5
                            )
                        if not answer_values:
                            print(
                                f"[3.2] No matching option(s) for answer '{answer}' "
                                f"(task {task_id}), skip."
                            )
                            continue
                        checkbox_key = (
                            f"questions[{qid}][]" if qid is not None else form_key
                        )
                        print(
                            f"[3.2] Task {task_id}: checkbox submit {checkbox_key}="
                            f"{answer_values} ({len(answer_values)} value(s))"
                        )
                        try:
                            resp = submit_answer_3_2_multiple(
                                session,
                                lesson_id,
                                task_id,
                                checkbox_key,
                                answer_values,
                                referer_path=referer_task,
                            )
                        except Exception:
                            print(f"[3.2] Submit task {task_id} failed:")
                            traceback.print_exc()
                            continue
                    else:
                        answer_value = best_match(
                            normalized_answer, options, threshold=ANSWER_SIMILARITY_THRESHOLD
                        )
                        if answer_value is None:
                            answer_values = best_matches_multiple(
                                normalized_answer, options, threshold=0.5
                            )
                            if len(answer_values) > 1:
                                checkbox_key = (
                                    f"questions[{qid}][]" if qid is not None else form_key
                                )
                                print(
                                    f"[3.2] Task {task_id}: multi-part answer -> "
                                    f"checkbox {checkbox_key}={answer_values}"
                                )
                                try:
                                    resp = submit_answer_3_2_multiple(
                                        session,
                                        lesson_id,
                                        task_id,
                                        checkbox_key,
                                        answer_values,
                                        referer_path=referer_task,
                                    )
                                except Exception:
                                    print(f"[3.2] Submit task {task_id} failed:")
                                    traceback.print_exc()
                                    continue
                            elif len(answer_values) == 1:
                                answer_value = answer_values[0]
                            else:
                                print(
                                    f"[3.2] No matching option for answer '{answer}' "
                                    f"(task {task_id}), skip."
                                )
                                continue
                        radio_key = (
                            f"questions[{qid}]" if qid is not None else form_key
                        )
                        print(
                            f"[3.2] Task {task_id}: radio submit {radio_key}={answer_value}"
                        )
                        try:
                            resp = submit_answer_3_2(
                                session,
                                lesson_id,
                                task_id,
                                radio_key,
                                answer_value,
                                referer_path=referer_task,
                            )
                        except Exception:
                            print(f"[3.2] Submit task {task_id} failed:")
                            traceback.print_exc()
                            continue
                else:
                    answer_value = str(normalized_answer).strip() if normalized_answer is not None else ""
                    if not answer_value:
                        print(f"[3.2] Task {task_id}: empty answer, skip.")
                        continue
                    try:
                        resp = submit_answer_3_2(
                            session,
                            lesson_id,
                            task_id,
                            form_key,
                            answer_value,
                            referer_path=referer_task,
                        )
                    except Exception:
                        print(f"[3.2] Submit task {task_id} failed:")
                        traceback.print_exc()
                        continue
            if resp.status_code in (200, 201, 204):
                preview = (resp.text or "")[:200].replace("\n", " ")
                print(f"[3.2] Submit lesson={lesson_id} task={task_id} -> {resp.status_code} {preview}")
            else:
                body = (resp.text or (resp.content.decode(errors="replace") if resp.content else ""))[:500]
                print(f"[3.2] Submit lesson={lesson_id} task={task_id} -> err {resp.status_code}: {body}")

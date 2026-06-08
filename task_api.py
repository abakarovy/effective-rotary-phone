"""Fetch task metadata via JSON API (no Selenium page load) and build parse result."""
import json
from typing import Any, Literal

import requests

ApiFormat = Literal["homework", "lesson", "training"]

from config import BASE_URL
from http_retry import request_with_retry

# Same shape as page_parser.parse_question_page()
ParsedTask = dict[str, Any]


def _apply_referer(session: requests.Session, referer_path: str) -> None:
    referer = f"{BASE_URL}{referer_path}"
    session.headers["Referer"] = referer
    session.headers["X-Referer"] = referer


def _parse_json_response(resp: requests.Response) -> dict | None:
    if resp.status_code not in (200, 304):
        return None
    if resp.status_code == 304 or not (resp.text or "").strip():
        return None
    try:
        data = resp.json() if getattr(resp, "json", None) else json.loads(resp.text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def fetch_homework_task(
    session: requests.Session,
    classwork_id: int | str,
    task_id: int | str,
) -> dict | None:
    """GET /api/homeworks/{classwork_id}/tasks/{task_id}"""
    url = f"{BASE_URL}/api/homeworks/{classwork_id}/tasks/{task_id}"
    _apply_referer(session, f"/classworks/{classwork_id}/tasks/{task_id}?page=1")
    try:
        resp = request_with_retry(
            session,
            "GET",
            url,
            headers={"Accept": "application/json"},
            timeout=15,
            label="homework task",
        )
        return _parse_json_response(resp)
    except Exception:
        return None


def fetch_lesson_task(
    session: requests.Session,
    lesson_id: int | str,
    task_id: int | str,
) -> dict | None:
    """GET /api/lessons/{lesson_id}/tasks/{task_id}"""
    url = f"{BASE_URL}/api/lessons/{lesson_id}/tasks/{task_id}"
    _apply_referer(session, f"/lessons/{lesson_id}/tasks/{task_id}")
    try:
        resp = request_with_retry(
            session,
            "GET",
            url,
            headers={"Accept": "application/json"},
            timeout=15,
            label="lesson task",
        )
        return _parse_json_response(resp)
    except Exception:
        return None


def fetch_training_task(
    session: requests.Session,
    training_id: int | str,
    task_id: int | str,
) -> dict | None:
    """GET /api/trainings/{training_id}/tasks/{task_id}"""
    url = f"{BASE_URL}/api/trainings/{training_id}/tasks/{task_id}"
    _apply_referer(session, f"/trainings/{training_id}/tasks/{task_id}")
    try:
        resp = request_with_retry(
            session,
            "GET",
            url,
            headers={"Accept": "application/json"},
            timeout=15,
            label="training task",
        )
        return _parse_json_response(resp)
    except Exception:
        return None


def questions_list(task_json: dict | None) -> list[dict]:
    if not isinstance(task_json, dict):
        return []
    qs = task_json.get("questions")
    if isinstance(qs, list) and qs:
        return [q for q in qs if isinstance(q, dict)]
    task = task_json.get("task")
    if isinstance(task, dict):
        qs = task.get("questions")
        if isinstance(qs, list):
            return [q for q in qs if isinstance(q, dict)]
    return []


def task_has_links_question(task_json: dict | None) -> bool:
    for q in questions_list(task_json):
        if q.get("type") == "links":
            return True
    return False


def _code_meta_from_task(task_json: dict, q: dict) -> tuple[int | None, str | None]:
    qid = q.get("id")
    tceid = q.get("test_case_execution_id")
    if tceid is None and isinstance(task_json.get("test_case_executions"), list):
        tce = task_json["test_case_executions"]
        if tce:
            first = tce[0]
            tceid = first.get("id") if isinstance(first, dict) else first
    if tceid is None:
        tceid = task_json.get("test_case_execution_id")
    if qid is None:
        return None, str(tceid) if tceid is not None else None
    return int(qid), str(tceid) if tceid is not None else None


TRAINING_SELECT_TYPES = frozenset({"select", "dropdown", "combobox", "single_select"})
TRAINING_RADIO_TYPES = frozenset({"radio"})
CHECKBOX_TYPES = frozenset(
    {"checkbox", "checkboxes", "multiple_choice", "multi_choice", "multi_select"}
)


def _truthy(val: Any) -> bool:
    if val is True or val == 1:
        return True
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "on")
    return False


def _allow_multiple_choice(q: dict) -> bool:
    for key in (
        "allow_multiple_choice",
        "allow_multiple_choices",
        "multiple_choice",
        "wk_allow_multiple_choice",
    ):
        if _truthy(q.get(key)):
            return True
    return False


def _is_checkbox_question(q: dict) -> bool:
    """Checkbox / multi-select (multipart key questions[id][])."""
    if _allow_multiple_choice(q):
        return True
    qtype = (q.get("type") or "").lower()
    if qtype in CHECKBOX_TYPES:
        return True
    for key in ("answers_content_type", "available_answers_content_type"):
        act = (q.get(key) or "").lower()
        if "checkbox" in act:
            return True
    for key in (
        "input_type",
        "widget_type",
        "answer_type",
        "selection_type",
        "answers_type",
    ):
        act = (q.get(key) or "").lower()
        if "checkbox" in act or act in ("multiple", "multi", "multi_select"):
            return True
    for key in (
        "max_answers",
        "maximum_answers",
        "max_answers_count",
        "max_selected",
        "maximum_number_of_answers",
        "max_number_of_answers",
        "maximum_selected_answers",
    ):
        try:
            if int(q[key]) > 1:
                return True
        except (TypeError, ValueError, KeyError):
            pass
    for key in (
        "min_answers",
        "minimum_answers",
        "min_selected",
        "minimum_number_of_answers",
        "minimum_number_of_correct_answers",
        "correct_answers_minimum",
    ):
        try:
            if int(q[key]) > 1:
                return True
        except (TypeError, ValueError, KeyError):
            pass
    return False


def _collect_choice_options(q: dict) -> list[tuple[str, str]]:
    """Options from answers and/or available_answers (select often uses available_answers)."""
    options: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key in ("answers", "available_answers"):
        for a in q.get(key) or []:
            if not isinstance(a, dict) or a.get("id") is None:
                continue
            val = str(a["id"])
            if val in seen:
                continue
            seen.add(val)
            options.append((str(a.get("content") or "").strip(), val))
    return options


def _is_training_select_question(q: dict) -> bool:
    """
    Detect dropdown/select in trainings API.
    Type is not always literally \"select\" — often \"answers\" + available_answers.
    """
    if _is_checkbox_question(q):
        return False
    qtype = (q.get("type") or "").lower()
    if qtype in TRAINING_SELECT_TYPES:
        return True
    if qtype in TRAINING_RADIO_TYPES:
        return False
    available = [a for a in (q.get("available_answers") or []) if isinstance(a, dict)]
    answers = [a for a in (q.get("answers") or []) if isinstance(a, dict)]
    if available and len(available) >= 2:
        return True
    act = (q.get("answers_content_type") or q.get("available_answers_content_type") or "").lower()
    if "select" in act or act == "dropdown":
        return True
    return False


def _choice_form_key(qid: int, api_format: ApiFormat, q: dict) -> str:
    """
    Multipart field name for single-value choice questions.
    Trainings (from browser curls):
      - select/dropdown -> questions[id][]
      - radio           -> questions[id]
    """
    if _is_checkbox_question(q):
        return f"questions[{qid}][]"
    if api_format == "training" and _is_training_select_question(q):
        return f"questions[{qid}][]"
    return f"questions[{qid}]"


def parse_task_from_api(task_json: dict | None, api_format: ApiFormat = "training") -> ParsedTask:
    """
    Build the same dict as page_parser.parse_question_page from task API JSON.
    api_format controls multipart field names for single-choice questions.
    """
    result: ParsedTask = {
        "question_form_key": None,
        "question_id": None,
        "is_text_input": False,
        "is_code": False,
        "is_multiple_choice": False,
        "is_select": False,
        "is_drag": False,
        "code_question_id": None,
        "test_case_execution_id": None,
        "options": [],
    }
    if not task_json:
        return result

    questions = questions_list(task_json)
    if not questions:
        return result

    q = questions[0]
    qid = q.get("id")
    if qid is None:
        return result
    qid = int(qid)
    qtype = (q.get("type") or "").lower()

    if qtype == "links":
        result["is_drag"] = True
        result["question_id"] = qid
        return result

    if qtype == "wk_programming":
        code_id, tceid = _code_meta_from_task(task_json, q)
        if code_id is not None:
            result["is_code"] = True
            result["code_question_id"] = code_id
            result["question_id"] = code_id
            result["question_form_key"] = f"questions[{code_id}][]"
            result["test_case_execution_id"] = tceid
        return result

    if _is_checkbox_question(q):
        result["is_multiple_choice"] = True
        result["question_form_key"] = f"questions[{qid}][]"
        result["question_id"] = qid
        result["options"] = _collect_choice_options(q)
        return result

    options = _collect_choice_options(q)
    if options and qtype not in ("open_answer", "text", "textarea", "string"):
        if api_format == "training" and _is_training_select_question(q):
            result["is_select"] = True
        result["question_form_key"] = _choice_form_key(qid, api_format, q)
        result["question_id"] = qid
        result["options"] = options
        return result

    result["is_text_input"] = True
    result["question_form_key"] = f"questions[{qid}][]"
    result["question_id"] = qid
    return result

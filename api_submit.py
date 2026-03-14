"""Submit answer attempt via API (multipart/form-data)."""
from typing import Any

import requests

from config import BASE_URL


def submit_answer_3_1(
    session: requests.Session,
    classwork_id: int | str,
    question_id: int | str,
    form_question_key: str,
    answer_value: str | int,
    referer_path: str | None = None,
) -> requests.Response:
    """POST to /api/homeworks/{classwork_id}/tasks/{question_id}/answer_attempts."""
    url = f"{BASE_URL}/api/homeworks/{classwork_id}/tasks/{question_id}/answer_attempts"
    referer_path = referer_path or f"/classworks/{classwork_id}/tasks/{question_id}?page=1"
    referer = f"{BASE_URL}{referer_path}"
    session.headers["Referer"] = referer
    session.headers["X-Referer"] = referer

    # multipart form: one field name=form_question_key, value=answer_value
    files = {}
    data = {form_question_key: (None, str(answer_value))}
    # requests will build multipart; we need to send as form data with one key
    # Actually the API expects multipart with Content-Disposition: form-data; name="questions[123]"
    payload = {form_question_key: str(answer_value)}
    resp = session.post(url, files=[(k, (None, v)) for k, v in payload.items()])
    return resp


def submit_answer_3_2(
    session: requests.Session,
    lesson_id: int | str,
    task_id: int | str,
    form_question_key: str,
    answer_value: str | int,
    referer_path: str | None = None,
) -> requests.Response:
    """POST to /api/lessons/{lesson_id}/tasks/{task_id}/answer_attempts."""
    url = f"{BASE_URL}/api/lessons/{lesson_id}/tasks/{task_id}/answer_attempts"
    referer_path = referer_path or f"/teacher/lessons/{lesson_id}/tasks/{task_id}"
    referer = f"{BASE_URL}{referer_path}"
    session.headers["Referer"] = referer
    session.headers["X-Referer"] = referer

    payload = {form_question_key: str(answer_value)}
    resp = session.post(url, files=[(k, (None, v)) for k, v in payload.items()])
    return resp


def submit_answer_3_2_multiple(
    session: requests.Session,
    lesson_id: int | str,
    task_id: int | str,
    form_question_key: str,
    answer_values: list[str | int],
    referer_path: str | None = None,
) -> requests.Response:
    """POST to /api/lessons/{lesson_id}/tasks/{task_id}/answer_attempts with multiple values for one key (checkboxes).
    Payload: one form-data part per selected value, all with name=form_question_key (e.g. questions[1207768][]).
    """
    url = f"{BASE_URL}/api/lessons/{lesson_id}/tasks/{task_id}/answer_attempts"
    referer_path = referer_path or f"/lessons/{lesson_id}/tasks/{task_id}"
    referer = f"{BASE_URL}{referer_path}"
    session.headers["Referer"] = referer
    session.headers["X-Referer"] = referer

    payload = [(form_question_key, (None, str(v))) for v in answer_values]
    resp = session.post(url, files=payload)
    return resp


def submit_answer_3_2_code(
    session: requests.Session,
    lesson_id: int | str,
    task_id: int | str,
    code_question_id: int | str,
    language: str,
    source_code: str,
    test_case_execution_id: str | int | None = None,
    referer_path: str | None = None,
) -> requests.Response:
    """POST to /api/lessons/{lesson_id}/tasks/{task_id}/answer_attempts with code-type payload (3.2).
    Payload per requirements.md: only questions[id][][language] and questions[id][][source_code].
    """
    url = f"{BASE_URL}/api/lessons/{lesson_id}/tasks/{task_id}/answer_attempts"
    referer_path = referer_path or f"/lessons/{lesson_id}/tasks/{task_id}"
    referer = f"{BASE_URL}{referer_path}"
    session.headers["Referer"] = referer
    session.headers["X-Referer"] = referer

    # Match requirements.md: questions[id][][language], questions[id][][source_code] (no test_case_execution_id)
    base = f"questions[{code_question_id}][]"
    source_code_bytes = (source_code or "").encode("utf-8")
    payload = [
        (f"{base}[language]", (None, language)),
        (f"{base}[source_code]", (None, source_code_bytes, "text/plain; charset=utf-8")),
    ]
    resp = session.post(url, files=payload)
    return resp

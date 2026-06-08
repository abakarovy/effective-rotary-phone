"""Shared drag-and-drop (links) parsing and API payload building."""
import re

from similarity import similarity


def parse_drag_pairs_from_excel(answer_text: str) -> list[tuple[str, str]]:
    """
    Parse '[left] -> [right]' pairs from Excel.
    Supports one pair per line OR several pairs in one cell separated by '.,'
    """
    if not answer_text:
        return []
    text = str(answer_text).replace("\r\n", "\n")
    segments: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # e.g. "[A] -> [B]., [C] -> [D]., [E] -> [F]."
        for part in re.split(r"\.\s*,\s*", line):
            part = part.strip().rstrip(".").strip()
            if part:
                segments.append(part)

    pairs: list[tuple[str, str]] = []
    for raw in segments:
        if "->" not in raw:
            continue
        left, right = raw.split("->", 1)
        left = left.strip()
        right = right.strip()
        if left.startswith("[") and left.endswith("]"):
            left = left[1:-1].strip()
        if right.startswith("[") and right.endswith("]"):
            right = right[1:-1].strip()
        if left and right:
            pairs.append((left, right))
    return pairs


def _normalize_match_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def best_id_by_text(text: str, options: list[tuple[str, int]], threshold: float = 0.7) -> int | None:
    best_ratio = 0.0
    best_id = None
    needle = _normalize_match_text(text)
    for opt_text, opt_id in options:
        r = similarity(needle, _normalize_match_text(opt_text))
        if r >= threshold and r > best_ratio:
            best_ratio = r
            best_id = opt_id
    return best_id


def build_drag_payload_from_api(
    task_json: dict,
    answer_text: str,
    *,
    threshold: float = 0.7,
) -> tuple[int | None, list[tuple[int, int]]]:
    """
    Build drag mappings from task API JSON (type links):
      questions[question_id][left_answer_id] = right_answer_id
    """
    if not isinstance(task_json, dict):
        return None, []
    questions = task_json.get("questions") or []
    if not isinstance(questions, list):
        task = task_json.get("task")
        if isinstance(task, dict):
            questions = task.get("questions") or []

    q_links = None
    for q in questions:
        if isinstance(q, dict) and q.get("type") == "links":
            q_links = q
            break
    if not q_links:
        return None, []

    qid = q_links.get("id")
    if qid is None:
        return None, []

    left_opts = []
    for a in q_links.get("answers") or []:
        if isinstance(a, dict) and a.get("id") is not None:
            left_opts.append((str(a.get("content") or "").strip(), int(a["id"])))
    right_opts = []
    for a in q_links.get("available_answers") or []:
        if isinstance(a, dict) and a.get("id") is not None:
            right_opts.append((str(a.get("content") or "").strip(), int(a["id"])))

    pairs = parse_drag_pairs_from_excel(answer_text)
    mappings: list[tuple[int, int]] = []
    used_left: set[int] = set()
    for left_txt, right_txt in pairs:
        left_id = best_id_by_text(left_txt, left_opts, threshold=threshold)
        right_id = best_id_by_text(right_txt, right_opts, threshold=threshold)
        if left_id is None or right_id is None or left_id in used_left:
            continue
        used_left.add(left_id)
        mappings.append((left_id, right_id))
    return int(qid), mappings

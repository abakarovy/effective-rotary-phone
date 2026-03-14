"""Answer text similarity for matching Excel answer to HTML option (radio)."""
from difflib import SequenceMatcher


def similarity(a: str, b: str) -> float:
    """0..1 ratio; 1 = identical."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    a, b = a.strip().lower(), b.strip().lower()
    return SequenceMatcher(None, a, b).ratio()


def best_match(
    answer: str | int | float,
    options: list[tuple[str, str]],
    threshold: float = 0.7,
) -> str | None:
    """
    Find option whose text best matches answer. options = [(display_text, value), ...].
    Returns value for the best match if ratio >= threshold, else None.
    """
    answer_str = str(answer).strip() if answer is not None else ""
    if not answer_str:
        return None
    best_ratio = 0.0
    best_value = None
    for text, value in options:
        r = similarity(answer_str, str(text).strip())
        if r >= threshold and r > best_ratio:
            best_ratio = r
            best_value = value
    return best_value

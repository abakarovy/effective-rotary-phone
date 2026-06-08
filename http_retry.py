"""Retry API requests when the server returns HTTP 500."""
import time

import requests

from config import API_RETRY_COUNT, API_RETRY_DELAY_SEC


def should_retry_status(status_code: int) -> bool:
    return status_code == 500


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    label: str | None = None,
    **kwargs,
) -> requests.Response:
    """GET/POST with retries on HTTP 500 internal server error."""
    method = method.upper()
    last_resp: requests.Response | None = None
    tag = f" {label}" if label else ""

    for attempt in range(API_RETRY_COUNT):
        if method == "GET":
            last_resp = session.get(url, **kwargs)
        elif method == "POST":
            last_resp = session.post(url, **kwargs)
        else:
            raise ValueError(f"Unsupported method: {method}")

        if not should_retry_status(last_resp.status_code):
            return last_resp

        if attempt < API_RETRY_COUNT - 1:
            print(
                f"[api]{tag} HTTP 500, retry {attempt + 2}/{API_RETRY_COUNT} in {API_RETRY_DELAY_SEC}s ..."
            )
            time.sleep(API_RETRY_DELAY_SEC)

    return last_resp  # type: ignore[return-value]

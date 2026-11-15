"""HTTP session with retry/back-off tuned for IRSA.

IRSA regularly answers ``503 Server busy, retry later`` under load, so every
request goes through :func:`request_with_retries`.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any

import requests

from .config import get_settings

log = logging.getLogger(__name__)

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}

_local = threading.local()


class HttpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, url: str | None = None):
        super().__init__(message)
        self.status = status
        self.url = url


def get_session() -> requests.Session:
    """One ``requests.Session`` per thread (Sessions are not thread-safe)."""
    sess = getattr(_local, "session", None)
    if sess is None:
        sess = requests.Session()
        sess.headers["User-Agent"] = get_settings().user_agent
        _local.session = sess
    return sess


def _retry_after(resp: requests.Response) -> float | None:
    ra = resp.headers.get("Retry-After")
    if not ra:
        return None
    try:
        return min(float(ra), 60.0)
    except ValueError:
        return None


def request_with_retries(
    session: requests.Session,
    method: str,
    url: str,
    *,
    retries: int | None = None,
    backoff: float | None = None,
    timeout: float | None = None,
    stream: bool = False,
    **kwargs: Any,
) -> requests.Response:
    """Perform an HTTP request, retrying transient failures with exponential back-off.

    Raises :class:`HttpError` on a non-retryable status or when retries run out.
    """
    s = get_settings()
    retries = s.http_retries if retries is None else retries
    backoff = s.http_backoff_s if backoff is None else backoff
    timeout = s.http_timeout_s if timeout is None else timeout
    last_err: str = ""
    last_status: int | None = None
    for attempt in range(retries + 1):
        try:
            resp = session.request(method, url, timeout=timeout, stream=stream, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_err, last_status = f"{type(exc).__name__}: {exc}", None
            wait = None
        else:
            if resp.status_code < 400:
                return resp
            last_status = resp.status_code
            body = resp.text[:200].strip() if not stream else ""
            last_err = f"HTTP {resp.status_code} {body}".strip()
            if resp.status_code not in RETRY_STATUS:
                raise HttpError(last_err, status=resp.status_code, url=url)
            wait = _retry_after(resp)
            resp.close()
        if attempt < retries:
            delay = wait if wait is not None else backoff * (2 ** attempt) * (0.75 + random.random() / 2)
            log.info("Retrying %s in %.1fs (attempt %d/%d): %s", url, delay, attempt + 1, retries, last_err)
            time.sleep(delay)
    raise HttpError(f"Giving up after {retries + 1} attempts: {last_err}", status=last_status, url=url)

"""One shared HTTP client with timeouts, retries and per-host throttling.

Reusing one client keeps connections alive (no new TLS handshake per call) and bounds the number of open
sockets, which matters for a collector that makes thousands of calls in a run.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from .. import __version__
from ..errors import StoreError

RETRY_STATUS = {429, 500, 502, 503, 504}

_client: httpx.Client | None = None
_lock = threading.Lock()
_last_call: dict[str, float] = {}


def client() -> httpx.Client:
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
                headers={"User-Agent": f"rankwell/{__version__}"},
                follow_redirects=True,
            )
        return _client


def close() -> None:
    global _client
    with _lock:
        if _client is not None:
            _client.close()
            _client = None


def throttle(bucket: str, min_interval: float) -> None:
    """Wait so calls in one bucket are at least min_interval seconds apart."""
    with _lock:
        wait = min_interval - (time.monotonic() - _last_call.get(bucket, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_call[bucket] = time.monotonic()


def request(
    method: str,
    url: str,
    *,
    retries: int = 3,
    backoff: float = 2.0,
    sleep: Any = time.sleep,
    **kwargs: Any,
) -> httpx.Response:
    """Send a request; retry transport errors and 429/5xx (only for idempotent methods); raise StoreError."""
    attempts = retries if method in ("GET", "HEAD", "PATCH", "PUT", "DELETE") else 1
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            r = client().request(method, url, **kwargs)
        except httpx.TransportError as e:
            last = e
        else:
            if r.status_code not in RETRY_STATUS or attempt == attempts - 1:
                return r
            last = StoreError(f"{r.status_code} from {httpx.URL(url).host}", r.status_code)
            retry_after = r.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                sleep(min(int(retry_after), 60))
                continue
        if attempt < attempts - 1:
            sleep(backoff * (attempt + 1))
    host = httpx.URL(url).host
    raise StoreError(f"Could not reach {host}: {last}") from last

"""Small helpers shared across modules."""

from __future__ import annotations

import datetime as dt
import re
import unicodedata


def today() -> str:
    return dt.date.today().isoformat()


def days_ago(n: int, start: str | None = None) -> str:
    base = dt.date.fromisoformat(start) if start else dt.date.today()
    return (base - dt.timedelta(days=n)).isoformat()


def now_utc() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-") or "app"

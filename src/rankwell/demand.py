"""Weekly Google demand of tracked keywords, collected a few dozen at a time by the daily run.

Each keyword is checked about once a week (≈4 cached Google autocomplete calls, and a Google Trends call for
the first few). A day's run checks at most MAX_PER_RUN keywords, the ones checked longest ago first, so a large
keyword list spreads over the week. When Google says slow down, the rest waits for the next run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import research
from .db import Database
from .stores import google
from .util import days_ago

WEEK = 7
MAX_PER_RUN = 40
MAX_TRENDS_PER_RUN = 8

Probe = Callable[[str, str], dict[str, Any] | None]
Trends = Callable[[str, str], dict[str, Any] | None]


def due(db: Database, day: str, app_ids: list[int] | None = None) -> list[tuple[str, str]]:
    """(country, term) of tracked keywords not checked in the last week, never-checked first, then oldest."""
    if app_ids is not None and not app_ids:
        return []
    scope = f"AND k.app_id IN ({', '.join('?' for _ in app_ids)})" if app_ids else ""
    rows = db.all(
        "SELECT k.country, k.term, max(g.date) AS last FROM keywords k JOIN apps a ON a.id = k.app_id "
        "LEFT JOIN google_demand g ON g.country = k.country AND g.term = k.term "
        f"WHERE a.archived_at IS NULL {scope} GROUP BY k.country, k.term "
        "HAVING last IS NULL OR last <= ? ORDER BY last IS NOT NULL, last, k.country, k.term",
        (*(app_ids or []), days_ago(WEEK, day)))
    return [(str(r["country"]), str(r["term"])) for r in rows]


def collect(db: Database, day: str, app_ids: list[int] | None = None, *, probe: Probe = google.first_suggested,
            trends: Trends = google.interest, limit: int = MAX_PER_RUN) -> dict[str, Any]:
    """Check up to `limit` due keywords on Google and store one google_demand row each. Never raises."""
    todo = due(db, day, app_ids)
    checked, failed, trend_calls = 0, 0, 0
    for country, term in todo[:limit]:
        found = probe(term, country)
        if found is None:
            failed += 1
            if google.blocked("suggest"):
                break
            continue
        trend = None
        if trend_calls < MAX_TRENDS_PER_RUN and not google.blocked("trends"):
            trend_calls += 1
            trend = trends(term, country)
        db.upsert("google_demand", [{
            "country": country, "term": term, "date": day,
            "demand": research.popularity_value(found["typed"], len(term), found["position"]),
            "suggested_after_chars": found["typed"], "position": found["position"],
            "trend_change_pct": trend.get("change_pct") if trend else None,
            "trend_low_volume": int(bool(trend["low_volume"])) if trend else None,
        }], ["country", "term", "date"])
        checked += 1
    return {"checked": checked, "failed": failed, "due": max(0, len(todo) - checked)}


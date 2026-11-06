"""Import from the original ASO tracker (Supabase over PostgREST). Read-only, idempotent.

Projects become apps (with countries, keywords and competitors); ranks, listing snapshots, listing changes,
reviews, keyword scores, keyword top 10, chart positions of tracked apps and App Store Connect data come
along. Only GET requests are sent. Running it again updates what was imported before.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .. import apps
from ..db import Database
from ..errors import NotConfigured, StoreError
from ..stores import http
from ..util import now_utc

DEFAULT_ENV = Path("tracker/.env")  # relative to the folder the command runs in
PAGE = 1000
CHART_RE = re.compile(r"^top(free|paid|grossing)-(\d+)$")

Row = dict[str, Any]
Log = Callable[[str], None]


def read_env(path: Path) -> dict[str, str]:
    """KEY=value lines; spaces around '=' and quotes are allowed, comments are skipped."""
    if not path.exists():
        raise NotConfigured(f"No tracker settings at {path}. Pass --env with the tracker's .env file.")
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    return out


class Supabase:
    """Read-only PostgREST client: paginated GETs on the shared HTTP client."""

    def __init__(self, url: str, key: str) -> None:
        self.base = url.rstrip("/") + "/rest/v1"
        self.headers = {"apikey": key, "Accept": "application/json"}
        if key.startswith("eyJ"):  # legacy service_role keys are JWTs and also go in Authorization
            self.headers["Authorization"] = f"Bearer {key}"

    def pages(self, table: str, order: str = "id.asc", **filters: str) -> Iterator[list[Row]]:
        """Every row of a table, a page at a time. A table the tracker does not have yields nothing."""
        offset = 0
        while True:
            params = {"select": "*", "order": order, "limit": str(PAGE), "offset": str(offset), **filters}
            r = http.request("GET", f"{self.base}/{table}", params=params, headers=self.headers)
            if r.status_code == 404:
                return
            if r.status_code >= 400:
                raise StoreError(f"Supabase GET {table} → {r.status_code}: {r.text[:200]}", r.status_code)
            batch = list(r.json())
            if batch:
                yield batch
            if len(batch) < PAGE:
                return
            offset += PAGE

    def rows(self, table: str, order: str = "id.asc", **filters: str) -> list[Row]:
        return [r for page in self.pages(table, order, **filters) for r in page]


def connect(env_path: Path | None = None) -> Supabase:
    env = read_env(env_path or DEFAULT_ENV)
    url, key = env.get("SUPABASE_URL"), env.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise NotConfigured("The tracker .env needs SUPABASE_URL and SUPABASE_SERVICE_KEY.")
    return Supabase(url, key)


def run(db: Database, source: Supabase, log: Log | None = None) -> dict[str, Any]:
    """Import everything. Returns counts per table."""
    say = log or (lambda _msg: None)
    run_id = int(db.execute("INSERT INTO runs (kind, started_at) VALUES ('import', ?)", (now_utc(),)).lastrowid or 0)
    counts: dict[str, int] = {}
    status = "failed"
    try:
        tracker_apps = {int(a["id"]): a for a in source.rows("apps")}
        keywords = {int(k["id"]): k for k in source.rows("keywords")}
        links = source.rows("project_apps", order="project_id.asc,app_id.asc")
        projects = source.rows("projects")
        counts.update(import_projects(db, projects, links, keywords, tracker_apps))
        say(f"{counts['apps']} apps, {counts['keywords']} keywords, {counts['competitors']} competitors")
        kw = {i: (str(k["country"]).lower(), str(k["term"]).strip().lower()) for i, k in keywords.items()}
        tracked = sorted({int(a["store_id"]) for a in db.all("SELECT store_id FROM apps WHERE store_id IS NOT NULL")}
                         | {int(c["store_id"]) for c in db.all("SELECT store_id FROM competitors")})
        steps: list[tuple[str, str, Callable[[Row], Row | None], dict[str, str], list[str]]] = [
            ("ranks", "rank_snapshots", lambda r: map_rank(r, kw), {}, ["date", "country", "term", "store_id"]),
            ("snapshots", "app_snapshots", map_snapshot, {}, ["store_id", "country", "date"]),
            ("changes", "listing_changes", map_change, {}, ["store_id", "country", "date", "field"]),
            ("reviews", "reviews", map_review, {}, ["id"]),
            ("keyword_scores", "keyword_scores", lambda r: map_score(r, kw), {}, ["country", "term", "date"]),
            ("keyword_top", "keyword_top10", lambda r: map_top(r, kw), {}, ["date", "country", "term", "position"]),
            ("chart_positions", "chart_snapshots", map_chart,
             {"app_id": f"in.({','.join(map(str, tracked))})"} if tracked else {"app_id": "eq.0"},
             ["date", "country", "genre_id", "chart", "store_id"]),
            ("asc_daily", "asc_daily", map_asc_daily, {}, ["store_id", "date", "metric", "source"]),
            ("asc_report_rows", "asc_report_rows", map_asc_row, {}, ["store_id", "report_name", "row_hash"]),
        ]
        for name, table, fn, filters, keys in steps:
            n = 0
            for page in source.pages(table, **filters):
                rows = _dedupe([m for m in map(fn, page) if m is not None], keys)
                n += db.upsert(name, rows, keys)
            counts[name] = n
            say(f"{name}: {n}")
        status = "ok"
    finally:
        db.execute("UPDATE runs SET finished_at = ?, status = ?, summary = ? WHERE id = ?",
                   (now_utc(), status, json.dumps({"source": "tracker", "counts": counts}), run_id))
    return {"run_id": run_id, "status": status, "counts": counts}


def _dedupe(rows: list[Row], keys: list[str]) -> list[Row]:
    """Last row wins per key (two tracker projects can share a term)."""
    return list({tuple(r[k] for k in keys): r for r in rows}.values())


# Projects → apps


def import_projects(db: Database, projects: list[Row], links: list[Row], keywords: dict[int, Row],
                    tracker_apps: dict[int, Row]) -> dict[str, int]:
    counts = {"apps": 0, "competitors": 0, "keywords": 0}
    for p in projects:
        app = _match_or_add(db, p)
        counts["apps"] += 1
        competitors = [int(link["app_id"]) for link in links
                       if link["project_id"] == p["id"] and link["role"] == "competitor"]
        for sid in competitors:
            if sid != app.get("store_id"):
                apps.add_competitor(db, app["id"], sid, str(tracker_apps.get(sid, {}).get("name") or sid))
                counts["competitors"] += 1
        by_country: dict[str, list[str]] = {}
        for k in keywords.values():
            if k.get("project_id") == p["id"] and k.get("active", True):
                by_country.setdefault(str(k["country"]).lower(), []).append(str(k["term"]))
        for country, terms in by_country.items():
            apps.add_keywords(db, app["id"], terms, country)
            counts["keywords"] += len({t.strip().lower() for t in terms})
    return counts


def _match_or_add(db: Database, p: Row) -> apps.App:
    store_id = int(p["app_id"]) if p.get("app_id") else None
    slug = str(p["slug"])
    countries = [str(c).lower() for c in (p.get("countries") or ["us"])]
    row = db.one("SELECT * FROM apps WHERE store_id = ?", (store_id,)) if store_id else None
    if row is None:
        row = db.one("SELECT * FROM apps WHERE slug = ?", (slug,))
        if row and store_id and row["store_id"] and row["store_id"] != store_id:  # same name, another app
            slug = f"{slug}-{p['id']}"
            row = db.one("SELECT * FROM apps WHERE slug = ?", (slug,))
    if row is None:
        app = apps.add(db, str(p["name"]), store_id=store_id, bundle_id=p.get("bundle_id"), countries=countries,
                       slug=slug)
    else:
        fields: dict[str, Any] = {"name": str(p["name"])}
        if p.get("bundle_id"):
            fields["bundle_id"] = p["bundle_id"]
        if store_id and not row["store_id"]:
            fields["store_id"] = store_id
        app = apps.update(db, row["id"], **fields)
        apps.set_countries(db, app["id"], list(dict.fromkeys([*app["countries"], *countries])))
        app = apps.get(db, app["id"])
    if p.get("active") is False and not app.get("archived_at"):
        apps.archive(db, app["id"])
    return app


# Row mappers: tracker row → Rankwell row (None to skip)


def map_rank(r: Row, kw: dict[int, tuple[str, str]]) -> Row | None:
    k = kw.get(int(r["keyword_id"]))
    if not k:
        return None
    return {"date": r["run_date"], "country": k[0], "term": k[1], "store_id": int(r["app_id"]), "rank": r.get("rank")}


def map_snapshot(r: Row) -> Row:
    return {
        "store_id": int(r["app_id"]), "country": str(r["country"]).lower(), "date": r["run_date"],
        "title": r.get("title"), "subtitle": r.get("subtitle"), "version": r.get("version"),
        "rating_count": r.get("rating_count"), "rating_avg": _float(r.get("rating_avg")),
        "price": _float(r.get("price")), "description": r.get("description"), "release_notes": r.get("release_notes"),
        "screenshots": r.get("screenshot_urls"),
    }


def map_change(r: Row) -> Row:
    return {"store_id": int(r["app_id"]), "country": str(r["country"]).lower(), "date": r["run_date"],
            "field": r["field"], "old": r.get("old_value"), "new": r.get("new_value")}


def map_review(r: Row) -> Row | None:
    if r.get("rating") is None:
        return None
    return {"id": str(r["id"]), "store_id": int(r["app_id"]), "country": str(r["country"]).lower(),
            "rating": int(r["rating"]), "title": r.get("title"), "content": r.get("content"),
            "author": r.get("author"), "version": r.get("version"), "reviewed_at": r.get("reviewed_at")}


def map_score(r: Row, kw: dict[int, tuple[str, str]]) -> Row | None:
    k = kw.get(int(r["keyword_id"]))
    if not k:
        return None
    return {"country": k[0], "term": k[1], "date": r["run_date"], "popularity": r.get("popularity_proxy"),
            "difficulty": r.get("difficulty"), "median_ratings": r.get("median_top10_ratings"),
            "title_match": _float(r.get("title_match_top10"))}


def map_top(r: Row, kw: dict[int, tuple[str, str]]) -> Row | None:
    k = kw.get(int(r["keyword_id"]))
    if not k or r.get("app_id") is None:
        return None
    return {"date": r["run_date"], "country": k[0], "term": k[1], "position": int(r["rank"]),
            "store_id": int(r["app_id"]), "name": r.get("app_name"), "ratings": r.get("ratings")}


def map_chart(r: Row) -> Row | None:
    m = CHART_RE.match(str(r.get("chart") or ""))
    if not m:
        return None
    return {"date": r["run_date"], "country": str(r["country"]).lower(), "genre_id": int(m.group(2)),
            "chart": m.group(1), "store_id": int(r["app_id"]), "rank": int(r["rank"])}


def map_asc_daily(r: Row) -> Row | None:
    if r.get("app_id") is None:
        return None
    return {"store_id": int(r["app_id"]), "date": r["report_date"], "metric": r["metric"],
            "source": r.get("source") or "all", "value": int(r["value"])}


def map_asc_row(r: Row) -> Row | None:
    if r.get("app_id") is None:
        return None
    return {"store_id": int(r["app_id"]), "report_name": r["report_name"], "report_date": r["report_date"],
            "row_hash": r["row_hash"], "data": r.get("data") or {}}


def _float(v: Any) -> float | None:
    return None if v is None else float(v)

"""Daily collection: listing snapshots and changes, keyword ranks, reviews, charts, App Store Connect analytics.

Per app and country: one lookup for the app and its competitors, a search per tracked keyword, the newest
reviews and the category charts. App Store Connect analytics run once per live app when a key is set.
Every step is recorded on its own: a failing step or app is noted in the run summary and the rest goes on.
Running twice on one day rewrites that day's rows.
"""

from __future__ import annotations

import csv
import datetime as dt
import fcntl
import gzip
import hashlib
import io
import json
import os
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from . import apps, config, paths, plans, secrets
from .db import Database
from .errors import RankwellError, StoreError
from .stores import appstore, http
from .stores.asc import ASC
from .util import days_ago, now_utc, today

CHANGE_FIELDS = ("title", "subtitle", "version", "description", "screenshots", "price")
CHART_KINDS = ("free", "grossing")
TOP_N = 10
SEARCH_LIMIT = 200
MAX_ERRORS = 25
LOCK_NAME = "collect.lock"
ASC_REPORTS = ("App Store Discovery and Engagement Standard", "App Downloads Standard")
ASC_FRESH_DAYS = 5  # App Store Connect reports lag 1-2 days; older than this means data stopped flowing

Log = Callable[[str], None]
AscFactory = Callable[[], ASC | None]


class CollectBusy(RankwellError):
    """Another collection holds the lock."""


# Running


def run(db: Database, app_refs: list[str] | None = None, on: dt.date | str | None = None, *,
        asc: AscFactory | None = None, log: Log | None = None) -> dict[str, Any]:
    """Collect every active app (or the given ones) for one day. Returns the run summary."""
    day = on.isoformat() if isinstance(on, dt.date) else (on or today())
    targets = [apps.get(db, r) for r in app_refs] if app_refs else apps.list_apps(db)
    mark_stale_runs(db)
    run_id = int(db.execute("INSERT INTO runs (kind, started_at) VALUES ('collect', ?)", (now_utc(),)).lastrowid or 0)
    ctx = _Context(db, day, asc or _default_asc, log or (lambda _msg: None))
    results: dict[str, dict[str, Any]] = {}
    try:
        for app in targets:
            ctx.log(f"{app['name']}: collecting")
            try:
                results[app["slug"]] = _collect_app(ctx, app)
            except Exception as e:  # one app must never stop the others
                results[app["slug"]] = {**_empty_counts(), "status": "failed", "errors": [_err("app", e)]}
            ctx.log(f"{app['name']}: {results[app['slug']]['status']}")
    finally:
        statuses = {r["status"] for r in results.values()}
        status = "ok" if statuses <= {"ok"} else "failed" if statuses == {"failed"} else "partial"
        if len(results) < len(targets):
            status = "failed" if not results else "partial"
        summary = {"date": day, "apps": results}
        db.execute("UPDATE runs SET finished_at = ?, status = ?, summary = ? WHERE id = ?",
                   (now_utc(), status, json.dumps(summary, ensure_ascii=False, default=str), run_id))
    return {"run_id": run_id, "date": day, "status": status, "apps": results}


def _default_asc() -> ASC | None:
    return ASC() if secrets.has_asc_key() else None


class _Context:
    """State shared across the apps of one run: the day, a cache of store calls, the ASC client."""

    def __init__(self, db: Database, day: str, asc: AscFactory, log: Log) -> None:
        self.db, self.day, self.log = db, day, log
        self._asc_factory = asc
        self._asc: ASC | None = None
        self._asc_loaded = False
        self._cache: dict[tuple[Any, ...], Any] = {}

    def cached(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Share identical store calls between apps (a competitor or keyword tracked twice is fetched once)."""
        key = (fn, *args)
        if key not in self._cache:
            self._cache[key] = fn(*args)
        return self._cache[key]

    def asc(self) -> ASC | None:
        if not self._asc_loaded:
            self._asc_loaded = True
            self._asc = self._asc_factory()
        return self._asc


def _empty_counts() -> dict[str, Any]:
    return {"snapshots": 0, "changes": 0, "keywords": 0, "ranks": 0, "reviews": 0, "charts": 0, "asc": None}


def _err(step: str, e: Exception) -> str:
    text = str(e) if isinstance(e, RankwellError) else f"{type(e).__name__}: {e}"
    return f"{step}: {text[:300]}"


class _AppResult:
    """Counts, notes and errors of one app; each step is tried on its own."""

    def __init__(self) -> None:
        self.counts = _empty_counts()
        self.errors: list[str] = []
        self.notes: list[str] = []

    def step(self, name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            self.errors.append(_err(name, e))
            return None

    def add(self, key: str, n: int | None) -> None:
        self.counts[key] += n or 0

    def summary(self) -> dict[str, Any]:
        out = dict(self.counts)
        extra = len(self.errors) - MAX_ERRORS
        out["errors"] = self.errors[:MAX_ERRORS] + ([f"… {extra} more"] if extra > 0 else [])
        out["notes"] = self.notes
        asc_ok = bool(out["asc"]) and not str(out["asc"]).startswith("skipped")
        collected = any(out[k] for k in ("snapshots", "keywords", "reviews", "charts")) or asc_ok
        out["status"] = "ok" if not self.errors else "partial" if collected else "failed"
        return out


def _collect_app(ctx: _Context, app: apps.App) -> dict[str, Any]:
    res = _AppResult()
    ids = apps.tracked_store_ids(ctx.db, app)
    for country in app["countries"] or ["us"]:
        found: dict[int, appstore.App] = {}
        if ids:
            found = res.step(f"{country} · listings", _listings, ctx, res, ids, country) or {}
        if app.get("store_id") and not app.get("genre_id") and found.get(app["store_id"], {}).get("primaryGenreId"):
            genre_id = int(found[app["store_id"]]["primaryGenreId"])
            app = res.step("category", apps.update, ctx.db, app["id"], genre_id=genre_id) or app
        for kw in apps.keywords(ctx.db, app["id"], country):
            n = res.step(f"{country} · rank '{kw['term']}'", _save_ranks, ctx, kw["term"], country, ids)
            if n is not None:
                res.add("keywords", 1)
                res.add("ranks", n)
        if not ids:
            continue
        res.add("reviews", res.step(f"{country} · reviews", _save_reviews, ctx, ids, country))
        genre = _genre(app, found)
        for kind in CHART_KINDS if genre else ():
            res.add("charts", res.step(f"{country} · {kind} chart", _save_chart, ctx, genre, country, kind, ids))
    if app.get("store_id"):
        res.counts["asc"] = res.step("App Store Connect", _analytics, ctx, int(app["store_id"]))
        if ctx.asc() is not None:
            res.add("reviews", res.step("App Store Connect · reviews", _asc_reviews, ctx, app))
    return res.summary()


def _listings(ctx: _Context, res: _AppResult, ids: list[int], country: str) -> dict[int, appstore.App]:
    found: dict[int, appstore.App] = ctx.cached(_lookup, tuple(ids), country)
    missing = [i for i in ids if i not in found]
    if missing:
        res.notes.append(f"{country}: not in the {country.upper()} store: " + ", ".join(map(str, missing)))
    n, c = _save_snapshots(ctx, found, country)
    res.add("snapshots", n)
    res.add("changes", c)
    return found


def _analytics(ctx: _Context, store_id: int) -> str | None:
    client = ctx.asc()
    return None if client is None else sync_asc(ctx.db, client, store_id)


def _save_reviews(ctx: _Context, ids: list[int], country: str) -> int:
    n = 0
    for sid in ids:
        rows: list[dict[str, Any]] = ctx.cached(appstore.reviews, sid, country)
        n += ctx.db.upsert("reviews", rows, ["id"])
    return n


def _asc_reviews(ctx: _Context, app: apps.App) -> int:
    """The app's own reviews from App Store Connect, all countries, last two weeks. The public feed stopped
    serving most requests in November 2026; this is the official source for the user's own apps."""
    from . import replies  # pulls in the listing module, which the collector does not need otherwise

    client = ctx.asc()
    if client is None:
        return 0
    rows = []
    for r in replies.asc_reviews(client, app, days=ASC_REVIEW_DAYS, max_rating=5):
        dup = ctx.db.value("SELECT 1 FROM reviews WHERE store_id = ? AND id NOT LIKE 'asc-%' AND author = ? "
                           "AND COALESCE(title, '') = ? AND substr(reviewed_at, 1, 10) = substr(?, 1, 10)",
                           (int(app["store_id"]), r["author"], r["title"], r["created"]))
        if dup:  # already saved from the public feed
            continue
        rows.append({"id": f"asc-{r['review_id']}", "store_id": int(app["store_id"]),
                     "country": TERRITORIES.get(r["territory"], r["territory"].lower()), "rating": r["rating"],
                     "title": r["title"], "content": r["body"], "author": r["author"], "version": None,
                     "reviewed_at": r["created"]})
    return ctx.db.upsert("reviews", rows, ["id"])


# App Store Connect territories (ISO 3166-1 alpha-3) to the two-letter storefront codes the rest of Rankwell
# uses; the rest are stored lowercased as they come.
TERRITORIES = {
    "USA": "us", "GBR": "gb", "CAN": "ca", "AUS": "au", "NZL": "nz", "IRL": "ie", "DEU": "de", "FRA": "fr",
    "ESP": "es", "ITA": "it", "NLD": "nl", "BEL": "be", "CHE": "ch", "AUT": "at", "SWE": "se", "NOR": "no",
    "DNK": "dk", "FIN": "fi", "POL": "pl", "PRT": "pt", "CZE": "cz", "GRC": "gr", "HUN": "hu", "ROU": "ro",
    "TUR": "tr", "RUS": "ru", "UKR": "ua", "ISR": "il", "ARE": "ae", "SAU": "sa", "EGY": "eg", "ZAF": "za",
    "NGA": "ng", "KEN": "ke", "IND": "in", "PAK": "pk", "BGD": "bd", "LKA": "lk", "NPL": "np", "IDN": "id",
    "MYS": "my", "SGP": "sg", "THA": "th", "VNM": "vn", "PHL": "ph", "CHN": "cn", "HKG": "hk", "TWN": "tw",
    "JPN": "jp", "KOR": "kr", "BRA": "br", "MEX": "mx", "ARG": "ar", "CHL": "cl", "COL": "co", "PER": "pe",
}
ASC_REVIEW_DAYS = 14


def _genre(app: apps.App, found: dict[int, appstore.App]) -> int | None:
    """The app's category; before launch, the competitors' most common one."""
    if app.get("genre_id"):
        return int(app["genre_id"])
    own = found.get(app["store_id"]) if app.get("store_id") else None
    if own and own.get("primaryGenreId"):
        return int(own["primaryGenreId"])
    genres = Counter(int(r["primaryGenreId"]) for r in found.values() if r.get("primaryGenreId"))
    return genres.most_common(1)[0][0] if genres else None


# Listings and changes


def _lookup(ids: tuple[int, ...], country: str) -> dict[int, appstore.App]:
    return appstore.lookup(list(ids), country)


def _subtitle(store_id: int, country: str) -> str | None:
    try:
        return appstore.subtitle(store_id, country)
    except StoreError:
        return None


def _save_snapshots(ctx: _Context, found: dict[int, appstore.App], country: str) -> tuple[int, int]:
    snaps, changes = [], []
    for sid, result in found.items():
        snap = {"store_id": sid, "country": country, "date": ctx.day, **appstore.snapshot_fields(result)}
        snap["subtitle"] = ctx.cached(_subtitle, sid, country)
        snaps.append(snap)
        prev = previous_values(ctx.db, sid, country, ctx.day)
        changes += [{"store_id": sid, "country": country, "date": ctx.day, **c} for c in diff(prev, snap)]
    with ctx.db.tx():
        ctx.db.upsert("snapshots", snaps, ["store_id", "country", "date"])
        for s in snaps:
            ctx.db.execute("DELETE FROM changes WHERE store_id = ? AND country = ? AND date = ?",
                           (s["store_id"], country, ctx.day))
        ctx.db.upsert("changes", changes, ["store_id", "country", "date", "field"])
    return len(snaps), len(changes)


def previous_values(db: Database, store_id: int, country: str, day: str) -> dict[str, Any]:
    """The latest known value of each tracked field before a day (a missing value does not count)."""
    cols = ", ".join(CHANGE_FIELDS)
    rows = db.all(f"SELECT {cols} FROM snapshots WHERE store_id = ? AND country = ? AND date < ? "
                  "ORDER BY date DESC LIMIT 30", (store_id, country, day))
    out: dict[str, Any] = {}
    for f in CHANGE_FIELDS:
        value = next((r[f] for r in rows if r[f] is not None), None)
        out[f] = json.loads(value) if f == "screenshots" and isinstance(value, str) else value
    return out


def diff(prev: dict[str, Any], snap: dict[str, Any]) -> list[dict[str, Any]]:
    """Changed fields between two listings: [{field, old, new}]. Unknown values on either side are skipped."""
    out = []
    for f in CHANGE_FIELDS:
        old, new = prev.get(f), snap.get(f)
        if old is None or new is None or _same(f, old, new):
            continue
        out.append({"field": f, "old": _text(old), "new": _text(new)})
    return out


def _same(field: str, old: Any, new: Any) -> bool:
    if field == "screenshots":
        return [_image_path(u) for u in old] == [_image_path(u) for u in new]
    if field == "price":
        return abs(float(old) - float(new)) < 0.005
    return str(old).strip() == str(new).strip()


def _image_path(url: str) -> str:
    """Screenshot URLs come from rotating CDN hosts (is1-ssl, is2-ssl...); compare the path only."""
    return re.sub(r"^https?://[^/]+", "", str(url))


def _text(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False) if isinstance(v, list | dict) else str(v)


# Ranks, reviews, charts


def _save_ranks(ctx: _Context, term: str, country: str, ids: list[int]) -> int:
    results: list[appstore.App] = ctx.cached(appstore.search, term, country, SEARCH_LIMIT)
    order = [r.get("trackId") for r in results]
    ranks = [{"date": ctx.day, "country": country, "term": term, "store_id": sid,
              "rank": order.index(sid) + 1 if sid in order else None} for sid in ids]
    top = [{"date": ctx.day, "country": country, "term": term, "position": i + 1, "store_id": int(r["trackId"]),
            "name": r.get("trackName"), "ratings": r.get("userRatingCount")}
           for i, r in enumerate(results[:TOP_N]) if r.get("trackId")]
    with ctx.db.tx():
        ctx.db.upsert("ranks", ranks, ["date", "country", "term", "store_id"])
        ctx.db.execute("DELETE FROM keyword_top WHERE date = ? AND country = ? AND term = ?", (ctx.day, country, term))
        ctx.db.upsert("keyword_top", top, ["date", "country", "term", "position"])
    return len(ranks)


def _save_chart(ctx: _Context, genre: int, country: str, kind: str, ids: list[int]) -> int:
    entries: list[dict[str, Any]] = ctx.cached(appstore.chart, genre, country, kind, 100)
    wanted = set(ids)
    rows = [{"date": ctx.day, "country": country, "genre_id": genre, "chart": kind, "store_id": e["store_id"],
             "rank": e["rank"]} for e in entries if e["store_id"] in wanted]
    marks = ",".join("?" for _ in ids)
    with ctx.db.tx():
        ctx.db.execute(f"DELETE FROM chart_positions WHERE date = ? AND country = ? AND genre_id = ? AND chart = ? "
                       f"AND store_id IN ({marks})", (ctx.day, country, genre, kind, *ids))
        ctx.db.upsert("chart_positions", rows, ["date", "country", "genre_id", "chart", "store_id"])
    return len(rows)


# App Store Connect analytics


def ensure_requests(client: ASC, db: Database, store_id: int) -> tuple[list[str], list[str]]:
    """Make sure the ONGOING and ONE_TIME_SNAPSHOT report requests exist. Returns (request ids, created)."""
    existing = client.get_all(f"/v1/apps/{store_id}/analyticsReportRequests")
    have = {r["attributes"]["accessType"] for r in existing if not r["attributes"].get("stoppedDueToInactivity")}
    created: list[str] = []
    if not config.load().create_report_requests:
        return [str(r["id"]) for r in existing], created
    for access in ("ONGOING", "ONE_TIME_SNAPSHOT"):
        if access in have:
            continue
        r = client.post("/v1/analyticsReportRequests", {"data": {
            "type": "analyticsReportRequests",
            "attributes": {"accessType": access},
            "relationships": {"app": {"data": {"type": "apps", "id": str(store_id)}}},
        }})
        plans.audit(db, None, "asc_report_request", str(store_id), "accessType", None, access)
        created.append(access)
        existing.append(r["data"])
    return [str(r["id"]) for r in existing], created


def segment_rows(url: str) -> list[dict[str, str]]:
    """Rows of one report segment (a gzip TSV file on a signed URL)."""
    r = http.request("GET", url, timeout=120)
    if r.status_code >= 400:
        raise StoreError(f"Report segment download returned {r.status_code}", r.status_code)
    raw = r.content
    with suppress(OSError):
        raw = gzip.decompress(raw)
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), delimiter="\t"))


def normalize(row: dict[str, Any]) -> dict[str, str]:
    return {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}


def row_hash(data: dict[str, str]) -> str:
    """Same hash as the original tracker, so imported rows and fresh downloads deduplicate."""
    return hashlib.sha1(json.dumps(data, sort_keys=True).encode(), usedforsecurity=False).hexdigest()


def aggregate(report_name: str, rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], int]:
    """Daily totals: {(date, metric, source): value}, with source 'all' as the sum of every source."""
    out: dict[tuple[str, str, str], int] = defaultdict(int)
    for raw in rows:
        r = normalize(raw)
        date = r.get("date")
        try:
            n = int(float(r.get("counts") or 0))
        except ValueError:
            continue
        if not date:
            continue
        source = r.get("source type") or "unknown"
        if report_name.startswith("App Store Discovery"):
            metric = {"impression": "impressions", "page view": "page_views", "tap": "taps"}.get(
                r.get("event", "").lower())
        else:
            kind = r.get("download type", "").lower()
            metric = ("first_downloads" if "first" in kind else "redownloads" if "redownload" in kind
                      else "downloads_other" if kind else "downloads")
        if not metric:
            continue
        out[(date, metric, "all")] += n
        out[(date, metric, source)] += n
    return dict(out)


def sync_asc(db: Database, client: ASC, store_id: int) -> str:
    """Fetch report instances not seen yet for an app and rebuild the daily totals they touch."""
    try:
        client.get(f"/v1/apps/{store_id}", **{"fields[apps]": "name"})
    except StoreError as e:
        if e.status in (403, 404):
            return "skipped: the app is not in your App Store Connect account"
        raise
    request_ids, created = ensure_requests(client, db, store_id)
    done = {r["id"] for r in db.all("SELECT id FROM asc_instances WHERE store_id = ?", (store_id,))}
    new_instances, new_rows = 0, 0
    for rid in request_ids:
        for report in client.get_all(f"/v1/analyticsReportRequests/{rid}/reports"):
            name = report["attributes"]["name"]
            if name not in ASC_REPORTS:
                continue
            instances = client.get_all(f"/v1/analyticsReports/{report['id']}/instances",
                                       **{"filter[granularity]": "DAILY", "limit": 200})
            for inst in instances:
                if inst["id"] in done:
                    continue
                rows: list[dict[str, str]] = []
                for seg in client.get_all(f"/v1/analyticsReportInstances/{inst['id']}/segments"):
                    rows.extend(segment_rows(seg["attributes"]["url"]))
                dates = save_report_rows(db, store_id, name, rows, inst["attributes"].get("processingDate"))
                # Rebuild the days this file touches before marking it done: a later failure in this run must not
                # leave a file marked done whose days never reached asc_daily.
                if dates:
                    recompute_daily(db, store_id, dates)
                db.upsert("asc_instances", [{"id": inst["id"], "store_id": store_id, "report_name": name,
                                             "processing_date": inst["attributes"].get("processingDate"),
                                             "rows": len(rows)}], ["id"])
                done.add(inst["id"])
                new_instances += 1
                new_rows += len(rows)
    msg = f"{new_instances} new report files, {new_rows} rows"
    if created:
        msg += f"; requested {', '.join(created)} reports (Apple needs 1-2 days before data appears)"
    return msg


def save_report_rows(db: Database, store_id: int, report_name: str, rows: list[dict[str, Any]],
                     fallback_date: str | None) -> set[str]:
    """Store raw rows deduplicated by hash. Returns the dates they cover."""
    payload = []
    for row in rows:
        data = normalize(row)
        payload.append({"store_id": store_id, "report_name": report_name,
                        "report_date": data.get("date") or fallback_date or "", "row_hash": row_hash(data),
                        "data": data})
    db.upsert("asc_report_rows", payload, ["store_id", "report_name", "row_hash"])
    return {str(p["report_date"]) for p in payload if p["report_date"]}


def recompute_daily(db: Database, store_id: int, dates: set[str]) -> int:
    """Rebuild asc_daily for the dates from every stored raw row (no double counting across files)."""
    out = []
    for date in sorted(dates):
        totals: dict[tuple[str, str, str], int] = defaultdict(int)
        for name in ASC_REPORTS:
            raw = db.all("SELECT data FROM asc_report_rows WHERE store_id = ? AND report_name = ? AND report_date = ?",
                         (store_id, name, date))
            for k, v in aggregate(name, [json.loads(r["data"]) for r in raw]).items():
                totals[k] += v
        out += [{"store_id": store_id, "date": d, "metric": m, "source": s, "value": v}
                for (d, m, s), v in totals.items() if d == date]
    with db.tx():
        for date in dates:
            db.execute("DELETE FROM asc_daily WHERE store_id = ? AND date = ?", (store_id, date))
        db.upsert("asc_daily", out, ["store_id", "date", "metric", "source"])
    return len(out)


# Catch-up, missed days, lock


def collected_on(db: Database, day: str) -> bool:
    """Whether a collection with data (ok or partial) ran for this day."""
    return bool(db.value(
        "SELECT 1 FROM runs WHERE kind = 'collect' AND status IN ('ok', 'partial') "
        "AND coalesce(json_extract(summary, '$.date'), substr(started_at, 1, 10)) = ? LIMIT 1", (day,)))


def missed_days(db: Database, until: str | None = None, window: int = 30) -> list[str]:
    """Days before `until` (default today) without a collection, since the first one and within the window.

    The App Store only shows today's data, so a missed day cannot be filled in later; this says where the
    history has holes.
    """
    end = until or today()
    done = {r["d"] for r in db.all(
        "SELECT DISTINCT coalesce(json_extract(summary, '$.date'), substr(started_at, 1, 10)) AS d "
        "FROM runs WHERE kind = 'collect' AND status IN ('ok', 'partial')")}
    if not done:
        return []
    start = max(min(done), days_ago(window, end))
    day, out = dt.date.fromisoformat(start), []
    stop = dt.date.fromisoformat(end)
    while day < stop:
        if day.isoformat() not in done:
            out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


def lock_path() -> Path:
    return paths.home() / LOCK_NAME


# Lock files this process holds right now (a second flock on the same file from this process would fail too,
# but the set lets is_locked answer without touching the file).
_held: set[Path] = set()


@contextmanager
def lock(path: Path | None = None) -> Iterator[None]:
    """One collection at a time: an exclusive flock held on the open lock file for the whole run.

    The kernel drops the lock when the process exits, so a crashed collector never leaves a lock behind. The
    holder's PID is written into the file for status display; the file itself is never deleted (deleting it
    could let a second process lock a fresh file while the first still holds the old one).
    """
    p = path or lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise CollectBusy("A collection is already running. Try again when it finishes.") from None
    key = p.resolve()
    try:
        with suppress(OSError):
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()} {now_utc()}\n".encode())
        _held.add(key)
        yield
    finally:
        _held.discard(key)
        with suppress(OSError):
            os.ftruncate(fd, 0)
        os.close(fd)  # releases the flock


def is_locked(path: Path | None = None) -> bool:
    """Whether a process (this one or another) holds the collection lock. Probes with a non-blocking flock."""
    p = path or lock_path()
    try:
        if p.resolve() in _held:
            return True
        fd = os.open(p, os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)  # closing also releases the probe lock when it was taken
    return False


def lock_holder(path: Path | None = None) -> int | None:
    """PID of the process holding the lock, or None when nobody holds it."""
    p = path or lock_path()
    if not is_locked(p):
        return None
    try:
        first = p.read_text(encoding="utf-8", errors="replace").split()
        return int(first[0]) if first else None
    except (OSError, ValueError):
        return None


def mark_stale_runs(db: Database, path: Path | None = None) -> int:
    """Close `running` collect rows left behind by a crashed collector. Returns how many were marked failed.

    A live collector holds the lock for its whole run and inserts its row only after taking it, so rows read
    *before* finding the lock free (or held by this process) cannot belong to a live collector. Call it before
    deciding whether a collection is running, and before inserting a new run row.
    """
    p = path or lock_path()
    rows = db.all("SELECT id, summary FROM runs WHERE kind = 'collect' AND status = 'running'")
    if not rows or (p.resolve() not in _held and is_locked(p)):
        return 0  # nothing left over, or another process is collecting right now
    note = "the collector stopped without finishing (crashed or killed); marked failed by a later run"
    for r in rows:
        try:
            summary = json.loads(r["summary"]) if r["summary"] else {}
        except ValueError:
            summary = {}
        if not isinstance(summary, dict):
            summary = {}
        summary["note"] = note
        db.execute("UPDATE runs SET status = 'failed', finished_at = ?, summary = ? "
                   "WHERE id = ? AND status = 'running'",
                   (now_utc(), json.dumps(summary, ensure_ascii=False, default=str), r["id"]))
    return len(rows)


def catch_up(db: Database, app_refs: list[str] | None = None, *, lock_file: Path | None = None,
             asc: AscFactory | None = None, log: Log | None = None) -> dict[str, Any]:
    """Collect today unless it was already done or another run holds the lock. Safe to call often."""
    day = today()
    if collected_on(db, day):
        return {"skipped": f"already collected for {day}", "date": day}
    try:
        with lock(lock_file):
            mark_stale_runs(db, lock_file)
            if collected_on(db, day):  # another process finished while we waited for the lock
                return {"skipped": f"already collected for {day}", "date": day}
            return run(db, app_refs, on=day, asc=asc, log=log)
    except CollectBusy:
        return {"skipped": "another collection is running", "date": day}


def estimate_seconds(db: Database, targets: list[apps.App], with_asc: bool = False) -> int:
    """Rough duration: ~3 s per keyword per country (search throttle) plus the per-app listing calls."""
    total = 0.0
    for a in targets:
        ids = len(apps.tracked_store_ids(db, a))
        for c in a["countries"] or ["us"]:
            total += len(apps.keywords(db, a["id"], c)) * appstore.SEARCH_INTERVAL
            total += appstore.SEARCH_INTERVAL + ids * 1.5 + len(CHART_KINDS)
        if with_asc and a.get("store_id"):
            total += 20
    return int(total) + 5

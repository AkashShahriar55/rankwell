"""The user's apps and what is tracked for each: countries, keywords, competitors."""

from __future__ import annotations

from typing import Any

from .db import Database
from .errors import RankwellError
from .util import slugify

App = dict[str, Any]


def list_apps(db: Database, include_archived: bool = False) -> list[App]:
    where = "" if include_archived else "WHERE archived_at IS NULL"
    apps = db.all(f"SELECT * FROM apps {where} ORDER BY id")
    for a in apps:
        _decorate(db, a)
    return apps


def get(db: Database, ref: str | int) -> App:
    """Find an app by id, slug, App Store id, bundle id or (case-insensitive) name."""
    s = str(ref).strip()
    row = None
    if s.isdigit():
        row = db.one("SELECT * FROM apps WHERE id = ? OR store_id = ?", (int(s), int(s)))
    if row is None:
        row = db.one(
            "SELECT * FROM apps WHERE slug = ? OR bundle_id = ? OR lower(name) = lower(?)", (slugify(s), s, s)
        )
    if row is None:
        like = db.all("SELECT * FROM apps WHERE lower(name) LIKE lower(?) AND archived_at IS NULL", (f"%{s}%",))
        if len(like) == 1:
            row = like[0]
    if row is None:
        names = ", ".join(a["name"] for a in list_apps(db)) or "none yet"
        raise RankwellError(f"No app matches '{ref}'. Your apps: {names}.")
    return _decorate(db, row)


def _decorate(db: Database, a: App) -> App:
    a["countries"] = [r["country"] for r in db.all(
        "SELECT country FROM app_countries WHERE app_id = ? ORDER BY rowid", (a["id"],))]
    a["stage"] = "live" if a.get("store_id") else "prelaunch"
    return a


def add(db: Database, name: str, *, store_id: int | None = None, bundle_id: str | None = None,
        repo_path: str | None = None, countries: list[str] | None = None, genre_id: int | None = None,
        slug: str | None = None, play_package: str | None = None) -> App:
    slug = slug or slugify(name)
    if db.one("SELECT id FROM apps WHERE slug = ?", (slug,)):
        raise RankwellError(f"An app with the slug '{slug}' already exists.")
    with db.tx():
        cur = db.execute(
            "INSERT INTO apps (slug, name, store_id, bundle_id, repo_path, genre_id, play_package) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (slug, name, store_id, bundle_id, repo_path, genre_id, play_package),
        )
        app_id = int(cur.lastrowid or 0)
        set_countries(db, app_id, countries or ["us"])
    return get(db, app_id)


UPDATABLE = {"name", "store_id", "bundle_id", "repo_path", "genre_id", "play_package"}


def update(db: Database, app_id: int, **fields: Any) -> App:
    bad = set(fields) - UPDATABLE
    if bad:
        raise RankwellError(f"Cannot change: {', '.join(sorted(bad))}")
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE apps SET {sets} WHERE id = ?", (*fields.values(), app_id))
    return get(db, app_id)


def archive(db: Database, app_id: int) -> None:
    db.execute("UPDATE apps SET archived_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?", (app_id,))


def set_countries(db: Database, app_id: int, countries: list[str]) -> None:
    with db.tx():
        db.execute("DELETE FROM app_countries WHERE app_id = ?", (app_id,))
        for c in dict.fromkeys(c.lower().strip() for c in countries if c.strip()):
            db.execute("INSERT INTO app_countries (app_id, country) VALUES (?, ?)", (app_id, c))


def keywords(db: Database, app_id: int, country: str | None = None) -> list[dict[str, Any]]:
    if country:
        return db.all("SELECT * FROM keywords WHERE app_id = ? AND country = ? ORDER BY term", (app_id, country))
    return db.all("SELECT * FROM keywords WHERE app_id = ? ORDER BY country, term", (app_id,))


def add_keywords(db: Database, app_id: int, terms: list[str], country: str) -> int:
    rows = [{"app_id": app_id, "country": country, "term": t.strip().lower()} for t in terms if t.strip()]
    before = db.value("SELECT count(*) FROM keywords WHERE app_id = ?", (app_id,))
    db.upsert("keywords", rows, ["app_id", "country", "term"])
    return int(db.value("SELECT count(*) FROM keywords WHERE app_id = ?", (app_id,))) - int(before)


def remove_keywords(db: Database, app_id: int, terms: list[str], country: str) -> int:
    n = 0
    for t in terms:
        n += db.execute("DELETE FROM keywords WHERE app_id = ? AND country = ? AND term = ?",
                        (app_id, country, t.strip().lower())).rowcount
    return n


def competitors(db: Database, app_id: int) -> list[dict[str, Any]]:
    return db.all("SELECT * FROM competitors WHERE app_id = ? ORDER BY id", (app_id,))


def add_competitor(db: Database, app_id: int, store_id: int, name: str) -> None:
    db.upsert("competitors", [{"app_id": app_id, "store_id": store_id, "name": name}], ["app_id", "store_id"])


def remove_competitor(db: Database, app_id: int, store_id: int) -> bool:
    return db.execute("DELETE FROM competitors WHERE app_id = ? AND store_id = ?",
                      (app_id, store_id)).rowcount > 0


def tracked_store_ids(db: Database, app: App) -> list[int]:
    """The app's own store id (when live) followed by its competitors."""
    ids = [int(app["store_id"])] if app.get("store_id") else []
    return ids + [int(c["store_id"]) for c in competitors(db, app["id"]) if c["store_id"] not in ids]

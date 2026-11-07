"""Google Play: store listing, bundle upload to a track, tracks, reviews and replies, for the user's own apps.

Every write is a plan (plans.py): plan_* reads Play, the applier re-reads it inside a fresh edit, refuses when
it changed, makes the change, validates and commits the edit (an edit that fails is deleted, so nothing is
half-applied). Review replies need no edit. Nothing here deletes Play data.
"""

from __future__ import annotations

import datetime as dt
import glob
import hashlib
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from . import listing, plans, release, render
from .apps import App
from .db import Database
from .errors import RankwellError
from .stores.play import Play, Resource
from .util import now_utc

LIMITS = {"title": 30, "short_description": 80, "full_description": 4000}
FIELDS = {"title": "title", "short_description": "shortDescription", "full_description": "fullDescription",
          "video": "video"}
TRACKS = ("internal", "alpha", "beta", "production")
STATUSES = ("completed", "draft", "inProgress")
NOTES_LIMIT = 500  # release notes per language
REPLY_LIMIT = 350  # Play rejects longer replies
AAB_GLOB = "build/app/outputs/bundle/release/*.aab"  # where `flutter build appbundle` writes
# Google's metadata policy: no ranking, price or promotion words in the title (they also hurt the short one).
PROMO = {"best", "top", "#1", "free", "new", "sale", "discount", "cheap", "bonus", "million"}
STOPWORDS = listing.STOPWORDS


def connect() -> Play:
    """The Google Play client (NotConfigured when no key is set). Tests replace this."""
    return Play()


def package_of(app: App) -> str:
    pkg = app.get("play_package")
    if not pkg:
        raise RankwellError(f"{app['name']} has no Google Play package name; set it with "
                            "update_app(app, play_package='com.example.app').")
    return str(pkg)


# ------------------------------------------------------------------ listing

def _words(text: str) -> list[str]:
    return re.findall(r"[\w'#]+", text.lower())


def _validate(fields: Mapping[str, Any]) -> None:
    bad = sorted(set(fields) - set(FIELDS))
    if bad:
        raise RankwellError(f"Unknown Play listing field(s): {', '.join(bad)}. Use: {', '.join(FIELDS)}.")


def check(fields: Mapping[str, str | None], tracked_keywords: Iterable[str] = ()) -> dict[str, Any]:
    """Problems (must fix) and tips for Play listing fields: limits, spaces, promo words, keyword coverage."""
    problems: list[dict[str, str]] = []
    tips: list[dict[str, str]] = []
    lengths = []
    vals = {k: v for k, v in fields.items() if v is not None}
    for k, v in vals.items():
        if k in LIMITS:
            lengths.append({"field": k, "chars": len(v), "limit": LIMITS[k], "left": LIMITS[k] - len(v)})
            if len(v) > LIMITS[k]:
                problems.append({"field": k, "message": f"{len(v)} characters; the limit is {LIMITS[k]}."})
        if v != v.strip():
            problems.append({"field": k, "message": "Leading or trailing spaces."})
    for k in ("title", "short_description"):
        text = vals.get(k) or ""
        promo = sorted(set(_words(text)) & PROMO)
        if promo:
            tips.append({"field": k, "message": f"'{', '.join(promo)}': Google's metadata policy bans ranking, "
                                                "price and promotion words here (it can reject the update)."})
        if re.search(r"[\U0001F300-\U0001FAFF☀-➿]", text):
            tips.append({"field": k, "message": "Emoji: not allowed in the title and discouraged in the short "
                                                "description by Google's metadata policy."})
    title = vals.get("title") or ""
    if len(title) > 5 and title.isupper():
        tips.append({"field": "title", "message": "All caps: allowed only when it is the brand name."})
    indexed = {w for f in ("title", "short_description", "full_description") for w in _words(vals.get(f) or "")}
    missing = [t for t in dict.fromkeys(k.strip().lower() for k in tracked_keywords if k.strip())
               if not all(w in indexed or w in STOPWORDS for w in _words(t))]
    if missing and indexed:
        more = f" (+{len(missing) - 15} more)." if len(missing) > 15 else "."
        tips.append({"field": "full_description", "message": "Tracked keywords not in title, short or full "
                     f"description (Play indexes all three): {', '.join(missing[:15])}{more}"})
    return {"ok": not problems, "problems": problems, "tips": tips, "lengths": lengths, "uncovered": missing}


def _fields(res: Resource | None) -> dict[str, str]:
    return {k: str((res or {}).get(a) or "") for k, a in FIELDS.items()}


def live(play: Play, package: str, language: str) -> dict[str, Any]:
    """The Play store listing for one language (empty fields when the language has none yet)."""
    with play.read_edit(package) as eid:
        all_ = play.listings(package, eid)
    found = next((r for r in all_ if r.get("language") == language), None)
    return {"package": package, "language": language, "exists": found is not None, "fields": _fields(found),
            "languages": sorted(str(r.get("language")) for r in all_)}


def live_display(cur: dict[str, Any]) -> str:
    head = (f"**{cur['package']} · {cur['language']}**" + ("" if cur["exists"] else " (no listing in this language)")
            + f" · languages: {', '.join(cur['languages']) or 'none'}")
    rows = [{"field": k, "value": listing._clip(v), "chars": len(v), "limit": LIMITS.get(k, "")}
            for k, v in cur["fields"].items()]
    return head + "\n\n" + render.table(rows, ["field", "value", "chars", "limit"], align_right=["chars", "limit"])


def plan_listing(db: Database, play: Play, app: App, language: str, fields: Mapping[str, str | None]) -> plans.Plan:
    """A plan that makes the Play listing for one language match these fields."""
    _validate(fields)
    new = {k: v for k, v in fields.items() if v is not None}
    over = [f"{k} is {len(v)} characters (limit {LIMITS[k]})" for k, v in new.items()
            if k in LIMITS and len(v) > LIMITS[k]]
    if over:
        raise RankwellError("Too long: " + "; ".join(over) + ".")
    pkg = package_of(app)
    cur = live(play, pkg, language)
    changes = [{"area": "Store listing", "locale": language, "field": k, "current": cur["fields"][k], "new": v,
                "target": {"kind": "play_listing"}} for k, v in new.items() if cur["fields"][k] != v]
    if not changes:
        raise RankwellError("Nothing to change: Google Play already has these values.")
    notes = ["Committed as one Play edit. Google reviews listing changes before they show (often hours, "
             "sometimes days); with managed publishing on, they wait for you to publish in Play Console."]
    if not cur["exists"]:
        notes.append(f"{language} is a new language on Play; it will be added (it needs a title).")
        if not (new.get("title") or cur["fields"]["title"]):
            raise RankwellError(f"{language} has no listing on Play yet; a new language needs a title.")
    return plans.create(db, "play_listing", app["id"], changes, plans.state_hash(cur["fields"]),
                        payload={"package": pkg, "language": language}, notes=notes)


@plans.applier("play_listing")
def _apply_listing(db: Database, plan: plans.Plan) -> dict[str, Any]:
    play, pkg, lang = connect(), plan["package"], plan["language"]
    eid = play.insert_edit(pkg)
    try:
        res = play.listing(pkg, eid, lang)
        plans.verify(plan, plans.state_hash(_fields(res)))
        body = {**{a: (res or {}).get(a) for a in FIELDS.values() if (res or {}).get(a)}, "language": lang}
        body.update({FIELDS[c["field"]]: c["new"] for c in plan["changes"]})
        play.put_listing(pkg, eid, lang, body)
        play.validate(pkg, eid)
        play.commit(pkg, eid)
    except BaseException:
        play.delete_edit(pkg, eid)
        raise
    for c in plan["changes"]:
        plans.audit(db, plan["id"], "play_listing", f"{pkg}/listings/{lang}", c["field"], c["current"], c["new"])
    return {"changed": [c["field"] for c in plan["changes"]], "language": lang, "edit": eid,
            "next": "Google reviews the change before it shows on Play."}


# ------------------------------------------------------------------ tracks

def _release_row(track: str, r: Resource) -> dict[str, Any]:
    frac = r.get("userFraction")
    return {"track": track, "release": r.get("name") or "", "status": r.get("status") or "",
            "version_codes": ", ".join(str(v) for v in r.get("versionCodes", [])),
            "rollout": f"{float(frac) * 100:.0f}%" if frac else ("100%" if r.get("status") == "completed" else ""),
            "notes": ", ".join(str(n.get("language")) for n in r.get("releaseNotes", []))}


def tracks(play: Play, package: str) -> list[dict[str, Any]]:
    """Every release on every track: name, status, version codes, rollout, release-note languages."""
    with play.read_edit(package) as eid:
        ts = play.tracks(package, eid)
    rows = []
    for t in ts:
        rels = t.get("releases") or []
        rows += [_release_row(str(t.get("track")), r) for r in rels] or [
            {"track": t.get("track"), "release": "", "status": "(empty)", "version_codes": "", "rollout": "",
             "notes": ""}]
    order = {n: i for i, n in enumerate(TRACKS)}
    return sorted(rows, key=lambda r: order.get(str(r["track"]), 99))


def tracks_display(rows: list[dict[str, Any]]) -> str:
    return render.table(rows, ["track", "release", "status", "version_codes", "rollout", "notes"])


# ------------------------------------------------------------------ upload

def find_aab(app: App, explicit: str | None = None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_absolute():
            p = release.repo_of(app) / p
        if not p.is_file():
            raise RankwellError(f"No .aab at {p}.")
        return p
    repo = release.repo_of(app)
    found = [Path(p) for p in glob.glob(os.path.join(glob.escape(str(repo)), AAB_GLOB)) if Path(p).is_file()]
    if not found:
        raise RankwellError(f"No .aab in {repo / AAB_GLOB}. Build it first: flutter build appbundle --release.")
    return max(found, key=lambda p: p.stat().st_mtime)


def pubspec_version(repo: Path) -> tuple[str, int] | None:
    """(version name, version code) from pubspec.yaml's `version: 1.2.3+45`, when there is one."""
    f = repo / "pubspec.yaml"
    if not f.is_file():
        return None
    m = re.search(r"^version:\s*['\"]?([0-9][^+\s'\"]*)\+(\d+)", f.read_text(), re.M)
    return (m.group(1), int(m.group(2))) if m else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _upload_state(play: Play, package: str, edit_id: str, path: Path, track: str) -> dict[str, Any]:
    st = path.stat() if path.exists() else None
    bundles = play.bundles(package, edit_id)
    cur = next((t for t in play.tracks(package, edit_id) if t.get("track") == track), None)
    return {"aab": str(path), "size": st.st_size if st else None, "mtime": int(st.st_mtime) if st else None,
            "sha256": _sha256(path) if st else None,
            "bundles": sorted((int(b.get("versionCode") or 0), str(b.get("sha256") or "")) for b in bundles),
            "track": (cur or {}).get("releases") or []}


def _release_status(status: str | None, rollout: float | None) -> tuple[str, float | None]:
    if rollout is not None:
        if not 0 < rollout <= 1:
            raise RankwellError("rollout is a fraction of users between 0 and 1 (e.g. 0.1 for 10%).")
        if rollout < 1:
            if status not in (None, "inProgress"):
                raise RankwellError(f"A staged rollout ({rollout:.0%}) needs status inProgress, not {status}.")
            return "inProgress", rollout
    status = status or "completed"
    if status not in STATUSES:
        raise RankwellError(f"status must be one of {', '.join(STATUSES)}, not '{status}'.")
    if status == "inProgress":
        raise RankwellError("status inProgress needs a rollout below 1 (e.g. rollout=0.2).")
    return status, None


def plan_upload(db: Database, play: Play, app: App, aab: str | None = None, track: str = "internal",
                release_notes: Mapping[str, str] | None = None, rollout: float | None = None,
                status: str | None = None, name: str | None = None) -> plans.Plan:
    """A plan to upload the newest .aab (or this one) and make it the release on a track, in one edit."""
    if track not in TRACKS:
        raise RankwellError(f"track must be one of {', '.join(TRACKS)}, not '{track}'.")
    status, fraction = _release_status(status, rollout)
    notes_in = {k.strip(): v.strip() for k, v in (release_notes or {}).items() if v and v.strip()}
    long_notes = [f"{k} is {len(v)} characters" for k, v in notes_in.items() if len(v) > NOTES_LIMIT]
    if long_notes:
        raise RankwellError(f"Release notes over {NOTES_LIMIT} characters: {'; '.join(long_notes)}.")
    pkg = package_of(app)
    path = find_aab(app, aab)
    expected = pubspec_version(release.repo_of(app)) if app.get("repo_path") else None
    with play.read_edit(pkg) as eid:
        state = _upload_state(play, pkg, eid, path, track)
    codes = [c for c, _ in state["bundles"]]
    same = next((c for c, sha in state["bundles"] if sha and sha == state["sha256"]), None)
    if same:
        raise RankwellError(f"{path.name} is already on Play as version code {same}. Build a new one.")
    if expected and expected[1] in codes:
        raise RankwellError(f"Version code {expected[1]} (pubspec.yaml {expected[0]}+{expected[1]}) is already on "
                            "Play. Raise the +N in pubspec.yaml and build again.")
    code_text = f"version code {expected[1]} ({expected[0]}, from pubspec.yaml)" if expected else \
        "version code read on upload"
    now = "; ".join(f"{r.get('status')} {', '.join(map(str, r.get('versionCodes', [])))}".strip()
                    for r in state["track"]) or "(no release)"
    rollout_text = f" · {fraction:.0%} of users" if fraction else ""
    changes = [
        {"area": "Bundles", "locale": "all", "field": "upload",
         "current": ", ".join(str(c) for c in codes[-3:]) or "(no bundle yet)",
         "new": f"{path.name} · {state['size'] / 1e6:.1f} MB · {code_text}", "target": {"kind": "bundle"}},
        {"area": f"Track {track}", "locale": "all", "field": "release", "current": now,
         "new": f"{status}{rollout_text}" + (f" · {name}" if name else ""), "target": {"kind": "track"}},
    ] + [{"area": f"Track {track}", "locale": lang, "field": "release notes", "current": "", "new": text,
          "target": {"kind": "track"}} for lang, text in notes_in.items()]
    notes = [f"Uploads {path} and replaces the releases on the {track} track with this one, then commits the edit."]
    if track == "production":
        notes.append("Production releases go through Google's review before users get them.")
    if status == "draft":
        notes.append("Draft: nothing reaches users until it is rolled out in Play Console (or a new plan).")
    else:
        notes.append("An app that was never published on Play accepts only status draft; Play says so on apply.")
    notes.append("A large .aab can outlast a tool call: then run `rankwell play upload <app> --track ...` "
                 "in the terminal.")
    return plans.create(db, "play_upload", app["id"], changes, plans.state_hash(state),
                        payload={"package": pkg, "aab": str(path), "track": track, "release_status": status,
                                 "fraction": fraction, "release_notes": notes_in, "name": name}, notes=notes)


@plans.applier("play_upload")
def _apply_upload(db: Database, plan: plans.Plan) -> dict[str, Any]:
    play, pkg, path, track = connect(), plan["package"], Path(plan["aab"]), plan["track"]
    eid = play.insert_edit(pkg)
    try:
        plans.verify(plan, plans.state_hash(_upload_state(play, pkg, eid, path, track)))
        bundle = play.upload_bundle(pkg, eid, path)
        code = int(bundle["versionCode"])
        rel: dict[str, Any] = {"versionCodes": [str(code)], "status": plan["release_status"]}
        if plan["fraction"]:
            rel["userFraction"] = plan["fraction"]
        if plan["name"]:
            rel["name"] = plan["name"]
        if plan["release_notes"]:
            rel["releaseNotes"] = [{"language": k, "text": v} for k, v in plan["release_notes"].items()]
        play.put_track(pkg, eid, track, [rel])
        play.validate(pkg, eid)
        play.commit(pkg, eid)
    except BaseException:
        play.delete_edit(pkg, eid)
        raise
    plans.audit(db, plan["id"], "play_upload", f"{pkg}/bundles", "versionCode", None, code)
    plans.audit(db, plan["id"], "play_upload", f"{pkg}/tracks/{track}", "release", None, rel)
    return {"version_code": code, "track": track, "status": plan["release_status"], "rollout": plan["fraction"],
            "sha256": bundle.get("sha256"), "next": "play_tracks shows the release; Google may review it first."}


# ------------------------------------------------------------------ reviews

def _ts(stamp: Any) -> str | None:
    secs = (stamp or {}).get("seconds") if isinstance(stamp, dict) else None
    if secs is None:
        return None
    return dt.datetime.fromtimestamp(int(secs), dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _review_row(app_id: int, r: Resource) -> dict[str, Any]:
    user: dict[str, Any] = {}
    dev: dict[str, Any] = {}
    for c in r.get("comments", []):
        user = user or c.get("userComment") or {}
        dev = dev or c.get("developerComment") or {}
    return {"id": str(r["reviewId"]), "app_id": app_id, "rating": user.get("starRating"),
            "content": (user.get("text") or "").strip(), "author": r.get("authorName"),
            "language": user.get("reviewerLanguage"), "version": user.get("appVersionName"),
            "version_code": user.get("appVersionCode"), "device": user.get("device"),
            "reviewed_at": _ts(user.get("lastModified")), "reply": dev.get("text"),
            "replied_at": _ts(dev.get("lastModified")), "fetched_at": now_utc()}


def fetch_reviews(db: Database, play: Play, app: App) -> int:
    """Store the reviews Play returns now (last 7 days); older ones stay in the database."""
    rows = [_review_row(app["id"], r) for r in play.reviews(package_of(app)) if r.get("reviewId")]
    return db.upsert("play_reviews", rows, ["id"])


def reviews(db: Database, app: App, days: int = 30, limit: int = 50, unanswered: bool = False) -> list[dict[str, Any]]:
    since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    extra = " AND reply IS NULL" if unanswered else ""
    return db.all(f"SELECT * FROM play_reviews WHERE app_id = ? AND reviewed_at >= ?{extra} "
                  "ORDER BY reviewed_at DESC LIMIT ?", (app["id"], since, limit))


def reviews_display(rows: list[dict[str, Any]]) -> str:
    out = [{"id": r["id"][-8:], "stars": "★" * int(r["rating"] or 0), "date": (r["reviewed_at"] or "")[:10],
            "version": r["version"], "review": listing._clip(r["content"], 90),
            "replied": "yes" if r["reply"] else ""} for r in rows]
    return render.table(out, ["date", "stars", "version", "review", "replied", "id"])


def plan_review_reply(db: Database, play: Play, app: App, review_id: str, text: str) -> plans.Plan:
    """A plan to reply to (or replace the reply to) one Play review."""
    text = text.strip()
    if not text:
        raise RankwellError("The reply is empty.")
    if len(text) > REPLY_LIMIT:
        raise RankwellError(f"The reply is {len(text)} characters; Google Play allows {REPLY_LIMIT}.")
    pkg = package_of(app)
    review_id = review_id.strip()
    # play_reviews shows the end of the id; accept that when it matches exactly one stored review
    ids = [r["id"] for r in db.all("SELECT id FROM play_reviews WHERE app_id = ?", (app["id"],))]
    if review_id not in ids:
        ends = [i for i in ids if i.endswith(review_id)]
        review_id = ends[0] if len(ends) == 1 else review_id
    row = _review_row(app["id"], play.review(pkg, review_id))
    db.upsert("play_reviews", [row], ["id"])
    changes = [{"area": f"Review {'★' * int(row['rating'] or 0)} by {row['author'] or 'someone'}",
                "locale": row["language"] or "", "field": "reply", "current": row["reply"] or "", "new": text,
                "target": {"kind": "play_review"}}]
    notes = [f"Review: \"{listing._clip(row['content'], 200)}\"",
             "The reply shows publicly under the review on Google Play at once; replying again replaces it."]
    return plans.create(db, "play_review_reply", app["id"], changes, plans.state_hash(row["reply"] or ""),
                        payload={"package": pkg, "review_id": row["id"], "text": text}, notes=notes)


@plans.applier("play_review_reply")
def _apply_reply(db: Database, plan: plans.Plan) -> dict[str, Any]:
    play, pkg, rid = connect(), plan["package"], plan["review_id"]
    cur = _review_row(plan["app_id"], play.review(pkg, rid))
    plans.verify(plan, plans.state_hash(cur["reply"] or ""))
    res = play.reply(pkg, rid, plan["text"]).get("result") or {}
    applied = str(res.get("replyText") or plan["text"])
    plans.audit(db, plan["id"], "play_review_reply", f"{pkg}/reviews/{rid}", "reply", cur["reply"], applied)
    db.execute("UPDATE play_reviews SET reply = ?, replied_at = ? WHERE id = ?",
               (applied, _ts(res.get("lastEdited")) or now_utc(), rid))
    return {"replied": rid, "text": applied}

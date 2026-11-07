"""App Store metadata: limits and checks, the live listing in App Store Connect, drafts, and write plans.

Where each field lives in App Store Connect:
- appInfoLocalizations (name, subtitle, privacy URLs): editable only while a version is in preparation.
- appStoreVersionLocalizations (description, keywords, promotional text, What's New, support and marketing
  URLs): the version in preparation; promotional text can also change on the live version at any time.
Nothing here deletes App Store Connect data.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from . import plans, render
from .apps import App
from .db import Database
from .errors import PlanError, RankwellError, StoreError
from .stores import asc as ascmod
from .stores.asc import ASC, Resource
from .util import now_utc

LIMITS = {"name": 30, "subtitle": 30, "keywords": 100, "promotional_text": 170, "description": 4000,
          "whats_new": 4000}
INFO_FIELDS = {"name": "name", "subtitle": "subtitle", "privacy_url": "privacyPolicyUrl",
               "privacy_choices_url": "privacyChoicesUrl"}
VERSION_FIELDS = {"description": "description", "keywords": "keywords", "promotional_text": "promotionalText",
                  "whats_new": "whatsNew", "support_url": "supportUrl", "marketing_url": "marketingUrl"}
FIELDS = {**INFO_FIELDS, **VERSION_FIELDS}
INDEXED = ("name", "subtitle", "keywords")
APPLY_NOTE = "Show this to the user and ask before calling apply_plan."

# App Store genre id → words of its category name (Apple already matches an app to its category).
CATEGORY_WORDS: dict[int, set[str]] = {
    6000: {"business"}, 6001: {"weather"}, 6002: {"utilities", "utility"}, 6003: {"travel"},
    6004: {"sports", "sport"}, 6005: {"social", "networking"}, 6006: {"reference"}, 6007: {"productivity"},
    6008: {"photo", "video"}, 6009: {"news"}, 6010: {"navigation"}, 6011: {"music"}, 6012: {"lifestyle"},
    6013: {"health", "fitness"}, 6014: {"games", "game"}, 6015: {"finance"}, 6016: {"entertainment"},
    6017: {"education"}, 6018: {"books", "book"}, 6020: {"medical"}, 6021: {"magazines", "newspapers"},
    6023: {"food", "drink"}, 6024: {"shopping"}, 6026: {"developer", "tools"}, 6027: {"graphics", "design"},
}
FILLER = {"app", "apps"}
STOPWORDS = {"a", "an", "the", "and", "or", "for", "of", "to", "in", "on", "with", "by", "your", "my", "&"}


def connect() -> ASC:
    """The App Store Connect client (NotConfigured when no key is set). Tests replace this."""
    return ASC()


def asc_app_id(asc: ASC, app: App) -> int:
    """The App Store Connect app id: the App Store id, or found by bundle id before release."""
    if app.get("store_id"):
        return int(app["store_id"])
    if app.get("bundle_id"):
        found = asc.get("/v1/apps", **{"filter[bundleId]": app["bundle_id"], "fields[apps]": "bundleId"})
        if found.get("data"):
            return int(found["data"][0]["id"])
        raise RankwellError(f"No app with bundle id {app['bundle_id']} in your App Store Connect account.")
    raise RankwellError(f"{app['name']} has no App Store id or bundle id; set one with update_app.")


# ------------------------------------------------------------------ checks

def _words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def _keyword_terms(field: str) -> list[str]:
    return [t.strip().lower() for t in field.split(",")]


def _plural_pairs(words: Iterable[str]) -> list[tuple[str, str]]:
    ws = set(words)
    pairs = []
    for w in sorted(ws):
        for p in (w + "s", w + "es", w[:-1] + "ies" if w.endswith("y") else ""):
            if p and p != w and p in ws:
                pairs.append((w, p))
    return pairs


def check(fields: Mapping[str, str | None], tracked_keywords: Iterable[str] = (),
          genre_id: int | None = None, context: Mapping[str, str | None] | None = None) -> dict[str, Any]:
    """Problems (must fix) and tips (wasted characters, missed keywords) for a set of listing fields.

    `context` is the rest of the listing (usually the live one): its name, subtitle and keyword field count for
    cross-field repeats and keyword coverage, but limits and problems are only reported for `fields`."""
    problems: list[dict[str, str]] = []
    tips: list[dict[str, str]] = []
    vals = {k: v for k, v in fields.items() if v is not None}
    indexed_vals = {**{k: v for k, v in (context or {}).items() if v and k in INDEXED}, **vals}
    lengths = []
    for k, v in vals.items():
        if k in LIMITS:
            lengths.append({"field": k, "chars": len(v), "limit": LIMITS[k], "left": LIMITS[k] - len(v)})
            if len(v) > LIMITS[k]:
                problems.append({"field": k, "message": f"{len(v)} characters; the limit is {LIMITS[k]}."})
        if v != v.strip():
            problems.append({"field": k, "message": "Leading or trailing spaces."})

    kw = vals.get("keywords")
    kw_words: list[str] = []
    if kw is None and indexed_vals.get("keywords"):  # unchanged keyword field: still counts for coverage
        kw_words = [w for t in _keyword_terms(indexed_vals["keywords"]) for w in _words(t)]
    if kw is not None:
        spaces = len(re.findall(r"\s+,|,\s+", kw))
        if spaces:
            problems.append({"field": "keywords", "message": f"Spaces around commas waste characters ({spaces}×); "
                                                             "use 'a,b,c'."})
        terms = _keyword_terms(kw)
        if any(not t for t in terms[:-1]) or (len(terms) > 1 and not terms[-1]):
            problems.append({"field": "keywords", "message": "Empty entry (double or trailing comma)."})
        kw_words = [w for t in terms for w in _words(t)]
        dupes = sorted({w for w in kw_words if kw_words.count(w) > 1})
        if dupes:
            problems.append({"field": "keywords", "message": f"Repeated in the keyword field: {', '.join(dupes)}."})
        filler = sorted(set(kw_words) & FILLER)
        if filler:
            tips.append({"field": "keywords", "message": f"'{', '.join(filler)}' is wasted: every result is an app."})
        cat = sorted(set(kw_words) & CATEGORY_WORDS.get(genre_id or 0, set()))
        if cat:
            tips.append({"field": "keywords", "message": f"Category word(s) {', '.join(cat)}: Apple already matches "
                                                         "your category; use the characters for other words."})

    where: dict[str, list[str]] = {}
    for f in INDEXED:
        if indexed_vals.get(f):
            ws = kw_words if f == "keywords" else _words(indexed_vals[f])
            for w in dict.fromkeys(ws):
                if w not in STOPWORDS:
                    where.setdefault(w, []).append(f)
    for w, fs in where.items():
        if len(fs) > 1:
            msg = f"'{w}' is in {' and '.join(fs)}; Apple indexes them together, so once is enough"
            msg += f" (remove it from keywords to save {len(w) + 1} characters)." if "keywords" in fs else "."
            tips.append({"field": fs[-1], "message": msg})
    for single, plural in _plural_pairs(where):
        tips.append({"field": "keywords" if kw_words else "name",
                     "message": f"Both '{single}' and '{plural}': one form is usually enough."})

    indexed = set(where)
    # Without any searchable field there is nothing to judge coverage against (e.g. a promo-text-only change).
    terms = dict.fromkeys(k.strip().lower() for k in tracked_keywords if k.strip())
    missing = [t for t in terms if not all(w in indexed or w in STOPWORDS for w in _words(t))
               ] if indexed_vals.keys() & set(INDEXED) else []
    if missing:
        more = f" (+{len(missing) - 15} more)." if len(missing) > 15 else "."
        tips.append({"field": "keywords", "message": "Tracked keywords not covered by name, subtitle or keyword "
                                                     f"field: {', '.join(missing[:15])}{more}"})
    return {"ok": not problems, "problems": problems, "tips": tips, "lengths": lengths, "uncovered": missing}


def check_display(result: dict[str, Any]) -> str:
    out = [render.table(result["lengths"], ["field", "chars", "limit", "left"], align_right=["chars", "limit", "left"])]
    if result["problems"]:
        out.append("**Problems**\n" + "\n".join(f"- {p['field']}: {p['message']}" for p in result["problems"]))
    if result["tips"]:
        out.append("**Tips**\n" + "\n".join(f"- {p['field']}: {p['message']}" for p in result["tips"]))
    if not result["problems"] and not result["tips"]:
        out.append("No problems found.")
    return "\n\n".join(out)


# ------------------------------------------------------------------ live state

def _ver(v: Resource | None) -> dict[str, Any] | None:
    if not v:
        return None
    a = v["attributes"]
    return {"id": v["id"], "version": a.get("versionString"), "state": ascmod.version_state(a),
            "release_type": a.get("releaseType"), "copyright": a.get("copyright"), "created": a.get("createdDate")}


def versions(asc: ASC, store_id: int) -> list[dict[str, Any]]:
    """iOS App Store versions, newest first as App Store Connect lists them."""
    rows = asc.get_all(f"/v1/apps/{store_id}/appStoreVersions", **{"filter[platform]": "IOS", "limit": 50})
    return [v for v in (_ver(r) for r in rows) if v]


def pick(vs: list[dict[str, Any]], states: set[str]) -> dict[str, Any] | None:
    return next((v for v in vs if v["state"] in states), None)


def _by_locale(rows: list[Resource]) -> dict[str, Resource]:
    return {r["attributes"]["locale"]: r for r in rows}


def _values(info_loc: Resource | None, ver_loc: Resource | None) -> dict[str, Any]:
    ia = info_loc["attributes"] if info_loc else {}
    va = ver_loc["attributes"] if ver_loc else {}
    return {**{k: ia.get(a) for k, a in INFO_FIELDS.items()}, **{k: va.get(a) for k, a in VERSION_FIELDS.items()}}


def live(asc: ASC, store_id: int, locale: str) -> dict[str, Any]:
    """What App Store Connect holds for one locale: the version in preparation (when any) and the live one."""
    vs = versions(asc, store_id)
    infos = asc.get_all(f"/v1/apps/{store_id}/appInfos")
    ev, lv = pick(vs, ascmod.EDITABLE), pick(vs, ascmod.LIVE)
    ei = next((i for i in infos if ascmod.version_state(i["attributes"]) in ascmod.EDITABLE), None)
    li = next((i for i in infos if ascmod.version_state(i["attributes"]) in ascmod.LIVE), None)

    def info_loc(info: Resource | None) -> Resource | None:
        return _by_locale(asc.get_all(f"/v1/appInfos/{info['id']}/appInfoLocalizations")).get(locale) if info else None

    def ver_locs(v: dict[str, Any] | None) -> dict[str, Resource]:
        return _by_locale(asc.get_all(f"/v1/appStoreVersions/{v['id']}/appStoreVersionLocalizations")) if v else {}

    eil, lil = info_loc(ei), info_loc(li)
    evl_all, lvl_all = ver_locs(ev), ver_locs(lv)
    evl, lvl = evl_all.get(locale), lvl_all.get(locale)
    live_fields = _values(lil, lvl)
    return {
        "store_id": store_id, "locale": locale,
        "edit_version": ev, "live_version": lv,
        "in_review": pick(vs, ascmod.IN_REVIEW),
        "edit_info_id": ei["id"] if ei else None, "live_info_id": li["id"] if li else None,
        "ids": {"edit_info_loc": eil["id"] if eil else None, "live_info_loc": lil["id"] if lil else None,
                "edit_version_loc": evl["id"] if evl else None, "live_version_loc": lvl["id"] if lvl else None},
        "fields": _values(eil if ei else lil, evl if ev else lvl),
        "live_fields": live_fields,
        "version_locales": sorted(evl_all or lvl_all),
    }


def live_display(cur: dict[str, Any]) -> str:
    ev, lv = cur["edit_version"], cur["live_version"]
    head = (f"**{cur['locale']}** · in preparation: {ev['version'] + ' (' + ev['state'] + ')' if ev else 'none'}"
            f" · live: {lv['version'] if lv else 'none'}")
    rows = [{"field": k, "value": _clip(v), "chars": len(v) if v else 0,
             "limit": LIMITS.get(k, "")} for k, v in cur["fields"].items()]
    return head + "\n\n" + render.table(rows, ["field", "value", "chars", "limit"], align_right=["chars", "limit"])


# ------------------------------------------------------------------ drafts

def _validate(fields: Mapping[str, Any]) -> None:
    bad = sorted(set(fields) - set(FIELDS))
    if bad:
        raise RankwellError(f"Unknown listing field(s): {', '.join(bad)}. Use: {', '.join(FIELDS)}.")


def draft(db: Database, app_id: int, locale: str) -> dict[str, str]:
    row = db.one("SELECT fields FROM listing_drafts WHERE app_id = ? AND locale = ?", (app_id, locale))
    return dict(json.loads(row["fields"])) if row else {}


def save_draft(db: Database, app_id: int, locale: str, fields: Mapping[str, str | None]) -> dict[str, str]:
    """Merge fields into the saved draft (None leaves a field as it is, '' clears it from the draft)."""
    _validate(fields)
    merged = {**draft(db, app_id, locale), **{k: v for k, v in fields.items() if v is not None}}
    merged = {k: v for k, v in merged.items() if v != ""}
    db.execute(
        "INSERT INTO listing_drafts (app_id, locale, fields, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (app_id, locale) DO UPDATE SET fields = excluded.fields, updated_at = excluded.updated_at",
        (app_id, locale, json.dumps(merged, ensure_ascii=False), now_utc()))
    return merged


# ------------------------------------------------------------------ plans

def _clip(v: Any, n: int = 70) -> str:
    s = "" if v is None else str(v).replace("\n", " ⏎ ")
    return s if len(s) <= n else s[: n - 1] + "…"


def present(plan: plans.Plan) -> dict[str, Any]:
    """A plan as a tool result: id, changes, notes and a current → new table to show the user."""
    rows = [{"where": c.get("area", ""), "locale": c.get("locale", ""), "field": c["field"],
             "current": _clip(c.get("current")) or "(empty)", "new": _clip(c.get("new"))} for c in plan["changes"]]
    display = f"**Plan `{plan['id']}`** · expires {plan['expires_at']}\n\n" + render.table(
        rows, ["where", "locale", "field", "current", "new"])
    if plan["notes"]:
        display += "\n\n" + "\n".join(f"- {n}" for n in plan["notes"])
    return {"plan_id": plan["id"], "kind": plan["kind"], "changes": plan["changes"], "notes": plan["notes"],
            "expires_at": plan["expires_at"], "display": display, "next": APPLY_NOTE}


def _add(changes: list[dict[str, Any]], area: str, locale: str, field: str, current: Any, new: str,
         target: dict[str, Any]) -> None:
    if (current or "") != new:
        changes.append({"area": area, "locale": locale, "field": field, "current": current or "", "new": new,
                        "target": target})


def plan_listing(db: Database, asc: ASC, app: App, locale: str, fields: Mapping[str, str | None]) -> plans.Plan:
    """A plan that makes App Store Connect match these fields for one locale."""
    sid = asc_app_id(asc, app)
    changes, notes, cur = _locale_changes(asc, sid, locale, fields)
    if not changes:
        raise RankwellError("Nothing to change: App Store Connect already has these values."
                            + (" " + " ".join(notes) if notes else ""))
    return plans.create(db, "listing", app["id"], changes, plans.state_hash(cur),
                        payload={"asc_app_id": sid, "locale": locale, "live_indexed": _indexed(cur)}, notes=notes)


def _indexed(cur: dict[str, Any]) -> dict[str, str]:
    return {k: v for k, v in cur["fields"].items() if k in INDEXED and v}


def plan_listing_locales(db: Database, asc: ASC, app: App,
                         by_locale: Mapping[str, Mapping[str, str | None]]) -> plans.Plan:
    """One plan for several languages at once: {locale: fields}. Applied together, re-checked per locale."""
    if not by_locale:
        raise RankwellError("No languages given.")
    for locale, fields in by_locale.items():  # check every language before reading App Store Connect
        _check_fields(fields, locale)
    sid = asc_app_id(asc, app)
    changes: list[dict[str, Any]] = []
    notes: list[str] = []
    state: dict[str, Any] = {}
    for locale, fields in by_locale.items():
        cs, ns, cur = _locale_changes(asc, sid, locale, fields)
        changes += cs
        notes += [n for n in ns if n not in notes]
        state[locale] = cur
    if not changes:
        raise RankwellError("Nothing to change: App Store Connect already has these values in every language."
                            + (" " + " ".join(notes) if notes else ""))
    return plans.create(db, "listing", app["id"], changes, plans.state_hash(state),
                        payload={"asc_app_id": sid, "locales": list(by_locale),
                                 "live_indexed": {loc: _indexed(cur) for loc, cur in state.items()}}, notes=notes)


def _check_fields(fields: Mapping[str, str | None], locale: str | None = None) -> dict[str, str]:
    _validate(fields)
    new = {k: v for k, v in fields.items() if v is not None}
    over = [f"{k} is {len(v)} characters (limit {LIMITS[k]})" for k, v in new.items()
            if k in LIMITS and len(v) > LIMITS[k]]
    if over:
        raise RankwellError(("" if locale is None else f"{locale}: ") + "Too long: " + "; ".join(over) + ".")
    return new


def _locale_changes(asc: ASC, sid: int, locale: str, fields: Mapping[str, str | None]
                    ) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """The changes for one locale, notes, and the live state they were built from."""
    new = _check_fields(fields)
    cur = live(asc, sid, locale)
    ev, lv, ids = cur["edit_version"], cur["live_version"], cur["ids"]
    changes: list[dict[str, Any]] = []
    notes: list[str] = []

    def note(text: str) -> None:
        if text not in notes:
            notes.append(text)

    ev_target = {"kind": "appStoreVersionLocalizations", "id": ids["edit_version_loc"],
                 "parent": ev["id"] if ev else None}
    for field, value in new.items():
        if field in INFO_FIELDS:
            if not cur["edit_info_id"]:
                note(f"{field} skipped: name, subtitle and privacy URLs change only with a version in preparation "
                     "(plan_create_version).")
                continue
            _add(changes, "App info", locale, field, cur["fields"].get(field), value,
                 {"kind": "appInfoLocalizations", "id": ids["edit_info_loc"], "parent": cur["edit_info_id"]})
        elif field == "promotional_text":
            if ev:
                _add(changes, f"Version {ev['version']}", locale, field, cur["fields"].get(field), value, ev_target)
            if lv and ids["live_version_loc"]:
                _add(changes, f"Live version {lv['version']}", locale, field, cur["live_fields"].get(field), value,
                     {"kind": "appStoreVersionLocalizations", "id": ids["live_version_loc"], "parent": lv["id"]})
                note("Promotional text on the live version shows right away, without review.")
        elif field == "whats_new" and not lv:
            note("What's New skipped: Apple does not allow it on an app's first version.")
        elif ev:
            _add(changes, f"Version {ev['version']}", locale, field, cur["fields"].get(field), value, ev_target)
        else:
            note(f"{field} skipped: needs a version in preparation (plan_create_version).")
    if ev and not ids["edit_version_loc"] and any(c["target"] is ev_target for c in changes):
        note(f"{locale} is a new language for version {ev['version']}; it will be added.")
    if cur["edit_info_id"] and not ids["edit_info_loc"] and any(
            c["target"]["kind"] == "appInfoLocalizations" for c in changes):
        note(f"{locale} is a new language for the app info; it will be added.")
    return changes, notes, cur


_PARENT = {"appInfoLocalizations": ("appInfo", "appInfos", "/v1/appInfos/{}/appInfoLocalizations"),
           "appStoreVersionLocalizations": ("appStoreVersion", "appStoreVersions",
                                            "/v1/appStoreVersions/{}/appStoreVersionLocalizations")}


def _set_loc(asc: ASC, kind: str, loc_id: str | None, parent: str, locale: str, attrs: dict[str, Any],
             shown: dict[str, Any] | None = None) -> str:
    """PATCH the localization, or POST a new one for a new language.

    `shown` is what the plan showed as current per attribute. When the POST gets 409 because Apple created the
    localization on its own meanwhile, it is re-read and patched only if none of the planned attributes holds a
    value the user did not see (non-empty and different from `shown`); otherwise PlanError.
    """
    if loc_id:
        asc.patch(f"/v1/{kind}/{loc_id}", {"data": {"type": kind, "id": loc_id, "attributes": attrs}})
        return loc_id
    rel, parent_type, list_path = _PARENT[kind]
    try:
        r = asc.post(f"/v1/{kind}", {"data": {
            "type": kind, "attributes": {"locale": locale, **attrs},
            "relationships": {rel: {"data": {"type": parent_type, "id": parent}}}}})
        return str(r["data"]["id"])
    except StoreError as e:
        # Apple sometimes adds the language itself (a new app-info language also adds the version one).
        if e.status != 409:
            raise
        cur = _by_locale(asc.get_all(list_path.format(parent))).get(locale)
        if not cur:
            raise
        live_attrs = cur.get("attributes") or {}
        clash = [a for a in attrs
                 if (live_attrs.get(a) or "") and (live_attrs.get(a) or "") != ((shown or {}).get(a) or "")]
        if clash:
            raise PlanError("App Store Connect created this localization meanwhile; make a new plan "
                            f"({locale}: {', '.join(clash)} already set there).") from e
        asc.patch(f"/v1/{kind}/{cur['id']}", {"data": {"type": kind, "id": cur["id"], "attributes": attrs}})
        return str(cur["id"])


@plans.applier("listing")
def _apply_listing(db: Database, plan: plans.Plan) -> dict[str, Any]:
    asc = connect()
    if plan.get("locales"):
        plans.verify(plan, plans.state_hash({loc: live(asc, plan["asc_app_id"], loc) for loc in plan["locales"]}))
    else:
        plans.verify(plan, plans.state_hash(live(asc, plan["asc_app_id"], plan["locale"])))
    groups: dict[tuple[str, str, str | None, str], list[dict[str, Any]]] = {}
    for c in plan["changes"]:
        t = c["target"]
        groups.setdefault((c["locale"], t["kind"], t["id"], t["parent"]), []).append(c)
    done: list[str] = []
    errors: list[str] = []
    refused = False
    for (locale, kind, loc_id, parent), cs in groups.items():
        attrs = {FIELDS[c["field"]]: c["new"] for c in cs}
        shown = {FIELDS[c["field"]]: c["current"] for c in cs}
        try:
            rid = _set_loc(asc, kind, loc_id, parent, locale, attrs, shown)
        except (StoreError, PlanError) as e:
            for c in cs:
                plans.audit(db, plan["id"], "listing", f"{kind}/{loc_id or 'new'}", c["field"], c["current"],
                            c["new"], "error", str(e))
            errors.append((f"{locale} " if plan.get("locales") else "") + f"{', '.join(c['field'] for c in cs)}: {e}")
            refused = refused or isinstance(e, PlanError)
            continue
        for c in cs:
            plans.audit(db, plan["id"], "listing", f"{kind}/{rid}", c["field"], c["current"], c["new"])
            done.append(f"{c['area']}: {c['field']}" + (f" ({locale})" if plan.get("locales") else ""))
    if errors and not done:
        if refused:
            raise PlanError("Nothing was changed. " + " | ".join(errors))
        raise RankwellError("Nothing was changed. " + " | ".join(errors))
    return {"changed": done, "errors": errors}


def _vtuple(v: str) -> tuple[int, ...]:
    """A version string as numbers for comparing: a non-numeric part counts as 0 ("1.2b" → (1, 0)) and trailing
    zeros are dropped ("1.2.0" == "1.2"). Never raises."""
    parts = [int(p) if re.fullmatch(r"\s*[0-9]+\s*", p) else 0 for p in str(v).split(".")]
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _versions_state(vs: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [(v["id"], v["version"], v["state"]) for v in vs]


def plan_create_version(db: Database, asc: ASC, app: App, version: str) -> plans.Plan:
    """A plan that creates a new iOS version in preparation (needed to change name, subtitle, keywords...)."""
    version = version.strip()
    if not re.fullmatch(r"\d+(\.\d+){0,2}", version):
        raise RankwellError(f"'{version}' is not a version number like 1.2 or 1.2.3.")
    sid = asc_app_id(asc, app)
    vs = versions(asc, sid)
    ev, lv, rv = pick(vs, ascmod.EDITABLE), pick(vs, ascmod.LIVE), pick(vs, ascmod.IN_REVIEW)
    if ev:
        raise RankwellError(f"Version {ev['version']} is already in preparation ({ev['state']}); use that one.")
    if rv:
        raise RankwellError(f"Version {rv['version']} is {rv['state']}; wait until it is released or rejected.")
    if lv and lv["version"] and _vtuple(version) <= _vtuple(lv["version"]):
        raise RankwellError(f"{version} must be higher than the live version {lv['version']}.")
    changes = [{"area": "App", "locale": "all", "field": "new version", "current": lv["version"] if lv else "",
                "new": version, "target": {"kind": "appStoreVersions"}}]
    notes = [f"Creates iOS version {version} in preparation. Its listing starts as a copy of the live one."]
    return plans.create(db, "create_version", app["id"], changes, plans.state_hash(_versions_state(vs)),
                        payload={"asc_app_id": sid, "version": version}, notes=notes)


@plans.applier("create_version")
def _apply_create_version(db: Database, plan: plans.Plan) -> dict[str, Any]:
    asc = connect()
    plans.verify(plan, plans.state_hash(_versions_state(versions(asc, plan["asc_app_id"]))))
    r = asc.post("/v1/appStoreVersions", {"data": {
        "type": "appStoreVersions", "attributes": {"platform": "IOS", "versionString": plan["version"]},
        "relationships": {"app": {"data": {"type": "apps", "id": str(plan["asc_app_id"])}}}}})
    vid = str(r["data"]["id"])
    plans.audit(db, plan["id"], "create_version", f"appStoreVersions/{vid}", "versionString", None, plan["version"])
    return {"version": plan["version"], "version_id": vid}

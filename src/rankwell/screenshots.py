"""App Store screenshots: read the sets of a version, scan a local folder, plan and apply an upload.

App Store Connect flow (developer.apple.com/documentation/appstoreconnectapi/uploading-assets-to-app-store-connect):
appStoreVersionLocalizations → appScreenshotSets (one per screenshotDisplayType) → appScreenshots.
Upload = POST a reservation (fileName, fileSize) → PUT each uploadOperations part (url, offset, length,
requestHeaders; no JWT) → PATCH uploaded=true with sourceFileChecksum (MD5 of the whole file) → poll
assetDeliveryState until COMPLETE or FAILED. Order = PATCH appScreenshotSets/{id}/relationships/appScreenshots.
Apple marks these resources deprecated in favour of the App Asset Library; they still work (2026-11-07).

Replacing is the one place Rankwell deletes App Store Connect data: the old screenshots of a set in a version
in preparation, and only after every new screenshot of the plan finished processing. Never on a live version.
"""

from __future__ import annotations

import hashlib
import re
import struct
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from . import listing, plans, render
from .apps import App
from .db import Database
from .errors import PlanError, RankwellError, StoreError
from .stores import asc as ascmod
from .stores import http
from .stores.asc import ASC, Resource

# Accepted pixel sizes per display type (iPhone and iPad), from Apple's "Screenshot specifications"
# (developer.apple.com/help/app-store-connect/reference/app-information/screenshot-specifications, read
# 2026-11-07). Apple lists sizes per device group; the group → screenshotDisplayType mapping is ours.
# Pairs are (width, height); _both() adds the landscape twin.


def _both(*sizes: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    return tuple(s for w, h in sizes for s in ((w, h), (h, w)))


SIZES: dict[str, tuple[tuple[int, int], ...]] = {
    "APP_IPHONE_67": _both((1260, 2736), (1290, 2796), (1320, 2868)),
    "APP_IPHONE_65": _both((1284, 2778), (1242, 2688)),
    "APP_IPHONE_61": _both((1179, 2556), (1206, 2622), (1170, 2532)),
    "APP_IPHONE_58": _both((1125, 2436), (1080, 2340)),
    "APP_IPHONE_55": _both((1242, 2208)),
    "APP_IPHONE_47": _both((750, 1334)),
    # Status-bar sizes: the landscape "without status bar" size is not the portrait one turned.
    "APP_IPHONE_40": ((640, 1096), (640, 1136), (1136, 600), (1136, 640)),
    "APP_IPHONE_35": ((640, 920), (640, 960), (960, 600), (960, 640)),
    "APP_IPAD_PRO_3GEN_129": _both((2064, 2752), (2048, 2732)),
    "APP_IPAD_PRO_129": _both((2048, 2732)),
    "APP_IPAD_PRO_3GEN_11": _both((1488, 2266), (1512, 2316), (1668, 2420), (1668, 2388), (1640, 2360)),
    "APP_IPAD_105": _both((1668, 2224)),
    "APP_IPAD_97": ((1536, 2008), (1536, 2048), (2048, 1496), (2048, 1536), (768, 1004), (768, 1024),
                    (1024, 748), (1024, 768)),
}
LABELS = {
    "APP_IPHONE_67": 'iPhone 6.9"/6.7" (Dynamic Island, large)',
    "APP_IPHONE_65": 'iPhone 6.5" (Face ID, large)',
    "APP_IPHONE_61": 'iPhone 6.3"/6.1" (medium)',
    "APP_IPHONE_58": 'iPhone 5.8"/5.4"',
    "APP_IPHONE_55": 'iPhone 5.5" (Home button, large)',
    "APP_IPHONE_47": 'iPhone 4.7"',
    "APP_IPHONE_40": 'iPhone 4"',
    "APP_IPHONE_35": 'iPhone 3.5"',
    "APP_IPAD_PRO_3GEN_129": 'iPad 13"',
    "APP_IPAD_PRO_129": 'iPad 12.9" (2nd gen)',
    "APP_IPAD_PRO_3GEN_11": 'iPad 11"',
    "APP_IPAD_105": 'iPad 10.5"',
    "APP_IPAD_97": 'iPad 9.7"',
}
# Sizes Apple accepts that have no screenshotDisplayType in the API yet.
NO_API_TYPE = {s: "iPhone Duo" for s in _both((1398, 2034), (2007, 2853))}
MAIN_IPHONE = ("APP_IPHONE_67", "APP_IPHONE_65", "APP_IPHONE_61")
IPAD_13 = ("APP_IPAD_PRO_3GEN_129", "APP_IPAD_PRO_129")
MAX_PER_SET = 10
EXTENSIONS = {".png", ".jpg", ".jpeg"}
LOCALE_RE = re.compile(r"[a-z]{2,3}(-[A-Z][a-z]{3})?(-[A-Z]{2})?")
ANY = "any"

WAIT_TIMEOUT = 600.0  # seconds to wait for Apple to process new screenshots
WAIT_INTERVAL = 3.0
sleep: Callable[[float], None] = time.sleep
clock: Callable[[], float] = time.monotonic
_progress: Callable[[str], None] | None = None


def on_progress(fn: Callable[[str], None] | None) -> None:
    """Report upload steps (the CLI prints them)."""
    global _progress
    _progress = fn


def _say(text: str) -> None:
    if _progress:
        _progress(text)


def display_types(width: int, height: int) -> list[str]:
    """Display types that accept this pixel size; the first one is the default."""
    return [d for d, sizes in SIZES.items() if (width, height) in sizes]


def display_name(name: str) -> str | None:
    """A folder name as a display type ("APP_IPHONE_67", "iphone_67"), "any", or None when it is neither."""
    n = name.strip()
    if n.lower() == ANY:
        return ANY
    up = n.upper().replace("-", "_")
    up = up if up.startswith("APP_") else "APP_" + up
    return up if up in SIZES else None


# ------------------------------------------------------------------ image headers

def image_info(path: Path) -> dict[str, Any]:
    """Format, pixel size and whether a PNG has alpha or transparency, from the file header only."""
    with path.open("rb") as f:
        head = f.read(8)
        if head == b"\x89PNG\r\n\x1a\n":
            return _png(f)
        if head[:2] == b"\xff\xd8":
            f.seek(2)
            return _jpeg(f)
    raise ValueError("not a PNG or JPEG file")


def _png(f: BinaryIO) -> dict[str, Any]:
    width = height = color = 0
    transparent = False
    while True:
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        length, ctype = struct.unpack(">I4s", hdr)
        if ctype == b"IHDR":
            width, height, _, color = struct.unpack(">IIBB", f.read(10))
            f.seek(length - 10 + 4, 1)
            continue
        if ctype == b"tRNS":
            transparent = True
        if ctype in (b"IDAT", b"IEND"):
            break
        f.seek(length + 4, 1)
    if not width:
        raise ValueError("PNG without a size header")
    # Colour types 4 and 6 carry an alpha channel; a tRNS chunk adds transparency to the others.
    return {"format": "png", "width": width, "height": height, "alpha": color in (4, 6) or transparent}


_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg(f: BinaryIO) -> dict[str, Any]:
    while True:
        b = f.read(1)
        if not b:
            break
        if b != b"\xff":
            continue
        m = f.read(1)
        while m == b"\xff":  # fill bytes
            m = f.read(1)
        if not m:
            break
        marker = m[0]
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            continue
        if marker in (0xD9, 0xDA):  # end of image or start of scan before any frame header
            break
        seg = f.read(2)
        if len(seg) < 2:
            break
        length = struct.unpack(">H", seg)[0]
        if marker in _SOF:
            _, height, width = struct.unpack(">BHH", f.read(5))
            return {"format": "jpeg", "width": width, "height": height, "alpha": False}
        f.seek(length - 2, 1)
    raise ValueError("JPEG without a frame header")


def md5(path: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ------------------------------------------------------------------ scan

def _natural(name: str) -> list[Any]:
    return [(0, int(t), "") if t.isdigit() else (1, 0, t.lower()) for t in re.split(r"(\d+)", name) if t]


def _images(folder: Path) -> list[Path]:
    return sorted((p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")
                   and p.suffix.lower() in EXTENSIONS), key=lambda p: _natural(p.name))


def _subdirs(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith("."))


def _file(path: Path, locale: str | None, declared: str | None, notes: list[str]) -> dict[str, Any]:
    f: dict[str, Any] = {"path": str(path), "name": path.name, "locale": locale, "declared": declared,
                         "bytes": path.stat().st_size, "format": None, "width": None, "height": None,
                         "alpha": None, "display": None, "problems": []}
    try:
        f.update(image_info(path))
    except (OSError, ValueError, struct.error) as e:
        f["problems"].append(f"cannot read it ({e})")
        return f
    w, h = f["width"], f["height"]
    fits = display_types(w, h)
    if declared and declared != ANY:
        if declared in fits:
            f["display"] = declared
        else:
            f["problems"].append(f"{w}×{h} does not fit {declared} (accepted: "
                                 + ", ".join(f"{a}×{b}" for a, b in SIZES[declared][:6]) + ")")
    elif fits:
        f["display"] = fits[0]
        if len(fits) > 1:
            note = (f"{w}×{h} fits {' and '.join(fits)}; used {fits[0]}. Put files in a <locale>/{fits[1]} "
                    "folder for the other slot.")
            if note not in notes:
                notes.append(note)
    elif (w, h) in NO_API_TYPE:
        f["problems"].append(f"{w}×{h} is an {NO_API_TYPE[(w, h)]} size; the API has no display type for it yet "
                             "(upload it in App Store Connect)")
    else:
        f["problems"].append(f"{w}×{h} is not an accepted iPhone or iPad screenshot size")
    if f["alpha"]:
        f["problems"].append("PNG has an alpha channel or transparency; Apple rejects it (export without alpha)")
    return f


def scan(folder: str | Path, locale: str | None = None) -> dict[str, Any]:
    """Read a folder of screenshots: size, display type, alpha, count per set, problems.

    Layouts: <folder>/<locale>/<display type or "any">/01-*.png, <folder>/<locale>/*.png (display type from
    the pixel size), or a flat <folder>/*.png or <folder>/<display type>/*.png for the given locale.
    Files are ordered by name with numbers compared as numbers (2 before 10).
    """
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise RankwellError(f"No folder {root}.")
    problems: list[str] = []
    notes: list[str] = []
    dirs = _subdirs(root)
    locale_dirs = {d.name: d for d in dirs if LOCALE_RE.fullmatch(d.name) and display_name(d.name) is None}
    if locale_dirs:
        targets: dict[str | None, Path] = {k: v for k, v in locale_dirs.items() if locale in (None, k)}
        if locale and locale not in locale_dirs:
            problems.append(f"No {locale} folder in {root} (found: {', '.join(sorted(locale_dirs))}).")
        if _images(root):
            notes.append(f"Files directly in {root} are ignored: it has locale folders.")
    else:
        targets = {locale: root}
    files: list[dict[str, Any]] = []
    for loc, d in targets.items():
        for p in _images(d):
            files.append(_file(p, loc, None, notes))
        for sub in _subdirs(d):
            declared = display_name(sub.name)
            if declared is None:
                if sub.name not in locale_dirs:
                    problems.append(f"Unknown folder {sub.relative_to(root)}: name it after a display type "
                                    "(e.g. APP_IPHONE_67, APP_IPAD_PRO_3GEN_129) or 'any'.")
                continue
            files.extend(_file(p, loc, declared, notes) for p in _images(sub))
    sets: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for f in files:
        if f["display"] and not f["problems"]:
            sets.setdefault(f["locale"] or "", {}).setdefault(f["display"], []).append(f)
    for set_loc, by_type in sets.items():
        for disp, fs in by_type.items():
            if len(fs) > MAX_PER_SET:
                problems.append(f"{set_loc or 'files'} {disp}: {len(fs)} screenshots; Apple allows at most "
                                f"{MAX_PER_SET}.")
    problems += [f"{f['name']}: {p}" for f in files for p in f["problems"]]
    if not files:
        problems.append(f"No .png or .jpg files in {root}.")
    if any(f["locale"] is None for f in files):
        notes.append("No locale: pass one, or use <folder>/<locale>/ folders.")
    return {"folder": str(root), "locales": sorted(k for k in sets if k), "sets": sets, "files": files,
            "problems": problems, "notes": notes, "ok": not problems}


def scan_display(result: dict[str, Any]) -> str:
    rows = [{"locale": f["locale"] or "?", "set": f["display"] or f["declared"] or "?", "file": f["name"],
             "size": f"{f['width']}×{f['height']}" if f["width"] else "?",
             "ok": "✗ " + "; ".join(f["problems"]) if f["problems"] else "✓"} for f in result["files"]]
    out = [f"**{result['folder']}** · {len(result['files'])} file(s)", render.table(rows)]
    counts = [{"locale": loc or "?", "set": d, "label": LABELS.get(d, d), "count": len(fs)}
              for loc, by in result["sets"].items() for d, fs in by.items()]
    if counts:
        out.append(render.table(counts, align_right=["count"]))
    if result["problems"]:
        out.append("**Problems**\n" + "\n".join(f"- {p}" for p in result["problems"]))
    if result["notes"]:
        out.append("\n".join(f"- {n}" for n in result["notes"]))
    return "\n\n".join(out)


# ------------------------------------------------------------------ live

def _shot(r: Resource) -> dict[str, Any]:
    a = r.get("attributes") or {}
    st = a.get("assetDeliveryState") or {}
    img = a.get("imageAsset") or {}
    return {"id": r["id"], "file": a.get("fileName"), "bytes": a.get("fileSize"),
            "checksum": a.get("sourceFileChecksum"), "state": st.get("state"),
            "errors": [e.get("description") or e.get("code") for e in st.get("errors") or []],
            "width": img.get("width"), "height": img.get("height")}


def set_shots(asc: ASC, set_id: str) -> list[dict[str, Any]]:
    """Screenshots of one set, in their App Store order."""
    return [_shot(r) for r in asc.get_all(f"/v1/appScreenshotSets/{set_id}/appScreenshots")]


def live(asc: ASC, store_id: int, locale: str) -> dict[str, Any]:
    """Screenshot sets of one locale for the version in preparation (or the live version when none)."""
    vs = listing.versions(asc, store_id)
    ev, lv = listing.pick(vs, ascmod.EDITABLE), listing.pick(vs, ascmod.LIVE)
    v = ev or lv
    out: dict[str, Any] = {"store_id": store_id, "locale": locale, "version": v, "editable": ev is not None,
                           "in_review": listing.pick(vs, ascmod.IN_REVIEW), "version_loc_id": None, "sets": [],
                           "version_locales": []}
    if not v:
        return out
    locs = {r["attributes"]["locale"]: r for r in
            asc.get_all(f"/v1/appStoreVersions/{v['id']}/appStoreVersionLocalizations")}
    out["version_locales"] = sorted(locs)
    loc = locs.get(locale)
    if not loc:
        return out
    out["version_loc_id"] = loc["id"]
    for s in asc.get_all(f"/v1/appStoreVersionLocalizations/{loc['id']}/appScreenshotSets"):
        shots = set_shots(asc, s["id"])
        d = s["attributes"]["screenshotDisplayType"]
        out["sets"].append({"id": s["id"], "display": d, "label": LABELS.get(d, d), "count": len(shots),
                            "screenshots": shots})
    return out


def live_display(cur: dict[str, Any]) -> str:
    v = cur["version"]
    if not v:
        return f"**{cur['locale']}** · no App Store version yet."
    kind = "in preparation" if cur["editable"] else "live (read only)"
    head = f"**{cur['locale']}** · version {v['version']} {kind} ({v['state']})"
    if not cur["version_loc_id"]:
        return head + f"\n\nThe version has no {cur['locale']} localization (has: " \
                      f"{', '.join(cur['version_locales']) or 'none'})."
    rows = []
    for s in cur["sets"]:
        states = sorted({x["state"] or "?" for x in s["screenshots"]})
        rows.append({"set": s["display"], "label": s["label"], "count": s["count"],
                     "files": _names(s["screenshots"], 6),
                     "state": ", ".join(states)})
    return head + "\n\n" + render.table(rows, ["set", "label", "count", "files", "state"], align_right=["count"])


# ------------------------------------------------------------------ plan

def _hash_state(cur: dict[str, Any]) -> dict[str, Any]:
    v = cur["version"]
    return {"version": (v["id"], v["state"]) if v else None, "editable": cur["editable"], "loc": cur["version_loc_id"],
            "sets": sorted((s["display"], s["id"], [(x["id"], x["checksum"]) for x in s["screenshots"]])
                           for s in cur["sets"])}


def _names(files: list[dict[str, Any]], n: int = 4) -> str:
    names = [f.get("file") or f.get("name") or "?" for f in files]
    more = f", +{len(names) - n} more" if len(names) > n else ""
    return f"{len(names)}: " + ", ".join(names[:n]) + more if names else "(none)"


def plan_screenshots(db: Database, asc: ASC, app: App, locale: str, folder: str | Path,
                     mode: str = "replace") -> plans.Plan:
    """A plan that uploads the folder's screenshots to the version in preparation, per display type.

    replace: the new files become the whole set (old ones deleted after every upload finished).
    append: the new files go after the current ones. Sets not in the folder are left alone.
    """
    if mode not in ("replace", "append"):
        raise RankwellError("mode must be 'replace' or 'append'.")
    found = scan(folder, locale)
    if found["problems"]:
        raise RankwellError("Fix these first:\n" + "\n".join(f"- {p}" for p in found["problems"]))
    wanted = found["sets"].get(locale) or {}
    if not wanted:
        raise RankwellError(f"No {locale} screenshots in {found['folder']}.")
    sid = listing.asc_app_id(asc, app)
    cur = live(asc, sid, locale)
    v = cur["version"]
    if not cur["editable"]:
        rv = cur["in_review"]
        raise RankwellError(f"Version {rv['version']} is {rv['state']}; screenshots change only on a version in "
                            "preparation." if rv else "Screenshots change only on a version in preparation; create "
                            "one first (plan_create_version). The live version is never touched.")
    if not cur["version_loc_id"]:
        raise RankwellError(f"Version {v['version']} has no {locale} localization (has: "
                            f"{', '.join(cur['version_locales']) or 'none'}). Add the language first (plan_listing "
                            "with that locale).")
    by_type = {s["display"]: s for s in cur["sets"]}
    changes: list[dict[str, Any]] = []
    notes: list[str] = []
    sets: dict[str, dict[str, Any]] = {}
    for d in sorted(wanted, key=list(SIZES).index):
        new = [{"path": f["path"], "name": f["name"], "bytes": f["bytes"], "md5": md5(Path(f["path"])),
                "width": f["width"], "height": f["height"]} for f in wanted[d]]
        s = by_type.get(d)
        old = [{"id": x["id"], "file": x["file"], "checksum": x["checksum"]} for x in (s["screenshots"] if s else [])]
        if mode == "replace" and [o["checksum"] for o in old] == [n["md5"] for n in new]:
            notes.append(f"{d} already has these screenshots in this order; skipped.")
            continue
        if mode == "append" and len(old) + len(new) > MAX_PER_SET:
            raise RankwellError(f"{d} has {len(old)} screenshots; adding {len(new)} goes over {MAX_PER_SET}. "
                                "Use mode='replace' or fewer files.")
        if mode == "replace":
            new_text = "replace with " + _names(new)
        else:
            new_text = f"add {_names(new)} → {len(old) + len(new)} total"
        changes.append({"area": f"Version {v['version']}", "locale": locale, "field": f"{d} ({LABELS[d]})",
                        "current": _names(old), "new": new_text,
                        "target": {"kind": "appScreenshotSets", "id": s["id"] if s else None, "display": d}})
        # A set holds at most 10, also while uploading: when old + new is over 10, the new files that fit go
        # first, then the old ones are deleted, then the rest.
        room = len(new) if mode == "append" else max(0, min(len(new), MAX_PER_SET - len(old)))
        pre = old[-1:] if room == 0 and new else []  # a full set: make room for one upload to prove the files
        room = max(room, len(pre))
        sets[d] = {"set_id": s["id"] if s else None, "old": old, "new": new, "first": room, "pre_delete": pre}
        if pre:
            notes.append(f"{d} is full ({len(old)}): its last screenshot ({pre[0]['file']}) is deleted first to make "
                         "room for one new upload.")
        if room < len(new):
            notes.append(f"{d}: a set holds at most {MAX_PER_SET}, so {room} new screenshot(s) upload first, then "
                         f"the old ones are deleted, then the other {len(new) - room} upload. If one of those last "
                         "uploads fails, the set has fewer screenshots than planned.")
        if not s:
            notes.append(f"{d}: a new screenshot set will be added.")
    if not changes:
        raise RankwellError("Nothing to change: App Store Connect already has these screenshots."
                            + (" " + " ".join(notes) if notes else ""))
    deleting = [(d, len(s["old"])) for d, s in sets.items() if mode == "replace" and s["old"]]
    if deleting:
        notes.append("Replacing deletes the current screenshots of " + ", ".join(f"{d} ({n})" for d, n in deleting)
                     + f" in version {v['version']} (in preparation; the live version is not touched). They are "
                     "deleted only after the new screenshots (that fit in the set) finished processing; if any of "
                     "those uploads fails, nothing is deleted.")
    untouched = sorted(set(by_type) - set(wanted))
    if untouched:
        notes.append("Sets not in the folder stay as they are: " + ", ".join(untouched) + ".")
    have = set(by_type) | set(wanted)
    if not have & set(MAIN_IPHONE):
        notes.append("No iPhone 6.9\"/6.5\"/6.3\" set: Apple needs one of them to submit.")
    if any(d.startswith("APP_IPAD") for d in have) and not have & set(IPAD_13):
        notes.append("iPad screenshots without an iPad 13\" set: Apple needs 13\" when the app runs on iPad.")
    total = sum(len(s["new"]) for s in sets.values())
    mb = sum(n["bytes"] for s in sets.values() for n in s["new"]) / 1e6
    notes.append(f"Uploads {total} file(s), {mb:.1f} MB. A large upload can outlast a tool call: run "
                 f"`rankwell screenshots upload {app.get('slug') or app['id']} --locale {locale} "
                 f"--folder {found['folder']} --mode {mode}` in a terminal instead.")
    return plans.create(db, "screenshots", app["id"], changes, plans.state_hash(_hash_state(cur)),
                        payload={"asc_app_id": sid, "locale": locale, "mode": mode, "version": v["version"],
                                 "version_loc_id": cur["version_loc_id"], "folder": found["folder"], "sets": sets},
                        notes=notes)


def present(plan: plans.Plan) -> dict[str, Any]:
    """The plan as a tool result: the current → new table plus the files in upload order."""
    out = listing.present(plan)
    rows = [{"set": d, "#": i, "file": n["name"], "size": f"{n['width']}×{n['height']}",
             "MB": f"{n['bytes'] / 1e6:.2f}"}
            for d, s in plan["sets"].items() for i, n in enumerate(s["new"], 1)]
    out["display"] += "\n\n**New screenshots, in order**\n\n" + render.table(rows, align_right=["#", "MB"])
    out["mode"] = plan["mode"]
    return out


# ------------------------------------------------------------------ apply

class _UploadFailed(StoreError):
    def __init__(self, message: str, asset_id: str | None) -> None:
        super().__init__(message)
        self.asset_id = asset_id


def create_set(asc: ASC, version_loc_id: str, display: str) -> str:
    r = asc.post("/v1/appScreenshotSets", {"data": {
        "type": "appScreenshotSets", "attributes": {"screenshotDisplayType": display},
        "relationships": {"appStoreVersionLocalization": {
            "data": {"type": "appStoreVersionLocalizations", "id": version_loc_id}}}}})
    return str(r["data"]["id"])


def upload(asc: ASC, set_id: str, path: Path, name: str, size: int, checksum: str) -> str:
    """Reserve, PUT every part, commit with the MD5. Returns the new appScreenshots id."""
    r = asc.post("/v1/appScreenshots", {"data": {
        "type": "appScreenshots", "attributes": {"fileName": name, "fileSize": size},
        "relationships": {"appScreenshotSet": {"data": {"type": "appScreenshotSets", "id": set_id}}}}})
    aid = str(r["data"]["id"])
    ops = (r["data"].get("attributes") or {}).get("uploadOperations") or []
    try:
        if not ops:
            raise StoreError("App Store Connect returned no upload operations")
        with path.open("rb") as f:
            for op in ops:
                offset, length = int(op["offset"]), int(op["length"])
                f.seek(offset)
                chunk = f.read(length)
                if len(chunk) != length:
                    raise StoreError(f"{name} is shorter than planned (part at {offset})")
                headers = {h["name"]: h["value"] for h in op.get("requestHeaders") or []}
                # Upload URLs are pre-signed: no JWT, and the URL itself is never logged.
                resp = http.request(op.get("method") or "PUT", op["url"], content=chunk, headers=headers, retries=3)
                if resp.status_code >= 400:
                    raise StoreError(f"upload of part at byte {offset} → {resp.status_code}", resp.status_code)
        asc.patch(f"/v1/appScreenshots/{aid}", {"data": {
            "type": "appScreenshots", "id": aid, "attributes": {"uploaded": True, "sourceFileChecksum": checksum}}})
    except (StoreError, OSError, KeyError, ValueError, TypeError) as e:  # a file gone, an odd answer: per file
        raise _UploadFailed(str(e) or type(e).__name__, aid) from e
    return aid


def wait_delivery(asc: ASC, ids: list[str], timeout: float | None = None) -> dict[str, dict[str, Any]]:
    """Poll each screenshot until COMPLETE or FAILED; ones still processing at the timeout keep their state."""
    end = clock() + (WAIT_TIMEOUT if timeout is None else timeout)
    out: dict[str, dict[str, Any]] = {}
    pending = list(ids)
    while pending:
        for aid in list(pending):
            try:
                r = asc.get(f"/v1/appScreenshots/{aid}", **{"fields[appScreenshots]": "fileName,assetDeliveryState"})
            except StoreError as e:  # cannot tell: report it as not complete, never as done
                out[aid] = {"id": aid, "state": "UNKNOWN", "errors": [f"could not read its state: {e}"]}
                pending.remove(aid)
                continue
            out[aid] = _shot(r["data"])
            if out[aid]["state"] in ("COMPLETE", "FAILED"):
                pending.remove(aid)
        if pending:
            _say(f"  waiting for Apple to process {len(pending)} screenshot(s)…")
            if clock() >= end:
                break
            sleep(WAIT_INTERVAL)
    return out


def reorder(asc: ASC, set_id: str, ids: list[str]) -> None:
    asc.patch(f"/v1/appScreenshotSets/{set_id}/relationships/appScreenshots",
              {"data": [{"type": "appScreenshots", "id": i} for i in ids]})


def _upload_batch(db: Database, asc: ASC, pid: str, batch: list[tuple[str, str, dict[str, Any]]]
                  ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Upload (set, set id, file) items, then wait for Apple. Returns (complete, failed); every step audited."""
    committed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for d, set_id, n in batch:
        _say(f"  {d}: uploading {n['name']} ({n['bytes'] / 1e6:.1f} MB)")
        target = f"appScreenshotSets/{set_id}"
        try:
            aid = upload(asc, set_id, Path(n["path"]), n["name"], n["bytes"], n["md5"])
        except _UploadFailed as e:
            plans.audit(db, pid, "screenshots", target, d, None, f"{n['name']} → appScreenshots/{e.asset_id}",
                        "error", str(e))
            failed.append({"set": d, "file": n["name"], "id": e.asset_id, "error": str(e)})
            continue
        except StoreError as e:  # the reservation itself failed
            plans.audit(db, pid, "screenshots", target, d, None, n["name"], "error", str(e))
            failed.append({"set": d, "file": n["name"], "id": None, "error": str(e)})
            continue
        committed.append({"set": d, "file": n["name"], "id": aid})
    states = wait_delivery(asc, [u["id"] for u in committed]) if committed else {}
    done: list[dict[str, Any]] = []
    for u in committed:
        st = states.get(u["id"], {})
        target = f"appScreenshots/{u['id']}"
        if st.get("state") == "COMPLETE":
            plans.audit(db, pid, "screenshots", target, u["set"], None, f"{u['file']} uploaded")
            done.append(u)
        else:
            why = "; ".join(st.get("errors") or []) or f"still {st.get('state') or 'unknown'} after waiting"
            plans.audit(db, pid, "screenshots", target, u["set"], None, f"{u['file']} uploaded", "error",
                        f"{st.get('state')}: {why}")
            failed.append({**u, "error": f"{st.get('state') or 'unknown'}: {why}"})
    return done, failed


@plans.applier("screenshots")
def _apply_screenshots(db: Database, plan: plans.Plan) -> dict[str, Any]:
    asc = listing.connect()
    cur = live(asc, plan["asc_app_id"], plan["locale"])
    plans.verify(plan, plans.state_hash(_hash_state(cur)))
    for s in plan["sets"].values():
        for n in s["new"]:
            p = Path(n["path"])
            if not p.is_file() or md5(p) != n["md5"]:
                raise PlanError(f"{n['path']} changed or is gone since the plan was made. Make a new plan.")
    pid, loc_id = plan["id"], plan["version_loc_id"]
    result: dict[str, Any] = {"mode": plan["mode"], "locale": plan["locale"], "version": plan["version"],
                              "uploaded": [], "failed": [], "deleted": [], "reordered": [], "errors": []}
    set_ids: dict[str, str] = {}
    for d, s in plan["sets"].items():
        if s["set_id"]:
            set_ids[d] = s["set_id"]
            continue
        try:
            set_ids[d] = create_set(asc, loc_id, d)
        except StoreError as e:
            plans.audit(db, pid, "screenshots", f"appStoreVersionLocalizations/{loc_id}", d, None, "new set",
                        "error", str(e))
            result["failed"] += [{"set": d, "file": n["name"], "id": None, "error": f"could not add the set: {e}"}
                                 for n in s["new"]]
            continue
        plans.audit(db, pid, "screenshots", f"appStoreVersionLocalizations/{loc_id}", d, None,
                    f"new set appScreenshotSets/{set_ids[d]}")

    def delete(d: str, o: dict[str, Any]) -> None:
        try:
            asc.request("DELETE", f"/v1/appScreenshots/{o['id']}")
        except StoreError as e:
            if e.status != 404:  # already gone is fine
                plans.audit(db, pid, "screenshots", f"appScreenshots/{o['id']}", d, o["file"], None, "error", str(e))
                result["errors"].append(f"{d}: could not delete {o['file']}: {e}")
                return
        plans.audit(db, pid, "screenshots", f"appScreenshots/{o['id']}", d, o["file"], None)
        result["deleted"].append({"set": d, "file": o["file"], "id": o["id"]})

    for d, s in plan["sets"].items():
        for o in s.get("pre_delete") or []:
            delete(d, o)

    # Phase 1: every new file that fits next to the old ones. Old ones are deleted only if all of them made it.
    first = [(d, set_ids[d], n) for d, s in plan["sets"].items() if d in set_ids for n in s["new"][:s["first"]]]
    done, failed = _upload_batch(db, asc, pid, first)
    result["uploaded"] += done
    result["failed"] += failed
    if result["failed"]:
        total = sum(len(s["new"]) for s in plan["sets"].values())
        result["ok"] = False
        kept = (f"Only {len(result['deleted'])} old screenshot(s) were deleted to make room; the others are kept"
                if result["deleted"] else "Nothing was deleted")
        result["message"] = (f"{len(done)} of {total} screenshot(s) uploaded; {len(result['failed'])} failed. "
                             f"{kept} and the order was not changed. Fix the failed files and make a new plan "
                             "(replace mode also clears failed uploads).")
        if not done:
            raise RankwellError(result["message"] + " " + " | ".join(f"{f['file']}: {f['error']}"
                                                                     for f in result["failed"]))
        result["display"] = result_display(result)
        return result

    if plan["mode"] == "replace":
        gone = {x["id"] for x in result["deleted"]}
        for d, s in plan["sets"].items():
            for o in s["old"]:
                if o["id"] not in gone:
                    delete(d, o)

    # Phase 2: the files that did not fit before the old ones were gone.
    rest = [(d, set_ids[d], n) for d, s in plan["sets"].items() for n in s["new"][s["first"]:]]
    if rest:
        done, failed = _upload_batch(db, asc, pid, rest)
        result["uploaded"] += done
        result["failed"] += failed

    names = {u["id"]: u["file"] for u in result["uploaded"]}
    for d, s in plan["sets"].items():
        names.update({o["id"]: o["file"] for o in s["old"]})
        new_ids = [u["id"] for u in result["uploaded"] if u["set"] == d]
        want = new_ids if plan["mode"] == "replace" else [o["id"] for o in s["old"]] + new_ids
        try:
            now = [x["id"] for x in set_shots(asc, set_ids[d])]
            order = [i for i in want if i in now] + [i for i in now if i not in want]
            if order != now:
                reorder(asc, set_ids[d], order)
                plans.audit(db, pid, "screenshots", f"appScreenshotSets/{set_ids[d]}", f"{d} order",
                            [names.get(i, i) for i in now], [names.get(i, i) for i in order])
                result["reordered"].append(d)
        except StoreError as e:
            plans.audit(db, pid, "screenshots", f"appScreenshotSets/{set_ids[d]}", f"{d} order", None, None, "error",
                        str(e))
            result["errors"].append(f"{d}: could not set the order: {e}")
    result["ok"] = not result["errors"] and not result["failed"]
    result["message"] = (f"{len(result['uploaded'])} screenshot(s) uploaded"
                         + (f", {len(result['deleted'])} old one(s) deleted" if result["deleted"] else "")
                         + (f"; {len(result['failed'])} failed after the old ones were deleted" if result["failed"]
                            else "") + ".")
    result["display"] = result_display(result)
    return result


def result_display(result: dict[str, Any]) -> str:
    rows = ([{"set": u["set"], "file": u["file"], "result": "uploaded"} for u in result["uploaded"]]
            + [{"set": f["set"], "file": f["file"], "result": "✗ " + f["error"]} for f in result["failed"]]
            + [{"set": x["set"], "file": x["file"], "result": "deleted (old)"} for x in result["deleted"]])
    out = [f"**{result['message']}**", render.table(rows)]
    if result["errors"]:
        out.append("\n".join(f"- {e}" for e in result["errors"]))
    return "\n\n".join(out)

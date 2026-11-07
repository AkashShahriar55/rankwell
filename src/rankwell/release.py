"""Release pipeline: release.toml, check, build, upload, wait for processing, attach, review info, submit.

Every step that changes App Store Connect (or uploads a build) is a plan: plan_* reads the live state,
apply_plan re-reads it, refuses when it changed, writes, and audits. Nothing here deletes App Store
Connect data. After a rejection the version goes back through its own review submission: the REJECTED
item is marked resolved, then the submission is submitted again (a new submission is refused by Apple).

Building runs a shell command from release.toml on the user's machine, so build_start is two-step: without
confirmed=True it only returns the command, working directory and log path to show the user.

Upload commands (`[upload] kind = "custom"`, or a fastlane command) may use `{ipa}` for the .ipa path. Rankwell
inserts the path shell-quoted, so write `{ipa}` bare; `"{ipa}"` and `'{ipa}'` also work (the quotes around the
placeholder are dropped before the quoted path goes in, so paths with spaces or quotes stay one argument).
"""

from __future__ import annotations

import datetime as dt
import glob
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import tomllib
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import listing, paths, plans
from .apps import App
from .db import Database
from .errors import RankwellError, StoreError
from .secrets import AscKey
from .stores import asc as ascmod
from .stores.asc import ASC
from .util import now_utc

KINDS = ("flutter", "xcode", "fastlane", "custom")
UPLOAD_KINDS = ("altool", "fastlane", "custom")
RELEASE_TYPES = {"after-approval": "AFTER_APPROVAL", "manual": "MANUAL"}
_KEYS = {"build": {"kind", "command", "ipa", "scheme", "workspace", "project", "configuration", "export_options",
                   "lane"},
         "upload": {"kind", "command", "lane"},
         "release": {"type", "phased"},
         "play": {"command", "aab"}}  # Google Play build (play.py reads it; the iOS pipeline ignores it)
EXAMPLE = """[build]
kind = "flutter"            # flutter | xcode | fastlane | custom
ipa = "build/ios/ipa/*.ipa"

[upload]
kind = "altool"             # altool | fastlane | custom

[release]
type = "after-approval"     # or "manual"
phased = false

# [play]                    # Google Play (optional)
# command = "flutter build appbundle --release"
# aab = "build/app/outputs/bundle/release/*.aab"
"""
REQUIRED_IPHONE = ("APP_IPHONE_67", "APP_IPHONE_65")  # Apple needs one of these sizes for iPhone apps
IPAD_13 = ("APP_IPAD_PRO_3GEN_129", "APP_IPAD_PRO_129")
EXIT_MARK = "[rankwell] exit"

# (command, cwd) → (exit code, combined output). Tests replace it; nothing real runs in tests.
Runner = Callable[[list[str], Path], tuple[int, str]]
# (shell script, cwd, log file) → pid of the detached process.
Spawner = Callable[[str, Path, Path], int]


@dataclass(frozen=True)
class ReleaseConfig:
    build_kind: str
    build_command: str
    ipa: str | None
    upload_kind: str
    upload_command: str | None
    release_type: str
    phased: bool


# ------------------------------------------------------------------ release.toml

def _bad(msg: str) -> RankwellError:
    return RankwellError(f".aso/release.toml: {msg}\n\nExample:\n{EXAMPLE}")


def parse(data: dict[str, Any]) -> ReleaseConfig:
    """Validate a parsed release.toml and fill in the defaults for its build kind."""
    for section, body in data.items():
        if section not in _KEYS:
            raise _bad(f"unknown section [{section}] (use {', '.join(f'[{s}]' for s in _KEYS)}).")
        if not isinstance(body, dict):
            raise _bad(f"[{section}] must be a table.")
        for k, v in body.items():
            if k not in _KEYS[section]:
                raise _bad(f"unknown key '{k}' in [{section}] (use {', '.join(sorted(_KEYS[section]))}).")
            if not (isinstance(v, bool) if (section, k) == ("release", "phased") else isinstance(v, str)):
                want = "true or false" if k == "phased" else "a string"
                raise _bad(f"[{section}] {k} must be {want}.")
    b: dict[str, str] = data.get("build", {})
    u: dict[str, str] = data.get("upload", {})
    r: dict[str, Any] = data.get("release", {})
    q = shlex.quote

    kind = b.get("kind", "flutter")
    if kind not in KINDS:
        raise _bad(f"[build] kind must be one of {', '.join(KINDS)}, not '{kind}'.")
    command, ipa = b.get("command"), b.get("ipa")
    if kind == "flutter":
        extra = f" --export-options-plist={q(b['export_options'])}" if b.get("export_options") else ""
        command = command or "flutter build ipa --release" + extra
        ipa = ipa or "build/ios/ipa/*.ipa"
    elif kind == "xcode" and not command:
        if not b.get("scheme"):
            raise _bad("[build] kind = \"xcode\" needs scheme = \"<scheme>\".")
        if not b.get("export_options"):
            raise _bad("[build] kind = \"xcode\" needs export_options = \"<path to ExportOptions.plist>\".")
        src = f"-workspace {q(b['workspace'])} " if b.get("workspace") else (
            f"-project {q(b['project'])} " if b.get("project") else "")
        archive = f"build/rankwell/{b['scheme']}.xcarchive"
        command = (f"xcodebuild archive {src}-scheme {q(b['scheme'])} -configuration "
                   f"{q(b.get('configuration', 'Release'))} -destination 'generic/platform=iOS' "
                   f"-archivePath {q(archive)} && xcodebuild -exportArchive -archivePath {q(archive)} "
                   f"-exportPath build/rankwell/export -exportOptionsPlist {q(b['export_options'])}")
        ipa = ipa or "build/rankwell/export/*.ipa"
    elif kind == "fastlane" and not command:
        if not b.get("lane"):
            raise _bad("[build] kind = \"fastlane\" needs lane = \"<lane that builds the .ipa>\".")
        command = f"fastlane {q(b['lane'])}"
        ipa = ipa or "*.ipa"
    elif kind == "custom" and not command:
        raise _bad("[build] kind = \"custom\" needs command = \"<shell command>\".")
    assert command is not None

    ukind = u.get("kind", "altool")
    if ukind not in UPLOAD_KINDS:
        raise _bad(f"[upload] kind must be one of {', '.join(UPLOAD_KINDS)}, not '{ukind}'.")
    ucommand = u.get("command")
    if ukind == "fastlane" and not ucommand:
        if not u.get("lane"):
            raise _bad("[upload] kind = \"fastlane\" needs lane = \"<lane that uploads>\".")
        ucommand = f"fastlane {q(u['lane'])}"
    elif ukind == "custom" and not ucommand:
        raise _bad("[upload] kind = \"custom\" needs command = \"<shell command>\" ({ipa} is the .ipa path).")
    elif ukind == "altool" and ucommand:
        raise _bad("[upload] command is only for kind = \"fastlane\" or \"custom\".")

    rtype = r.get("type", "after-approval")
    if rtype not in RELEASE_TYPES:
        raise _bad(f"[release] type must be {' or '.join(RELEASE_TYPES)}, not '{rtype}'.")
    return ReleaseConfig(kind, command, ipa, ukind, ucommand, rtype, bool(r.get("phased", False)))


def repo_of(app: App) -> Path:
    if not app.get("repo_path"):
        raise RankwellError(f"{app['name']} has no repo path; set it with update_app(repo_path=...).")
    repo = Path(app["repo_path"]).expanduser()
    if not repo.is_dir():
        raise RankwellError(f"The repo path of {app['name']} does not exist: {repo}")
    return repo


def play_settings(repo: Path) -> dict[str, str]:
    """The [play] table of <repo>/.aso/release.toml (command, aab glob), checked like the rest; {} when absent."""
    f = repo / ".aso" / "release.toml"
    if not f.exists():
        return {}
    load(repo)  # same validation and messages as the iOS pipeline
    data = tomllib.loads(f.read_text())
    return dict(data.get("play", {}))


def load(repo: Path) -> ReleaseConfig:
    """Read <repo>/.aso/release.toml; a Flutter repo without one gets the Flutter defaults."""
    f = repo / ".aso" / "release.toml"
    if not f.exists():
        if (repo / "pubspec.yaml").exists():
            return parse({})
        raise RankwellError(f"No {f}. Create it, for example:\n{EXAMPLE}")
    try:
        data = tomllib.loads(f.read_text())
    except tomllib.TOMLDecodeError as e:
        raise _bad(f"not valid TOML ({e}).") from e
    return parse(data)


# ------------------------------------------------------------------ App Store Connect reads

BUILD_PAGE = 200  # the most /v1/builds returns per page
BUILD_PAGES = 10  # at most 2000 builds are read; an app version never has that many


def builds(asc: ASC, store_id: int, version: str | None = None, limit: int = BUILD_PAGE,
           max_pages: int = BUILD_PAGES) -> list[dict[str, Any]]:
    """Uploaded builds, newest first (of one app version when given), with processing state and export compliance.

    Every page is read (up to max_pages), so an older build of the version is never missed. With a version, the
    list is also filtered server-side by the marketing version (filter[preReleaseVersion.version]; filter[version]
    would be the build number), and again here.
    """
    params: dict[str, Any] = {
        "filter[app]": store_id, "sort": "-uploadedDate", "limit": min(limit, BUILD_PAGE),
        "fields[builds]": "version,processingState,uploadedDate,usesNonExemptEncryption,expired,preReleaseVersion",
        "include": "preReleaseVersion", "fields[preReleaseVersions]": "version"}
    if version:
        params["filter[preReleaseVersion.version]"] = version
    r = asc.get("/v1/builds", **params)
    data: list[dict[str, Any]] = list(r.get("data", []))
    pre = {p["id"]: p["attributes"].get("version") for p in r.get("included", [])}
    seen: set[str] = set()
    nxt = (r.get("links") or {}).get("next")
    while nxt and nxt not in seen and len(seen) < max_pages - 1:
        seen.add(nxt)
        r = asc.get(nxt)  # the next link carries every parameter
        data.extend(r.get("data", []))
        pre.update({p["id"]: p["attributes"].get("version") for p in r.get("included", [])})
        nxt = (r.get("links") or {}).get("next")
    out = []
    for b in data:
        a = b["attributes"]
        rel = ((b.get("relationships", {}).get("preReleaseVersion") or {}).get("data") or {}).get("id")
        out.append({"id": b["id"], "build": a.get("version"), "version": pre.get(rel),
                    "state": a.get("processingState"), "expired": bool(a.get("expired")),
                    "encryption": a.get("usesNonExemptEncryption"),
                    "uploaded": a.get("uploadedDate")})
    return [b for b in out if b["version"] == version] if version else out


def attached_build(asc: ASC, version_id: str) -> dict[str, Any] | None:
    data = asc.get(f"/v1/appStoreVersions/{version_id}/build").get("data")
    if not data:
        return None
    a = data["attributes"]
    return {"id": data["id"], "build": a.get("version"), "state": a.get("processingState"),
            "expired": bool(a.get("expired")), "encryption": a.get("usesNonExemptEncryption")}


def _get_or_none(asc: ASC, path: str, **params: Any) -> dict[str, Any] | None:
    try:
        data = asc.get(path, **params).get("data")
    except StoreError as e:
        if e.status == 404:
            return None
        raise
    return dict(data) if data else None


def review_detail(asc: ASC, version_id: str) -> tuple[str | None, dict[str, Any]]:
    d = _get_or_none(asc, f"/v1/appStoreVersions/{version_id}/appStoreReviewDetail")
    return (d["id"], dict(d.get("attributes") or {})) if d else (None, {})


def phased_release(asc: ASC, version_id: str) -> dict[str, Any] | None:
    return _get_or_none(asc, f"/v1/appStoreVersions/{version_id}/appStoreVersionPhasedRelease")


def screenshot_counts(asc: ASC, version_loc_id: str) -> dict[str, int]:
    """{display type: number of screenshots} for one version localization."""
    out = {}
    for s in asc.get_all(f"/v1/appStoreVersionLocalizations/{version_loc_id}/appScreenshotSets"):
        shots = asc.get_all(f"/v1/appScreenshotSets/{s['id']}/appScreenshots")
        out[s["attributes"]["screenshotDisplayType"]] = len(shots)
    return out


def _editable(asc: ASC, app: App) -> tuple[int, dict[str, Any]]:
    sid = listing.asc_app_id(asc, app)
    vs = listing.versions(asc, sid)
    ev = listing.pick(vs, ascmod.EDITABLE)
    if not ev:
        rv = listing.pick(vs, ascmod.IN_REVIEW)
        raise RankwellError(f"Version {rv['version']} is {rv['state']}; nothing to change until Apple finishes."
                            if rv else "No version in preparation. Create one first (plan_create_version).")
    return sid, ev


# ------------------------------------------------------------------ check

def _item(ok: bool | None, text: str) -> dict[str, Any]:
    return {"ok": ok, "item": text}


def check(asc: ASC, app: App) -> dict[str, Any]:
    """What Apple still needs before the version in preparation can be submitted.

    ok True = done, False = blocks submission, None = cannot be checked here (look in App Store Connect).
    """
    sid = listing.asc_app_id(asc, app)
    vs = listing.versions(asc, sid)
    ev, lv, rv = (listing.pick(vs, s) for s in (ascmod.EDITABLE, ascmod.LIVE, ascmod.IN_REVIEW))
    items: list[dict[str, Any]] = []
    if not ev:
        items = [_item(None, f"Version {rv['version']} is {rv['state']}: nothing to submit until Apple finishes.")
                 if rv else _item(False, "No version in preparation. Create one (plan_create_version).")]
        return {"version": None, "state": rv["state"] if rv else None, "items": items, "ready": False}
    vid = ev["id"]
    infos = asc.get_all(f"/v1/apps/{sid}/appInfos")
    ei = next((i for i in infos if ascmod.version_state(i["attributes"]) in ascmod.EDITABLE), None)
    info_locs = {r["attributes"]["locale"]: r["attributes"]
                 for r in asc.get_all(f"/v1/appInfos/{ei['id']}/appInfoLocalizations")} if ei else {}
    locs = asc.get_all(f"/v1/appStoreVersions/{vid}/appStoreVersionLocalizations")
    shots = {r["attributes"]["locale"]: screenshot_counts(asc, r["id"]) for r in locs}
    any_ipad = any(k.startswith("APP_IPAD") and n for s in shots.values() for k, n in s.items())
    for r in locs:
        a, loc = r["attributes"], r["attributes"]["locale"]
        info = info_locs.get(loc, {})
        missing = [f for f, v in (("name", info.get("name")), ("description", a.get("description")),
                                  ("keywords", a.get("keywords")), ("support URL", a.get("supportUrl")),
                                  ("privacy policy URL", info.get("privacyPolicyUrl"))) if not v]
        if lv and not a.get("whatsNew"):
            missing.append("What's New")
        have = {k for k, n in shots[loc].items() if n}
        if not have & set(REQUIRED_IPHONE):
            missing.append('iPhone 6.9"/6.5" screenshots')
        if any_ipad and not have & set(IPAD_13):
            missing.append('iPad 13" screenshots')
        items.append(_item(not missing, f"{loc}: " + ("complete" if not missing else "missing " + ", ".join(missing))))
    if not locs:
        items.append(_item(False, "The version has no localizations."))
    items.append(_item(bool(ev.get("copyright")), "Copyright set" if ev.get("copyright") else "Copyright is empty"))

    b = attached_build(asc, vid)
    if not b:
        items.append(_item(False, "No build attached (plan_attach_build)."))
    elif b["state"] != "VALID" or b["expired"]:
        items.append(_item(False, f"Attached build {b['build']} is {'expired' if b['expired'] else b['state']}."))
    elif b["encryption"] is None:
        items.append(_item(False, f"Build {b['build']}: export compliance (encryption) not answered; set "
                                  "ITSAppUsesNonExemptEncryption in Info.plist or answer it in App Store Connect."))
    else:
        items.append(_item(True, f"Build {b['build']} attached"))

    _, rd = review_detail(asc, vid)
    contact = all(rd.get(k) for k in ("contactFirstName", "contactLastName", "contactPhone", "contactEmail"))
    items.append(_item(contact, "App Review contact complete" if contact
                       else "App Review contact incomplete (plan_review_info)"))
    if rd.get("demoAccountRequired") and not (rd.get("demoAccountName") and rd.get("demoAccountPassword")):
        items.append(_item(False, "Demo account required but name or password is empty (plan_review_info)."))

    app_attrs = asc.get(f"/v1/apps/{sid}", **{"fields[apps]": "contentRightsDeclaration"})["data"]["attributes"]
    rights = app_attrs.get("contentRightsDeclaration")
    items.append(_item(bool(rights), "Content rights declared" if rights
                       else "Content rights not declared (App Store Connect → App Information)."))
    if ei:
        try:
            age = _get_or_none(asc, f"/v1/appInfos/{ei['id']}/ageRatingDeclaration")
        except StoreError:
            items.append(_item(None, "Age rating: could not read it; check in App Store Connect."))
        else:
            done = bool(age and any(v is not None for v in (age.get("attributes") or {}).values()))
            items.append(_item(done, "Age rating set" if done else "Age rating not set (App Store Connect → "
                                                                    "App Information → Age Rating)."))
    try:
        for p in asc.get_all(f"/v1/apps/{sid}/inAppPurchasesV2", limit=200):
            s = p["attributes"].get("state")
            if s in ("MISSING_METADATA", "DEVELOPER_ACTION_NEEDED", "REJECTED"):
                items.append(_item(False, f"In-app purchase {p['attributes'].get('productId')}: {s} (add price, review "
                                          "screenshot and localizations in App Store Connect)."))
    except StoreError:
        items.append(_item(None, "In-app purchases: could not read them."))
    items.append(_item(None, "App Privacy labels, pricing and availability: check in App Store Connect "
                             "(not available in the API)."))
    return {"version": ev["version"], "state": ev["state"], "first_version": lv is None, "items": items,
            "ready": all(i["ok"] is not False for i in items)}


def check_display(result: dict[str, Any]) -> str:
    mark = {True: "✓", False: "✗", None: "ℹ"}
    head = (f"**Version {result['version']}** ({result['state']}) · "
            + ("ready to submit" if result["ready"] else "not ready")) if result["version"] else "**No version**"
    return head + "\n\n" + "\n".join(f"- {mark[i['ok']]} {i['item']}" for i in result["items"])


# ------------------------------------------------------------------ build

_procs: dict[int, subprocess.Popen[bytes]] = {}


def _spawn(script: str, cwd: Path, log: Path) -> int:
    with log.open("ab") as out:
        p = subprocess.Popen(["/bin/sh", "-c", script], cwd=cwd, stdin=subprocess.DEVNULL,  # noqa: S603
                             stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    _procs[p.pid] = p
    return p.pid


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    if pid in _procs:
        return _procs[pid].poll() is None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 64_000))
        return f.read().decode(errors="replace").splitlines()[-lines:]


def _build_log(app: App) -> Path:
    return paths.logs_dir() / f"build-{app['slug']}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.log"


def build_start(db: Database, app: App, spawn: Spawner | None = None, *, confirmed: bool = False) -> dict[str, Any]:
    """Start the build command from release.toml in the app repo, detached, with output to a log file.

    Two steps, because it runs a shell command from a file in the repo: without confirmed=True nothing runs and
    the result is {"started": False, "command", "cwd", "log_path", "kind"} to show the user (the log is named by
    the start time). With confirmed=True it starts and returns the build_jobs row.
    """
    repo = repo_of(app)
    cfg = load(repo)
    running = db.one("SELECT * FROM build_jobs WHERE app_id = ? AND status = 'running' ORDER BY id DESC LIMIT 1",
                     (app["id"],))
    if running and _refresh(db, running)["status"] == "running":
        raise RankwellError(f"A build is already running (job {running['id']}, log {running['log_path']}).")
    log = _build_log(app)
    if not confirmed:
        return {"started": False, "command": cfg.build_command, "cwd": str(repo), "log_path": str(log),
                "kind": cfg.build_kind}
    log.write_text(f"$ cd {repo}\n$ {cfg.build_command}\n")
    script = f"{cfg.build_command}\ncode=$?\necho \"{EXIT_MARK} $code\"\nexit $code\n"
    pid = (spawn or _spawn)(script, repo, log)
    cur = db.execute(
        "INSERT INTO build_jobs (app_id, kind, command, cwd, log_path, pid, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (app["id"], cfg.build_kind, cfg.build_command, str(repo), str(log), pid, now_utc()))
    row = db.one("SELECT * FROM build_jobs WHERE id = ?", (cur.lastrowid,))
    assert row is not None
    return row


def _refresh(db: Database, job: dict[str, Any]) -> dict[str, Any]:
    if job["status"] != "running":
        return job
    tail = _tail(Path(job["log_path"]), 5)
    mark = next((ln for ln in reversed(tail) if ln.startswith(EXIT_MARK)), None)
    if mark:
        code = int(mark.split()[-1])
        job.update(status="ok" if code == 0 else "failed", exit_code=code, finished_at=now_utc())
    elif not _alive(job["pid"]):
        job.update(status="stopped", finished_at=now_utc())
    else:
        return job
    db.execute("UPDATE build_jobs SET status = ?, exit_code = ?, finished_at = ? WHERE id = ?",
               (job["status"], job.get("exit_code"), job["finished_at"], job["id"]))
    return job


def build_status(db: Database, app: App, lines: int = 30) -> dict[str, Any]:
    """The app's latest build job: running, ok, failed or stopped, its last log lines and the .ipa it made."""
    job = db.one("SELECT * FROM build_jobs WHERE app_id = ? ORDER BY id DESC LIMIT 1", (app["id"],))
    if not job:
        return {"job": None, "status": "none", "message": "No build started yet (build_start)."}
    job = _refresh(db, job)
    out: dict[str, Any] = {"job": job, "status": job["status"], "log_tail": _tail(Path(job["log_path"]), lines)}
    if job["status"] == "ok":
        try:
            ipa = find_ipa(Path(job["cwd"]), load(Path(job["cwd"])))
        except RankwellError:
            ipa = None
        if ipa:
            out["ipa"] = str(ipa)
    return out


# ------------------------------------------------------------------ upload

def find_ipa(repo: Path, cfg: ReleaseConfig, explicit: str | None = None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        p = p if p.is_absolute() else repo / p
        if not p.is_file():
            raise RankwellError(f"No .ipa at {p}.")
        return p
    if not cfg.ipa:
        raise RankwellError("Set [build] ipa = \"<glob>\" in .aso/release.toml or pass the .ipa path.")
    pattern = os.path.expanduser(cfg.ipa)
    # Path.glob refuses absolute patterns; glob.glob takes both (the repo part is escaped, the pattern is not)
    full = pattern if os.path.isabs(pattern) else os.path.join(glob.escape(str(repo)), pattern)
    found = [Path(p) for p in glob.glob(full, recursive=True) if Path(p).is_file()]
    if not found:
        raise RankwellError(f"No .ipa matches {cfg.ipa} in {repo}. Build first (build_start).")
    return max(found, key=lambda p: p.stat().st_mtime)


def ipa_info(path: Path) -> dict[str, Any]:
    """Version, build number and bundle id from the .ipa's Payload/<App>.app/Info.plist."""
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)]
            if not names:
                raise RankwellError(f"{path.name} has no Payload/*.app/Info.plist; is it an iOS .ipa?")
            info = plistlib.loads(z.read(names[0]))
    except (zipfile.BadZipFile, plistlib.InvalidFileException) as e:
        raise RankwellError(f"{path.name} is not a readable .ipa ({e}).") from e
    st = path.stat()
    return {"path": str(path), "size": st.st_size, "mtime": int(st.st_mtime),
            "version": str(info.get("CFBundleShortVersionString", "")), "build": str(info.get("CFBundleVersion", "")),
            "bundle_id": str(info.get("CFBundleIdentifier", ""))}


def altool_command(ipa: Path, key: AscKey, p8: Path) -> list[str]:
    """`altool --upload-package` (the successor of the deprecated --upload-app); the key is passed as a file."""
    return ["xcrun", "altool", "--upload-package", str(ipa.resolve()),
            "--api-key", key.key_id, "--api-issuer", key.issuer_id, "--p8-file-path", str(p8)]


def _run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=3600, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, f"Could not run {cmd[0]}: {e}"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


runner: Runner = _run


def _scrub(text: str, key: AscKey) -> str:
    for line in key.private_key.splitlines():
        if len(line.strip()) > 8:
            text = text.replace(line.strip(), "<hidden>")
    return text


def upload_shell_command(template: str, ipa: Path) -> str:
    """The upload command with {ipa} replaced by the shell-quoted path. Quotes the user put around the
    placeholder ("{ipa}" or '{ipa}') are dropped first, so the path is quoted exactly once."""
    bare = re.sub(r"""(["'])\{ipa\}\1""", "{ipa}", template)
    return bare.replace("{ipa}", shlex.quote(str(ipa)))


def upload_altool(ipa: Path, key: AscKey, run: Runner | None = None) -> tuple[int, str]:
    """Upload with xcrun altool; the key is written to a private temp dir for the call and deleted after."""
    tmp = Path(tempfile.mkdtemp(prefix="rankwell-upload-"))
    try:
        tmp.chmod(0o700)
        keys = tmp / "private_keys"
        keys.mkdir(mode=0o700)
        p8 = keys / f"AuthKey_{key.key_id}.p8"
        fd = os.open(p8, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key.private_key)
        code, out = (run or runner)(altool_command(ipa, key, p8), tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return code, _scrub(out, key)


def _upload_state(asc: ASC | None, sid: int | None, path: Path, version: str) -> dict[str, Any]:
    st = path.stat() if path.exists() else None
    existing = sorted(b["build"] or "" for b in builds(asc, sid, version)) if asc and sid else []
    return {"ipa": str(path), "size": st.st_size if st else None, "mtime": int(st.st_mtime) if st else None,
            "builds": existing}


def plan_upload(db: Database, asc: ASC | None, app: App, ipa: str | None = None) -> plans.Plan:
    """A plan to upload the newest .ipa (or this one). asc may be None for fastlane/custom uploads."""
    repo = repo_of(app)
    cfg = load(repo)
    path = find_ipa(repo, cfg, ipa)
    info = ipa_info(path)
    if app.get("bundle_id") and info["bundle_id"] and info["bundle_id"] != app["bundle_id"]:
        raise RankwellError(f"{path.name} is {info['bundle_id']}, but {app['name']} is {app['bundle_id']}.")
    if cfg.upload_kind == "altool" and asc is None:
        raise RankwellError("Uploading with altool needs the App Store Connect key (rankwell init --asc-key ...).")
    sid = listing.asc_app_id(asc, app) if asc else None
    state = _upload_state(asc, sid, path, info["version"])
    if info["build"] in state["builds"]:
        raise RankwellError(f"Build {info['build']} of version {info['version']} is already in App Store Connect. "
                            "Raise the build number (CFBundleVersion, or the +N in pubspec.yaml) and build again.")
    how = "xcrun altool with your API key" if cfg.upload_kind == "altool" else f"`{cfg.upload_command}`"
    changes = [{"area": "Builds", "locale": "all", "field": "upload",
                "current": ", ".join(state["builds"][-3:]) or "(no build of this version)",
                "new": f"{info['version']} ({info['build']}) · {info['size'] / 1e6:.1f} MB · {path.name}",
                "target": {"kind": "upload"}}]
    notes = [f"Uploads {path} with {how}. It takes a few minutes; Apple then processes the build (often 5-30 "
             "minutes) before it can be attached (plan_attach_build)."]
    return plans.create(db, "upload", app["id"], changes, plans.state_hash(state),
                        payload={"ipa": str(path), "info": info, "asc_app_id": sid, "repo": str(repo),
                                 "upload_kind": cfg.upload_kind, "upload_command": cfg.upload_command}, notes=notes)


@plans.applier("upload")
def _apply_upload(db: Database, plan: plans.Plan) -> dict[str, Any]:
    path, info, sid = Path(plan["ipa"]), plan["info"], plan["asc_app_id"]
    asc = listing.connect() if sid or plan["upload_kind"] == "altool" else None
    plans.verify(plan, plans.state_hash(_upload_state(asc, sid, path, info["version"])))
    label = f"{info['version']} ({info['build']})"
    if plan["upload_kind"] == "altool":
        assert asc is not None
        code, out = upload_altool(path, asc.key)
    else:
        cmd = upload_shell_command(str(plan["upload_command"]), path)
        code, out = runner(["/bin/sh", "-c", cmd], Path(plan["repo"]))
        if asc:
            out = _scrub(out, asc.key)
    tail = "\n".join(out.strip().splitlines()[-25:])
    plans.audit(db, plan["id"], "upload", f"apps/{sid}" if sid else str(path), "build", None, label,
                "ok" if code == 0 else "error", None if code == 0 else tail[-500:])
    if code != 0:
        raise RankwellError(f"Upload failed (exit {code}):\n{tail}")
    return {"uploaded": label, "ipa": str(path), "output": tail,
            "next": "Apple is processing the build; then plan_attach_build."}


def wait_for_processing(asc: ASC, store_id: int, version: str, build: str, timeout: float = 1800,
                        interval: float = 30, sleep: Callable[[float], None] = time.sleep,
                        clock: Callable[[], float] = time.monotonic,
                        progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Poll until Apple finished processing an uploaded build (VALID, INVALID or FAILED)."""
    end = clock() + timeout
    while True:
        b = next((x for x in builds(asc, store_id, version) if x["build"] == build), None)
        state = b["state"] if b else "not visible yet"
        if progress:
            progress(f"{version} ({build}): {state}")
        if b and state != "PROCESSING":
            return b
        if clock() >= end:
            raise RankwellError(f"Build {build} is still {state} after {timeout / 60:.0f} minutes; check again later.")
        sleep(interval)


# ------------------------------------------------------------------ attach build

def _attach_state(asc: ASC, sid: int, build_id: str) -> dict[str, Any]:
    ev = listing.pick(listing.versions(asc, sid), ascmod.EDITABLE)
    cur = attached_build(asc, ev["id"]) if ev else None
    b = asc.get(f"/v1/builds/{build_id}", **{"fields[builds]": "version,processingState,expired"})["data"]
    return {"version": ev, "attached": cur["id"] if cur else None,
            "build": (b["id"], b["attributes"].get("processingState"), bool(b["attributes"].get("expired")))}


def plan_attach_build(db: Database, asc: ASC, app: App, build: str | None = None) -> plans.Plan:
    """A plan to attach the newest processed build of the version in preparation (or this build number)."""
    sid, ev = _editable(asc, app)
    all_builds = builds(asc, sid, ev["version"])
    valid = [b for b in all_builds if b["state"] == "VALID" and not b["expired"]]
    chosen = next((b for b in valid if b["build"] == str(build)), None) if build else (valid[0] if valid else None)
    if not chosen:
        seen = ", ".join(f"{b['build']} {b['state']}" for b in all_builds) or "none"
        raise RankwellError(f"No processed build {build or ''} for version {ev['version']} (builds: {seen}). "
                            "Upload one (plan_upload) and wait until it is VALID.")
    cur = attached_build(asc, ev["id"])
    if cur and cur["id"] == chosen["id"]:
        raise RankwellError(f"Build {chosen['build']} is already attached to version {ev['version']}.")
    notes = []
    if chosen["encryption"] is None:
        notes.append(f"Export compliance is not answered for build {chosen['build']}: set "
                     "ITSAppUsesNonExemptEncryption in Info.plist or answer it in App Store Connect before submitting.")
    changes = [{"area": f"Version {ev['version']}", "locale": "all", "field": "build",
                "current": cur["build"] if cur else "", "new": f"{chosen['build']} (uploaded {chosen['uploaded']})",
                "target": {"kind": "appStoreVersions", "id": ev["id"]}}]
    return plans.create(db, "attach_build", app["id"], changes, plans.state_hash(_attach_state(asc, sid, chosen["id"])),
                        payload={"asc_app_id": sid, "version_id": ev["id"], "build_id": chosen["id"],
                                 "build": chosen["build"]}, notes=notes)


@plans.applier("attach_build")
def _apply_attach(db: Database, plan: plans.Plan) -> dict[str, Any]:
    asc = listing.connect()
    plans.verify(plan, plans.state_hash(_attach_state(asc, plan["asc_app_id"], plan["build_id"])))
    vid, bid = plan["version_id"], plan["build_id"]
    asc.patch(f"/v1/appStoreVersions/{vid}/relationships/build", {"data": {"type": "builds", "id": bid}})
    c = plan["changes"][0]
    plans.audit(db, plan["id"], "attach_build", f"appStoreVersions/{vid}", "build", c["current"], plan["build"])
    return {"attached": plan["build"], "version_id": vid}


# ------------------------------------------------------------------ App Review info

REVIEW_FIELDS = {"first_name": "contactFirstName", "last_name": "contactLastName", "phone": "contactPhone",
                 "email": "contactEmail", "notes": "notes", "demo_account_required": "demoAccountRequired",
                 "demo_account_name": "demoAccountName", "demo_account_password": "demoAccountPassword"}
HIDDEN = "•••• (hidden)"


def plan_review_info(db: Database, asc: ASC, app: App, values: dict[str, Any]) -> plans.Plan:
    """A plan to set the App Review contact, notes and demo account of the version in preparation."""
    bad = sorted(set(values) - set(REVIEW_FIELDS))
    if bad:
        raise RankwellError(f"Unknown review field(s): {', '.join(bad)}. Use: {', '.join(REVIEW_FIELDS)}.")
    want = {k: v for k, v in values.items() if v is not None}
    if want.get("email") and "@" not in str(want["email"]):
        raise RankwellError(f"'{want['email']}' is not an email address.")
    sid, ev = _editable(asc, app)
    rid, cur = review_detail(asc, ev["id"])
    changes: list[dict[str, Any]] = []
    secret: dict[str, str] = {}
    for k, v in want.items():
        attr = REVIEW_FIELDS[k]
        if cur.get(attr) == v:
            continue
        if k == "demo_account_password":
            secret[attr] = str(v)
            changes.append({"area": "App Review", "locale": "all", "field": k, "current": HIDDEN if cur.get(attr)
                            else "", "new": HIDDEN, "target": {"kind": "appStoreReviewDetails", "id": rid}})
            continue
        changes.append({"area": "App Review", "locale": "all", "field": k, "current": cur.get(attr), "new": v,
                        "target": {"kind": "appStoreReviewDetails", "id": rid}})
    if not changes:
        raise RankwellError("Nothing to change: App Review info already has these values.")
    return plans.create(db, "review_info", app["id"], changes, plans.state_hash(_review_state(ev["id"], rid, cur)),
                        payload={"asc_app_id": sid, "version_id": ev["id"], "secret": secret})


def _review_state(version_id: str, detail_id: str | None, attrs: dict[str, Any]) -> dict[str, Any]:
    """The live state a review_info plan is checked against. Password fields are reduced to "is set": an
    unsalted hash of a secret must not be stored in the plan."""
    safe = {k: (bool(v) if "password" in k.lower() else v) for k, v in attrs.items()}
    return {"version": version_id, "detail": detail_id, "attrs": safe}


@plans.applier("review_info")
def _apply_review_info(db: Database, plan: plans.Plan) -> dict[str, Any]:
    try:
        asc = listing.connect()
        vid = plan["version_id"]
        rid, cur = review_detail(asc, vid)
        plans.verify(plan, plans.state_hash(_review_state(vid, rid, cur)))
        attrs = {REVIEW_FIELDS[c["field"]]: c["new"] for c in plan["changes"] if c["field"] != "demo_account_password"}
        attrs.update(plan.get("secret") or {})
        if rid:
            asc.patch(f"/v1/appStoreReviewDetails/{rid}",
                      {"data": {"type": "appStoreReviewDetails", "id": rid, "attributes": attrs}})
        else:
            created = asc.post("/v1/appStoreReviewDetails", {"data": {
                "type": "appStoreReviewDetails", "attributes": attrs,
                "relationships": {"appStoreVersion": {"data": {"type": "appStoreVersions", "id": vid}}}}})
            rid = str(created["data"]["id"])
        for c in plan["changes"]:
            plans.audit(db, plan["id"], "review_info", f"appStoreReviewDetails/{rid}", c["field"], c["current"],
                        c["new"])
        return {"saved": [c["field"] for c in plan["changes"]]}
    finally:  # the demo password is only kept until the plan is used
        db.execute("UPDATE plans SET payload = json_remove(payload, '$.secret') WHERE id = ?", (plan["id"],))


# ------------------------------------------------------------------ submit / resubmit

SUBMISSION_STATES = ("UNRESOLVED_ISSUES", "READY_FOR_REVIEW", "WAITING_FOR_REVIEW", "IN_REVIEW")


def _version_item(asc: ASC, submission_id: str, version_id: str) -> dict[str, Any] | None:
    # Items name their version only when it is asked for with include.
    for i in asc.get_all(f"/v1/reviewSubmissions/{submission_id}/items", include="appStoreVersion"):
        rel = ((i.get("relationships", {}).get("appStoreVersion") or {}).get("data") or {}).get("id")
        if rel == version_id:
            return {"id": i["id"], "state": i["attributes"].get("state"), "resolved": i["attributes"].get("resolved")}
    return None


def _submit_state(asc: ASC, sid: int, version_id: str) -> dict[str, Any]:
    subs = [s for state in SUBMISSION_STATES for s in asc.get_all("/v1/reviewSubmissions", **{
        "filter[app]": sid, "filter[platform]": "IOS", "filter[state]": state})]
    out = []
    for s in sorted(subs, key=lambda s: s["id"]):
        out.append({"id": s["id"], "state": s["attributes"].get("state"),
                    "item": _version_item(asc, s["id"], version_id)})
    ev = listing.pick(listing.versions(asc, sid), ascmod.EDITABLE)
    phased = phased_release(asc, version_id)
    return {"version": ev, "submissions": out, "phased": phased["id"] if phased else None}


def submit_steps(state: dict[str, Any], version_id: str, cfg: ReleaseConfig | None) -> tuple[list[dict[str, Any]],
                                                                                          list[str]]:
    """The writes needed to submit this version: resubmit in its UNRESOLVED_ISSUES submission when rejected,
    otherwise reuse an open (READY_FOR_REVIEW) submission or create one."""
    steps: list[dict[str, Any]] = []
    notes: list[str] = []
    ev = state["version"]
    if not ev or ev["id"] != version_id:
        raise RankwellError("The version in preparation changed; make a new plan.")
    busy = next((s for s in state["submissions"] if s["state"] in ("WAITING_FOR_REVIEW", "IN_REVIEW") and s["item"]),
                None)
    if busy:
        raise RankwellError(f"Version {ev['version']} is already submitted ({busy['state']}).")
    if cfg:
        want = RELEASE_TYPES[cfg.release_type]
        if ev.get("release_type") != want:
            steps.append({"step": "release_type", "field": "release type", "current": ev.get("release_type") or "",
                          "new": want})
        if cfg.phased and not state["phased"]:
            steps.append({"step": "phased_on", "field": "phased release", "current": "off", "new": "on (7 days)"})
        elif not cfg.phased and state["phased"]:
            notes.append("Phased release is on in App Store Connect but off in release.toml; Rankwell does not "
                         "delete it, turn it off in App Store Connect if you want.")
    rejected = next((s for s in state["submissions"] if s["state"] == "UNRESOLVED_ISSUES" and s["item"]), None)
    if rejected:
        item = rejected["item"]
        if item["state"] == "REJECTED" and not item.get("resolved"):
            steps.append({"step": "resolve", "field": "rejected item", "current": "REJECTED", "new": "resolved",
                          "submission": rejected["id"], "item": item["id"]})
        steps.append({"step": "submit", "field": "review submission", "current": "UNRESOLVED_ISSUES",
                      "new": "submitted (resubmission)", "submission": rejected["id"]})
        notes.append("Resubmits in the existing review submission after the rejection (keeps in-app purchases "
                     "that were reviewed with it).")
        return steps, notes
    ready = next((s for s in state["submissions"] if s["state"] == "READY_FOR_REVIEW"), None)
    if ready:
        if not ready["item"]:
            steps.append({"step": "add_item", "field": "submission item", "current": "",
                          "new": f"version {ev['version']}", "submission": ready["id"]})
        steps.append({"step": "submit", "field": "review submission", "current": "READY_FOR_REVIEW",
                      "new": "submitted", "submission": ready["id"]})
    else:
        steps += [{"step": "create", "field": "review submission", "current": "", "new": "new (iOS)"},
                  {"step": "add_item", "field": "submission item", "current": "", "new": f"version {ev['version']}"},
                  {"step": "submit", "field": "review submission", "current": "READY_FOR_REVIEW", "new": "submitted"}]
    return steps, notes


def _config_or_none(app: App) -> ReleaseConfig | None:
    if not app.get("repo_path"):
        return None
    try:
        return load(repo_of(app))
    except RankwellError:
        return None


def plan_submit(db: Database, asc: ASC, app: App, force: bool = False) -> plans.Plan:
    """A plan to submit the version in preparation for App Review (or resubmit it after a rejection)."""
    sid, ev = _editable(asc, app)
    chk = check(asc, app)
    blockers = [i["item"] for i in chk["items"] if i["ok"] is False]
    if blockers and not force:
        raise RankwellError("Not ready to submit:\n- " + "\n- ".join(blockers)
                            + "\nFix these and plan again (release_check shows the full list).")
    cfg = _config_or_none(app)
    state = _submit_state(asc, sid, ev["id"])
    steps, notes = submit_steps(state, ev["id"], cfg)
    if blockers:
        notes.append("Submitting despite: " + "; ".join(blockers))
    changes = [{"area": f"Version {ev['version']}", "locale": "all", **s, "target": {"kind": s["step"]}}
               for s in steps]
    return plans.create(db, "submit", app["id"], changes, plans.state_hash(state),
                        payload={"asc_app_id": sid, "version_id": ev["id"], "version": ev["version"],
                                 "resubmit": any(s["step"] == "resolve" or "resubmission" in s["new"] for s in steps)},
                        notes=notes)


@plans.applier("submit")
def _apply_submit(db: Database, plan: plans.Plan) -> dict[str, Any]:
    asc = listing.connect()
    sid, vid = plan["asc_app_id"], plan["version_id"]
    plans.verify(plan, plans.state_hash(_submit_state(asc, sid, vid)))
    submission: str | None = None
    for c in plan["changes"]:
        step = c["step"]
        submission = c.get("submission") or submission
        target = f"reviewSubmissions/{submission}" if submission else f"appStoreVersions/{vid}"
        if step == "release_type":
            asc.patch(f"/v1/appStoreVersions/{vid}", {"data": {
                "type": "appStoreVersions", "id": vid, "attributes": {"releaseType": c["new"]}}})
            target = f"appStoreVersions/{vid}"
        elif step == "phased_on":
            asc.post("/v1/appStoreVersionPhasedReleases", {"data": {
                "type": "appStoreVersionPhasedReleases", "attributes": {"phasedReleaseState": "INACTIVE"},
                "relationships": {"appStoreVersion": {"data": {"type": "appStoreVersions", "id": vid}}}}})
            target = f"appStoreVersions/{vid}"
        elif step == "create":
            submission = str(asc.post("/v1/reviewSubmissions", {"data": {
                "type": "reviewSubmissions", "attributes": {"platform": "IOS"},
                "relationships": {"app": {"data": {"type": "apps", "id": str(sid)}}}}})["data"]["id"])
            target = f"reviewSubmissions/{submission}"
        elif step == "add_item":
            asc.post("/v1/reviewSubmissionItems", {"data": {
                "type": "reviewSubmissionItems",
                "relationships": {"reviewSubmission": {"data": {"type": "reviewSubmissions", "id": submission}},
                                  "appStoreVersion": {"data": {"type": "appStoreVersions", "id": vid}}}}})
        elif step == "resolve":
            # Without this Apple answers 409 "not ready to be submitted yet", however long you wait.
            asc.patch(f"/v1/reviewSubmissionItems/{c['item']}", {"data": {
                "type": "reviewSubmissionItems", "id": c["item"], "attributes": {"resolved": True}}})
            target = f"reviewSubmissionItems/{c['item']}"
        elif step == "submit":
            asc.patch(f"/v1/reviewSubmissions/{submission}", {"data": {
                "type": "reviewSubmissions", "id": submission, "attributes": {"submitted": True}}})
        plans.audit(db, plan["id"], "submit", target, c["field"], c["current"], c["new"])
    if plan.get("resubmit"):
        db.execute("UPDATE rejections SET resolved_at = ? WHERE app_id = ? AND resolved_at IS NULL",
                   (now_utc(), plan["app_id"]))
    return {"submitted": plan["version"], "review_submission": submission, "resubmit": bool(plan.get("resubmit"))}

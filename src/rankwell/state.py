"""The .aso/ folder in an app repo: context, decisions, progress and experiments, as Markdown people can edit.

Files are read leniently because users edit them by hand: dated headings, bullets and `Key: value` lines
are recognised in several spellings, and writes only touch the lines they need.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from .errors import RankwellError
from .util import today as _today

FOLDER = ".aso"
NOTE_KINDS = ("decision", "progress", "todo", "done")
VERDICTS = ("confirmed", "failed", "inconclusive")
DEFAULT_CHECK_DAYS = 14

DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
HEADING_RE = re.compile(r"^#{2,4}\s+(.*?)\s*#*\s*$")
BULLET_RE = re.compile(r"^\s{0,3}[-*+]\s+(.*)$")
CHECK_RE = re.compile(r"^\[( |x|X)\]\s*(.*)$")
FIELD_RE = re.compile(r"^\s{0,3}(?:[-*+]\s+)?(?:\*\*)?([A-Za-z][A-Za-z /'-]{0,40}?)(?::\*\*|\*\*:|:)\s*(.*)$")
EXP_ID_RE = re.compile(r"^([A-Za-z]{1,8}-?\d{1,5})\s*(?:[:—–-]\s*(.*))?$")

FIELD_ALIASES = {
    "status": "status", "state": "status",
    "opened": "opened", "date": "opened", "started": "opened", "start": "opened",
    "hypothesis": "hypothesis", "hypothesis / insight tested": "hypothesis", "insight tested": "hypothesis",
    "change": "change", "changes": "change", "what changed": "change",
    "metric": "metric", "metrics": "metric", "measure": "metric",
    "baseline": "baseline", "before": "baseline",
    "check after": "check_after", "check-after": "check_after", "check": "check_after", "due": "check_after",
    "check on": "check_after", "review on": "check_after",
    "result": "result", "results": "result", "after": "result", "outcome": "result",
    "verdict": "verdict", "closed": "closed", "closed on": "closed",
}
FIELD_LABELS = {
    "status": "Status", "opened": "Opened", "hypothesis": "Hypothesis", "change": "Change", "metric": "Metric",
    "baseline": "Baseline", "check_after": "Check after", "result": "Result", "verdict": "Verdict",
    "closed": "Closed",
}

Experiment = dict[str, Any]


# Paths and templates -------------------------------------------------------------------------------------------


def repo_for(app: dict[str, Any]) -> Path:
    """The app's repo folder, or a clear error when it is not set or missing."""
    raw = app.get("repo_path")
    if not raw:
        raise RankwellError(
            f"No repo folder is set for {app.get('name', 'this app')}. Set it with "
            "aso_init(app, repo_path='/path/to/app/repo') or update_app(app, repo_path=...)."
        )
    p = Path(str(raw)).expanduser()
    if not p.is_dir():
        raise RankwellError(f"The repo folder for {app.get('name', 'this app')} does not exist: {p}. "
                            "Update it with update_app(app, repo_path=...).")
    return p


def folder(repo: Path | str) -> Path:
    return Path(repo).expanduser() / FOLDER


def _app_md(app: dict[str, Any]) -> str:
    name = app.get("name") or "My app"
    store = f"https://apps.apple.com/app/id{app['store_id']}" if app.get("store_id") else "not released yet"
    countries = ", ".join(app.get("countries") or ["us"])
    return f"""\
# {name}

<!-- Rankwell reads this file before ASO work. Keep it short and true; edit it whenever things change. -->

## What it is
One or two sentences: what the app does and the main problem it solves.

## Who it is for
The people who search for it, in their own words.

## Market
- App Store: {store}
- Bundle id: {app.get("bundle_id") or "-"}
- Countries: {countries}
- Main competitors: (filled in as you add them)

## Positioning
Why someone picks this app over the others. The one thing it does best.

## Features that ship today
- (only list what is in the current build; the listing must not promise more)

## Do not claim
- (features that are not built, numbers you cannot back up, words you do not want used)
"""


DECISIONS_MD = """\
# Decisions

What was decided about the listing and the app, and why. Newest first.
Rankwell adds entries when you decide something; edit or add your own freely.
"""

PROGRESS_MD = """\
# Progress

Open items (`- [ ]`) and what was done, newest first. Rankwell adds entries as work happens.
"""

EXPERIMENTS_MD = """\
# Experiments

One change at a time, with a baseline and a date to check it. Newest first.
Verdict: confirmed (the change helped), failed (it did not help, or hurt), inconclusive (not enough signal).

<!-- Entry format (Rankwell writes and reads this; hand edits are fine):
## EXP-001: Short title
- Status: open
- Opened: 2026-01-01
- Hypothesis: If <change>, then <metric> improves because <reason>.
- Change: before -> after
- Metric: what to compare (e.g. rank for "habit tracker" in us, conversion rate)
- Baseline: the value before the change, with its date range
- Check after: 2026-01-15
- Result:
- Verdict:
-->
"""

RELEASE_TOML = """\
# How Rankwell builds and ships this app. Uncomment and adjust what you need.

# [build]
# One of: "flutter", "xcode", "fastlane", "custom"
# kind = "flutter"
# command = "flutter build ipa --release --export-options-plist=ios/ExportOptions.plist"
# ipa = "build/ios/ipa/*.ipa"

# [upload]
# "altool" (xcrun altool with the App Store Connect key), "fastlane", or "custom"
# kind = "altool"

# [release]
# type = "after-approval"   # or "manual"
# phased = false
"""


def init(repo: Path | str, app: dict[str, Any]) -> dict[str, list[str]]:
    """Create .aso/ with templates. Existing files are never overwritten."""
    root = Path(repo).expanduser()
    if not root.is_dir():
        raise RankwellError(f"Not a folder: {root}")
    d = folder(root)
    d.mkdir(exist_ok=True)
    files = {"app.md": _app_md(app), "decisions.md": DECISIONS_MD, "progress.md": PROGRESS_MD,
             "experiments.md": EXPERIMENTS_MD, "release.toml": RELEASE_TOML}
    created, kept = [], []
    for name, text in files.items():
        p = d / name
        if p.exists():
            kept.append(name)
        else:
            p.write_text(text)
            created.append(name)
    return {"created": created, "kept": kept, "folder": [str(d)]}


def _ensure(repo: Path | str, name: str, template: str) -> Path:
    p = folder(repo) / name
    if not p.exists():
        if not Path(repo).expanduser().is_dir():
            raise RankwellError(f"Not a folder: {repo}")
        p.parent.mkdir(exist_ok=True)
        p.write_text(template)
    return p


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# Reading -------------------------------------------------------------------------------------------------------


def read(repo: Path | str) -> dict[str, Any]:
    """Everything in .aso/: raw text per file plus parsed decisions, progress, open items and experiments."""
    d = folder(repo)
    out: dict[str, Any] = {"folder": str(d), "exists": d.is_dir(), "files": {}}
    if not d.is_dir():
        return {**out, "decisions": [], "progress": [], "open_items": [], "experiments": []}
    for p in sorted(d.iterdir()):
        if p.is_file() and p.suffix in (".md", ".toml"):
            out["files"][p.name] = _read_text(p)
    files = out["files"]
    progress = parse_notes(files.get("progress.md", ""))
    out["decisions"] = parse_notes(files.get("decisions.md", ""))
    out["progress"] = progress
    out["open_items"] = [e["text"] for e in progress if e.get("open")]
    out["experiments"] = parse_experiments(files.get("experiments.md", ""))
    return out


def _strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)


def parse_notes(text: str) -> list[dict[str, Any]]:
    """Dated entries from decisions.md / progress.md, newest first as written.

    Dates come from the `## <date>` heading above a bullet, or from a date at the start of the bullet.
    Checkbox bullets are open (`[ ]`) or done (`[x]`) items.
    """
    entries: list[dict[str, Any]] = []
    date: str | None = None
    current: dict[str, Any] | None = None
    for line in _strip_comments(text).splitlines():
        h = HEADING_RE.match(line)
        if h or line.startswith("# "):
            m = DATE_RE.search(h.group(1)) if h else None
            date = m.group(1) if m else None
            current = None
            continue
        b = BULLET_RE.match(line)
        if b and not line.startswith("    "):
            body = b.group(1).strip()
            entry: dict[str, Any] = {"date": date, "text": body}
            inline = re.match(r"^\**(\d{4}-\d{2}-\d{2})\**\s*[:—–-]?\s*(.*)$", body)
            if inline:
                entry["date"], entry["text"] = inline.group(1), inline.group(2)
            c = CHECK_RE.match(entry["text"])
            if c:
                entry["open"] = c.group(1) == " "
                entry["text"] = c.group(2).strip()
            entries.append(entry)
            current = entry
        elif current is not None and line.strip() and line.startswith((" ", "\t")):
            current["text"] += "\n" + line.strip()
        elif not line.strip():
            current = None
        elif date and line.strip():
            entry = {"date": date, "text": line.strip()}
            entries.append(entry)
            current = entry
    return entries


# Notes ---------------------------------------------------------------------------------------------------------


def _bullet(text: str, checkbox: bool = False) -> list[str]:
    lines = [ln.rstrip() for ln in text.strip().splitlines() if ln.strip()] or [""]
    first = ("[ ] " if checkbox else "") + lines[0]
    return [f"- {first}"] + [f"  {ln.strip()}" for ln in lines[1:]]


def _insert_dated(text: str, day: str, bullet: list[str]) -> str:
    """Insert a bullet under the day's heading (newest first), creating the heading in date order."""
    lines = text.splitlines()
    in_comment = False
    for i, line in enumerate(lines):
        if "<!--" in line and "-->" not in line:
            in_comment = True
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        h = HEADING_RE.match(line)
        if not h:
            continue
        m = DATE_RE.search(h.group(1))
        if not m:
            continue
        if m.group(1) > day:
            continue
        if m.group(1) == day:
            new = [*lines[: i + 1], *bullet, *lines[i + 1:]]
        else:
            new = [*lines[:i], f"## {day}", *bullet, "", *lines[i:]]
        return "\n".join(new) + "\n"
    body = text.rstrip("\n")
    return (body + "\n\n" if body else "") + "\n".join([f"## {day}", *bullet]) + "\n"


def note(repo: Path | str, kind: str, text: str, today: str | None = None) -> dict[str, Any]:
    """Add a dated entry: decision → decisions.md; progress, todo (open item) and done → progress.md."""
    kind = kind.strip().lower()
    if kind not in NOTE_KINDS:
        raise RankwellError(f"Unknown note kind '{kind}'. Use one of: {', '.join(NOTE_KINDS)}.")
    if not text.strip():
        raise RankwellError("The note is empty.")
    day = today or _today()
    if kind == "decision":
        p = _ensure(repo, "decisions.md", DECISIONS_MD)
        p.write_text(_insert_dated(_read_text(p), day, _bullet(text)))
        return {"file": str(p), "kind": kind, "date": day}
    p = _ensure(repo, "progress.md", PROGRESS_MD)
    body = _read_text(p)
    closed = None
    if kind == "done":
        body, closed = _close_item(body, text)
        lines = _bullet(f"Done: {closed}" if closed else text)
        if not closed:
            lines[0] = "- [x] " + lines[0][2:]
        body = _insert_dated(body, day, lines)
    else:
        body = _insert_dated(body, day, _bullet(text, checkbox=kind == "todo"))
    p.write_text(body)
    out: dict[str, Any] = {"file": str(p), "kind": kind, "date": day}
    if kind == "done":
        out["closed_item"] = closed
    return out


def _close_item(text: str, query: str) -> tuple[str, str | None]:
    """Tick the first open item that matches query (case-insensitive substring either way)."""
    q = query.strip().lower()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\s{0,3}[-*+]\s+)\[ \]\s*(.*)$", line)
        if m and (q in m.group(2).lower() or m.group(2).strip().lower() in q):
            lines[i] = f"{m.group(1)}[x] {m.group(2)}"
            return "\n".join(lines) + "\n", m.group(2).strip()
    return text, None


# Experiments ---------------------------------------------------------------------------------------------------


def _field_key(raw: str) -> str | None:
    k = re.sub(r"\s+", " ", raw.replace("*", "").strip().lower())
    return FIELD_ALIASES.get(k)


def _date_in(value: str | None) -> str | None:
    m = DATE_RE.search(value or "")
    return m.group(1) if m else None


def _blocks(text: str) -> list[tuple[int, int]]:
    """(start, end) line spans of each `## ` entry outside HTML comments."""
    lines = text.splitlines()
    starts, in_comment = [], False
    for i, line in enumerate(lines):
        if "<!--" in line:
            in_comment = "-->" not in line.split("<!--", 1)[1]
            continue
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if line.startswith("## "):
            starts.append(i)
    spans = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(lines)
        while e > s + 1 and (not lines[e - 1].strip() or lines[e - 1].lstrip().startswith(("<!--", "-->"))):
            e -= 1
        spans.append((s, e))
    return spans


def parse_experiments(text: str) -> list[Experiment]:
    """Experiments from experiments.md. Unknown `Key: value` lines are kept under `extra`."""
    lines = text.splitlines()
    out: list[Experiment] = []
    for n, (s, e) in enumerate(_blocks(text)):
        heading = lines[s][3:].strip()
        m = EXP_ID_RE.match(heading)
        exp: Experiment = {"id": m.group(1).upper() if m else None, "title": (m.group(2) or "").strip() if m
                           else heading, "extra": {}, "notes": []}
        if exp["id"] is None:
            exp["id"] = f"ENTRY-{n + 1}"
        last: str | None = None
        for line in lines[s + 1: e]:
            f = FIELD_RE.match(line)
            key = _field_key(f.group(1)) if f else None
            if f and key:
                exp[key] = f.group(2).strip().strip("*").strip()
                last = key
            elif f and BULLET_RE.match(line):
                exp["extra"][f.group(1).strip().strip("*")] = f.group(2).strip()
                last = None
            elif line.strip() and line.startswith(("  ", "\t")) and last:
                exp[last] = (exp[last] + " " + line.strip()).strip()
            elif line.strip():
                exp["notes"].append(line.strip())
        if not exp.get("opened"):
            exp["opened"] = _date_in(heading)
        else:
            exp["opened"] = _date_in(exp["opened"]) or exp["opened"]
        exp["check_after"] = _date_in(exp.get("check_after")) or exp.get("check_after") or None
        verdict = (exp.get("verdict") or "").strip().lower()
        exp["verdict"] = next((v for v in VERDICTS if verdict.startswith(v)), verdict or None)
        status = (exp.get("status") or "").strip().lower()
        if status.startswith(("closed", "done", "finished")):
            exp["status"] = "closed"
        elif status.startswith(("open", "running", "active")):
            exp["status"] = "open"
        else:
            exp["status"] = "closed" if exp["verdict"] else "open"
        out.append(exp)
    return out


def experiments(repo: Path | str) -> list[Experiment]:
    return parse_experiments(_read_text(folder(repo) / "experiments.md"))


def _next_id(existing: list[Experiment]) -> str:
    nums = [int(m.group(1)) for e in existing if (m := re.search(r"(\d+)$", str(e["id"])))]
    return f"EXP-{(max(nums) + 1 if nums else 1):03d}"


def open_experiment(repo: Path | str, hypothesis: str, change: str, metric: str, baseline: str,
                    title: str | None = None, check_after: str | None = None,
                    today: str | None = None) -> Experiment:
    """Add an open experiment at the top of experiments.md and a progress line. Returns the parsed entry."""
    day = today or _today()
    if check_after:
        try:
            dt.date.fromisoformat(check_after)
        except ValueError as e:
            raise RankwellError(f"check_after must be a date like 2026-01-31, not '{check_after}'.") from e
    else:
        check_after = (dt.date.fromisoformat(day) + dt.timedelta(days=DEFAULT_CHECK_DAYS)).isoformat()
    if check_after <= day:
        raise RankwellError("check_after must be after today: give the change time to show an effect.")
    for name, value in (("hypothesis", hypothesis), ("change", change), ("metric", metric),
                        ("baseline", baseline)):
        if not value.strip():
            raise RankwellError(f"An experiment needs a {name}.")
    p = _ensure(repo, "experiments.md", EXPERIMENTS_MD)
    text = _read_text(p)
    eid = _next_id(parse_experiments(text))
    head = title.strip() if title and title.strip() else change.strip().splitlines()[0][:60]
    block = [f"## {eid}: {head}"] + [f"- {FIELD_LABELS[k]}: {_one_line(v)}" for k, v in (
        ("status", "open"), ("opened", day), ("hypothesis", hypothesis), ("change", change), ("metric", metric),
        ("baseline", baseline), ("check_after", check_after), ("result", ""), ("verdict", ""))]
    block = [ln.rstrip() for ln in block]
    lines = text.splitlines()
    spans = _blocks(text)
    if spans:
        at = spans[0][0]
        new = [*lines[:at], *block, "", *lines[at:]]
    else:
        new = [*lines, "", *block] if lines else block
    p.write_text("\n".join(new).rstrip("\n") + "\n")
    note(repo, "progress", f"Opened {eid}: {head} (check after {check_after})", today=day)
    return next(e for e in experiments(repo) if e["id"] == eid)


def _one_line(value: str) -> str:
    return " ".join(value.split())


def close_experiment(repo: Path | str, experiment_id: str, result: str, verdict: str,
                     today: str | None = None) -> Experiment:
    """Record the result and verdict of an experiment, in place, keeping any hand-written lines."""
    v = verdict.strip().lower()
    if v not in VERDICTS:
        raise RankwellError(f"Verdict must be one of: {', '.join(VERDICTS)}.")
    if not result.strip():
        raise RankwellError("Write the result: the metric after the change, next to the baseline.")
    day = today or _today()
    p = folder(repo) / "experiments.md"
    text = _read_text(p)
    want = experiment_id.strip().upper()
    found = parse_experiments(text)
    spans = _blocks(text)
    idx = next((i for i, e in enumerate(found) if str(e["id"]).upper() == want), None)
    if idx is None:
        ids = ", ".join(str(e["id"]) for e in found) or "none"
        raise RankwellError(f"No experiment '{experiment_id}' in {p}. Experiments: {ids}.")
    s, e = spans[idx]
    lines = text.splitlines()
    block = lines[s:e]
    updates = {"status": "closed", "result": _one_line(result), "verdict": v, "closed": day}
    for key, value in updates.items():
        block = _set_field(block, key, value)
    new = [*lines[:s], *block, *lines[e:]]
    p.write_text("\n".join(new).rstrip("\n") + "\n")
    title = found[idx]["title"] or want
    note(repo, "progress", f"Closed {want}: {title} - {v}. {_one_line(result)}", today=day)
    return next(x for x in experiments(repo) if str(x["id"]).upper() == want)


def _set_field(block: list[str], key: str, value: str) -> list[str]:
    last_field = 0
    for i, line in enumerate(block[1:], start=1):
        f = FIELD_RE.match(line)
        if not f:
            continue
        last_field = i
        if _field_key(f.group(1)) == key:
            prefix = line[: f.start(2)].rstrip() + " "
            block[i] = (prefix + value).rstrip()
            j = i + 1  # drop continuation lines of the old value
            while j < len(block) and block[j].startswith(("  ", "\t")) and not FIELD_RE.match(block[j]):
                del block[j]
            return block
    line = f"- {FIELD_LABELS[key]}: {value}".rstrip()
    at = last_field + 1 if last_field else len(block)
    while at < len(block) and block[at].startswith(("  ", "\t")) and block[at].strip() \
            and not FIELD_RE.match(block[at]):
        at += 1
    return [*block[:at], line, *block[at:]]


def due(repo: Path | str, today: str | None = None) -> list[Experiment]:
    """Open experiments whose check-after date has come."""
    day = today or _today()
    out = []
    for e in experiments(repo):
        ca = e.get("check_after")
        if e["status"] == "open" and ca and DATE_RE.fullmatch(str(ca)) and ca <= day:
            out.append({**e, "days_overdue": (dt.date.fromisoformat(day) - dt.date.fromisoformat(ca)).days})
    return out

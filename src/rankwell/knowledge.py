"""ASO rules: the ones shipped in the plugin's knowledge/ folder plus the user's own notes.

Shipped rules are `## <id>: <title>` sections. User folders (config `[knowledge] dirs`) can use any Markdown:
each `##` or `###` heading with text under it is a rule; an `id:` prefix in the heading keeps its id stable,
and a user rule with the same id as a shipped one replaces it. User rules always come first.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import config
from .util import slugify

SKIP_FILES = {"index.md", "readme.md", "changelog.md", "inbox.md"}
ID_RE = re.compile(r"^([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\s*:\s*(.+)$")
HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*#*\s*$")
TOPIC_ALIASES = {
    "keywords": ("keyword", "kw", "search", "keyword-research"),
    "metadata": ("meta", "listing", "title", "subtitle", "description", "copy"),
    "screenshots": ("screenshot", "shot", "shots", "preview", "previews", "creative-assets", "creatives"),
    "ratings-reviews": ("ratings", "reviews", "rating", "rr", "rating-prompt", "ratings-and-reviews"),
    "conversion": ("cvr", "icon", "product-page", "product-page-testing", "cpp", "ppo", "cro"),
    "review-guidelines": ("rev", "app-review", "app-review-rules", "guidelines", "rejection", "rejections"),
    "release": ("rel", "ship", "shipping", "releases", "submit"),
    "localization": ("loc", "l10n", "localisation", "languages", "markets"),
    "experiments": ("exp", "experiment", "testing", "ab-testing"),
}
PRECEDENCE = ("User rules (origin 'user') come first. When a user rule and a shipped rule disagree, follow the "
              "user rule and tell the user you did so, naming both rule ids.")


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    body: str
    topic: str
    origin: str  # shipped | user
    file: str


def shipped_dir() -> Path:
    """The plugin's knowledge/ folder (RANKWELL_KNOWLEDGE_DIR overrides, for tests and packaging)."""
    env = os.environ.get("RANKWELL_KNOWLEDGE_DIR")
    if env:
        return Path(env).expanduser()
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root and (Path(root) / "knowledge").is_dir():
        return Path(root) / "knowledge"
    return Path(__file__).resolve().parents[2] / "knowledge"


def topic_of(name: str) -> str:
    """The canonical topic for a file stem or a topic the user typed ('icon' → conversion)."""
    n = slugify(name)
    for topic, aliases in TOPIC_ALIASES.items():
        if n == topic or n in aliases:
            return topic
    return n


def parse(text: str, topic: str, origin: str, file: str) -> list[Rule]:
    """Rules in one Markdown file. Headings without text under them (section titles) are skipped."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    lines = text.splitlines()
    heads = [(i, m) for i, line in enumerate(lines) if (m := HEADING_RE.match(line))]
    out: list[Rule] = []
    stem = Path(file).stem
    for n, (i, m) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        body = "\n".join(lines[i + 1: end]).strip()
        if not body:
            continue
        heading = m.group(2).strip()
        explicit = ID_RE.match(heading)
        if explicit:
            rid, title = explicit.group(1), explicit.group(2).strip()
        else:
            rid, title = f"my-{slugify(stem)}-{slugify(heading)[:48].strip('-')}", heading
        out.append(Rule(rid, title, body, topic, origin, file))
    return out


def load_dir(folder: Path, origin: str) -> list[Rule]:
    if not folder.is_dir():
        return []
    rules: list[Rule] = []
    for p in sorted(folder.rglob("*.md")):
        if p.name.lower() in SKIP_FILES or any(part.startswith(".") for part in p.relative_to(folder).parts):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rules += parse(text, topic_of(p.stem), origin, str(p))
    return rules


def all_rules(user_dirs: list[Path] | None = None) -> tuple[list[Rule], list[dict[str, str]]]:
    """User rules first, then shipped ones. A shipped rule whose id a user rule reuses is dropped (reported)."""
    dirs = config.load().knowledge_dirs if user_dirs is None else user_dirs
    user = [r for d in dirs for r in load_dir(d, "user")]
    shipped = load_dir(shipped_dir(), "shipped")
    seen: dict[str, Rule] = {}
    overridden: list[dict[str, str]] = []
    for r in user + shipped:
        if r.id in seen:
            if r.origin == "shipped" and seen[r.id].origin == "user":
                overridden.append({"id": r.id, "shipped_title": r.title, "user_file": seen[r.id].file})
            continue
        seen[r.id] = r
    return list(seen.values()), overridden


def rules(topic: str | None = None, ids: list[str] | None = None,
          user_dirs: list[Path] | None = None) -> dict[str, Any]:
    """Rules for a topic, rules by id, or (neither) an index of topics with their rule ids."""
    found, overridden = all_rules(user_dirs)
    if ids:
        want = [i.strip() for i in ids]
        by_id = {r.id: r for r in found}
        picked = [by_id[i] for i in want if i in by_id]
        missing = [i for i in want if i not in by_id]
        return {"rules": [asdict(r) for r in picked], "missing": missing, "precedence": PRECEDENCE,
                "overridden": [o for o in overridden if o["id"] in want]}
    if topic:
        t = topic_of(topic)
        picked = [r for r in found if r.topic == t]
        if not picked:
            q = topic.lower()
            picked = [r for r in found if q in r.title.lower() or q in r.id]
        picked.sort(key=lambda r: r.origin != "user")
        return {"topic": t, "rules": [asdict(r) for r in picked], "precedence": PRECEDENCE,
                "overridden": [o for o in overridden if any(o["id"] == r.id for r in picked)]}
    topics: dict[str, dict[str, Any]] = {}
    for r in found:
        entry = topics.setdefault(r.topic, {"topic": r.topic, "shipped": 0, "user": 0, "ids": []})
        entry[r.origin] += 1
        entry["ids"].append(r.id)
    return {"topics": sorted(topics.values(), key=lambda e: str(e["topic"])), "precedence": PRECEDENCE,
            "overridden": overridden, "shipped_dir": str(shipped_dir())}

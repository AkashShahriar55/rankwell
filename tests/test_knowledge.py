import asyncio
import re
from pathlib import Path

import pytest

from rankwell import knowledge
from rankwell.mcp import server

ROOT = Path(__file__).resolve().parents[1]
SHIPPED_TOPICS = {"keywords", "metadata", "screenshots", "ratings-reviews", "conversion", "review-guidelines",
                  "release", "localization", "experiments"}
TOOLS = {t.name for t in asyncio.run(server.build().list_tools())}
# Skills every release must ship; new ones are fine.
SKILLS = {"aso-setup", "aso-today", "aso-gap", "aso-keywords", "aso-listing", "aso-stats", "aso-code-audit",
          "aso-ship", "aso-review-fix", "aso-experiment", "aso-reviews", "aso-play", "aso-screenshots"}


@pytest.fixture(autouse=True)
def no_user_dirs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RANKWELL_KNOWLEDGE_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)


def shipped_ids() -> set[str]:
    return {r.id for r in knowledge.load_dir(knowledge.shipped_dir(), "shipped")}


def test_shipped_rules_load() -> None:
    rules = knowledge.load_dir(knowledge.shipped_dir(), "shipped")
    assert {r.topic for r in rules} == SHIPPED_TOPICS
    ids = [r.id for r in rules]
    assert len(ids) == len(set(ids)), "rule ids must be unique"
    assert "kw-title-weight" in ids
    for r in rules:
        assert re.fullmatch(r"[a-z]+(-[a-z0-9]+)+", r.id)
        assert "Source:" in r.body, r.id


def test_topic_aliases_and_ids() -> None:
    r = knowledge.rules("icon", user_dirs=[])
    assert r["topic"] == "conversion" and any(x["id"] == "cvr-icon" for x in r["rules"])
    r = knowledge.rules(ids=["kw-field-format", "nope"], user_dirs=[])
    assert [x["id"] for x in r["rules"]] == ["kw-field-format"] and r["missing"] == ["nope"]
    idx = knowledge.rules(user_dirs=[])
    assert {t["topic"] for t in idx["topics"]} == SHIPPED_TOPICS


def test_user_rules_come_first_and_override(tmp_path: Path) -> None:
    (tmp_path / "INDEX.md").write_text("## Not a rule\nindex text\n")
    (tmp_path / "keywords.md").write_text(
        "# Keywords\n\n### Long tail first\n- **Insight:** start with long terms\n\n"
        "## kw-title-weight: My title rule\nThe keyword goes first, always.\n\n## Section only\n\n### Empty\n")
    (tmp_path / "icon.md").write_text("### Name in the icon\nPut the wordmark in the icon.\n")
    r = knowledge.rules("keywords", user_dirs=[tmp_path])
    first = r["rules"][0]
    assert first["origin"] == "user" and first["id"] == "my-keywords-long-tail-first"
    title_rules = [x for x in r["rules"] if x["id"] == "kw-title-weight"]
    assert len(title_rules) == 1 and title_rules[0]["origin"] == "user"
    assert r["overridden"][0]["id"] == "kw-title-weight"
    assert "user rule" in r["precedence"]
    assert not any(x["title"] in ("Not a rule", "Section only", "Empty") for x in r["rules"])
    icon = knowledge.rules("conversion", user_dirs=[tmp_path])["rules"]
    assert icon[0]["origin"] == "user" and icon[0]["topic"] == "conversion"


def test_user_dirs_from_config(tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "release.md").write_text("## rel-mine: Ship on Tuesdays\nFewer reviewers on weekends.\n")
    from rankwell import paths

    paths.config_path().write_text(f'[knowledge]\ndirs = ["{notes}"]\n')
    r = knowledge.rules("release")
    assert r["rules"][0]["id"] == "rel-mine"


def test_cited_rule_ids_exist() -> None:
    prefix = r"(?:kw|meta|shot|rr|cvr|rev|rel|loc|exp)"
    cited = set(re.findall(rf'"({prefix}-[a-z0-9-]+)"', (ROOT / "src/rankwell/code_audit.py").read_text()))
    for p in (ROOT / "skills").glob("*/SKILL.md"):
        cited |= set(re.findall(rf"(?<![\w-])({prefix}-[a-z0-9]+(?:-[a-z0-9]+)*)", p.read_text()))
    assert len(cited) > 30
    assert cited <= shipped_ids(), sorted(cited - shipped_ids())


def test_skills_frontmatter_and_tool_names() -> None:
    found = {}
    for p in (ROOT / "skills").glob("*/SKILL.md"):
        text = p.read_text()
        m = re.match(r"---\nname: ([a-z-]+)\ndescription: (.+?)\n---\n", text, re.S)
        assert m, p
        assert m.group(1) == p.parent.name
        assert len(m.group(2)) > 80 and "Use when" in m.group(2)
        found[m.group(1)] = text
    assert set(found) >= SKILLS, SKILLS - set(found)
    for name, text in found.items():
        used = set(re.findall(r"`([a-z_]+)\(", text)) | set(re.findall(r"`([a-z]+_[a-z_]+)`", text))
        assert used - TOOLS == set(), (name, used - TOOLS)

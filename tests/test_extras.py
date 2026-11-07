import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "extras" / "app-store-listing-writer" / "scripts" / "check_listing.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CHECK), *args], capture_output=True, text=True, check=False)


def test_checker_flags_problems_and_tips(tmp_path: Path) -> None:
    f = tmp_path / "opt.json"
    f.write_text(json.dumps({"name": "Streaky: Habit Tracker", "subtitle": "Daily goals and routine streaks",
                             "keywords": "habit, tracker,app,goal,goals", "track": ["mood journal"]}))
    r = run(str(f))
    assert r.returncode == 1
    assert "subtitle: 31 characters; the limit is 30." in r.stdout
    assert "spaces around commas" in r.stdout and "'app' is wasted" in r.stdout
    assert "'habit' is in name and keywords" in r.stdout and "both 'goal' and 'goals'" in r.stdout
    assert "not covered by name, subtitle or keywords: mood journal" in r.stdout


def test_checker_clean_listing_passes() -> None:
    r = run("--name", "Streaky: Habit Tracker", "--subtitle", "Build streaks that last",
            "--keywords", "routine,planner,goals,daily,reminder")
    assert r.returncode == 0 and "No problems found." in r.stdout


def test_agensi_zips(tmp_path: Path) -> None:
    subprocess.run([sys.executable, str(ROOT / "extras" / "build_agensi.py")], check=True, capture_output=True)
    out = ROOT / "dist" / "agensi"
    names = set(zipfile.ZipFile(out / "rankwell.zip").namelist())
    assert {"rankwell/SKILL.md", "rankwell/SETUP.md", "rankwell/config/mcp.json", "rankwell/LICENSE"} <= names
    playbooks = {n.split("/")[2] for n in names if n.startswith("rankwell/playbooks/")}
    assert playbooks == {p.name for p in (ROOT / "skills").iterdir() if (p / "SKILL.md").exists()}
    router = zipfile.ZipFile(out / "rankwell.zip").read("rankwell/SKILL.md").decode()
    for p in playbooks:
        assert f"playbooks/{p}/SKILL.md" in router, p
    assert "app-store-listing-writer/SKILL.md" in zipfile.ZipFile(out / "app-store-listing-writer.zip").namelist()

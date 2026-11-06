import plistlib
from pathlib import Path

import pytest
from click.testing import CliRunner

from rankwell import paths, schedule
from rankwell.cli import main
from rankwell.errors import RankwellError


class FakeLaunchctl:
    def __init__(self, codes: dict[str, int] | None = None, output: str = "") -> None:
        self.calls: list[list[str]] = []
        self.codes = codes or {}
        self.output = output

    def __call__(self, args: list[str]) -> tuple[int, str]:
        self.calls.append(args)
        return self.codes.get(args[0], 0), self.output


@pytest.fixture(autouse=True)
def no_real_launchd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Belt and braces: nothing in these tests may reach the real launchctl or LaunchAgents."""
    agents = tmp_path / "LaunchAgents"
    monkeypatch.setattr(schedule, "launch_agents_dir", lambda: agents)

    def refuse(args: list[str]) -> tuple[int, str]:
        raise AssertionError(f"real launchctl called: {args}")

    monkeypatch.setattr(schedule, "run_launchctl", refuse)
    return agents


def test_plist_content() -> None:
    plist = schedule.build_plist("07:05", ["/x/bin/rankwell"], Path("/logs"))
    assert plist["Label"] == "com.rankwell.collect"
    assert plist["ProgramArguments"] == ["/x/bin/rankwell", "collect", "--catch-up", "--quiet"]
    assert plist["StartCalendarInterval"] == {"Hour": 7, "Minute": 5}
    assert plist["RunAtLoad"] is True
    assert plist["StandardOutPath"] == "/logs/collect.log" and plist["StandardErrorPath"] == "/logs/collect.err.log"
    assert plist["EnvironmentVariables"]["PATH"].startswith("/x/bin:")
    assert plist["EnvironmentVariables"]["RANKWELL_HOME"] == str(paths.home())


def test_parse_time() -> None:
    assert schedule.parse_time("9:30") == (9, 30)
    for bad in ("24:00", "11:60", "noon", "11"):
        with pytest.raises(RankwellError):
            schedule.parse_time(bad)


def test_install_status_uninstall(tmp_path: Path) -> None:
    agents = tmp_path / "agents"
    fake = FakeLaunchctl(output="state = waiting\n\tlast exit code = 0\n")
    out = schedule.install("06:45", agents_dir=agents, launchctl=fake, command=["/opt/rankwell"])
    target = agents / "com.rankwell.collect.plist"
    assert out["at"] == "06:45" and target.exists()
    with target.open("rb") as f:
        assert plistlib.load(f)["StartCalendarInterval"] == {"Hour": 6, "Minute": 45}
    assert [c[0] for c in fake.calls] == ["bootout", "bootstrap"]
    assert fake.calls[1][1].startswith("gui/") and fake.calls[1][2] == str(target)

    st = schedule.status(agents_dir=agents, launchctl=fake)
    assert st["installed"] and st["loaded"] and st["at"] == "06:45" and st["last_exit"] == 0
    assert fake.calls[-1] == ["print", f"{fake.calls[1][1]}/com.rankwell.collect"]
    assert "loaded" not in schedule.status(agents_dir=agents, check_loaded=False)

    assert schedule.uninstall(agents_dir=agents, launchctl=fake)["removed"] and not target.exists()
    assert not schedule.uninstall(agents_dir=agents, launchctl=fake)["removed"]
    assert schedule.status(agents_dir=agents, launchctl=fake) == {"installed": False, "plist": str(target)}


def test_install_uses_config_time(tmp_path: Path) -> None:
    paths.config_path().write_text('[collect]\nat = "13:20"\n')
    out = schedule.install(agents_dir=tmp_path, launchctl=FakeLaunchctl(), command=["/opt/rankwell"])
    assert out["at"] == "13:20"


def test_install_reports_launchctl_failure(tmp_path: Path) -> None:
    fake = FakeLaunchctl(codes={"bootstrap": 5}, output="Bootstrap failed: 5: Input/output error")
    with pytest.raises(RankwellError, match="exit 5"):
        schedule.install("11:00", agents_dir=tmp_path, launchctl=fake, command=["/opt/rankwell"])


def test_rankwell_command_is_absolute() -> None:
    cmd = schedule.rankwell_command()
    assert Path(cmd[0]).is_absolute() and Path(cmd[0]).exists()


def test_cli_schedule(monkeypatch: pytest.MonkeyPatch, no_real_launchd: Path) -> None:
    fake = FakeLaunchctl(output="last exit code = 1")
    monkeypatch.setattr(schedule, "run_launchctl", fake)
    runner = CliRunner()
    r = runner.invoke(main, ["schedule", "install", "--at", "08:15"])
    assert r.exit_code == 0, r.output
    assert "08:15" in r.output and (no_real_launchd / "com.rankwell.collect.plist").exists()
    r = runner.invoke(main, ["schedule", "status"])
    assert "daily at 08:15, loaded, last exit 1" in r.output
    r = runner.invoke(main, ["schedule", "uninstall"])
    assert "removed" in r.output and not (no_real_launchd / "com.rankwell.collect.plist").exists()


def test_launcher_follows_recorded_root(tmp_path: Path) -> None:
    import subprocess

    script = schedule.write_launcher(["/bin/echo", "fallback"])
    out = subprocess.run([str(script), "collect"], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "fallback collect"
    root = schedule.record_root()
    text = script.read_text()
    assert "plugin_root" in text and (root is None or (schedule.paths.home() / "plugin_root").read_text() == str(root))

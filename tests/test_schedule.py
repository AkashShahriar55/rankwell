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
    monkeypatch.setattr(schedule, "current_platform", lambda: "darwin")  # these are the launchd tests, on any OS

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


LINUX_CMD = ["/opt/rankwell/bin/rankwell"]


class FakeSystemctl:
    """Stands in for `systemctl --user`. usable=False makes every call fail, like a box with no user session."""

    def __init__(self, usable: bool = True, codes: dict[str, int] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.usable = usable
        self.codes = codes or {}

    def __call__(self, args: list[str]) -> tuple[int, str]:
        self.calls.append(args)
        if not self.usable:
            return 1, "Failed to connect to bus: No medium found"
        return self.codes.get(args[0], 0), ""


class FakeCrontab:
    """Stands in for `crontab`: `-l` lists the stored text, `-` replaces it from stdin."""

    def __init__(self, text: str = "", list_error: tuple[int, str] | None = None) -> None:
        self.text = text
        self.list_error = list_error  # makes `crontab -l` fail for a reason other than "no crontab"
        self.writes = 0

    def __call__(self, args: list[str], stdin: str | None = None) -> tuple[int, str]:
        if args == ["-l"]:
            if self.list_error:
                return self.list_error
            return (0, self.text) if self.text else (1, "no crontab for user")
        self.writes += 1
        self.text = (stdin or "").rstrip("\n")
        return 0, ""


def linux_install(tmp_path: Path, at: str = "11:00", *, systemd: bool = True, cron: FakeCrontab | None = None,
                  sc: FakeSystemctl | None = None) -> tuple[dict, FakeSystemctl, FakeCrontab]:
    sc = sc or FakeSystemctl(usable=systemd)
    cron = cron or FakeCrontab()
    out = schedule.install(at, platform="linux", unit_dir=tmp_path / "units", systemctl=sc, crontab=cron,
                           command=LINUX_CMD)
    return out, sc, cron


def test_linux_install_writes_and_enables_timer(tmp_path: Path) -> None:
    out, sc, _ = linux_install(tmp_path, "11:00")
    units = tmp_path / "units"
    timer, service = (units / "rankwell-collect.timer").read_text(), (units / "rankwell-collect.service").read_text()
    assert out["backend"] == "systemd" and out["at"] == "11:00" and out["catch_up"] is True
    assert "OnCalendar=*-*-* 11:00:00" in timer and "Persistent=true" in timer and "WantedBy=timers.target" in timer
    assert "Type=oneshot" in service
    assert 'ExecStart="/opt/rankwell/bin/rankwell" "collect" "--catch-up" "--quiet"' in service
    assert f"Environment=\"RANKWELL_HOME={paths.home()}\"" in service
    assert f"StandardOutput=append:{paths.logs_dir() / 'collect.log'}" in service
    assert sc.calls[-2:] == [["daemon-reload"], ["enable", "--now", "rankwell-collect.timer"]]


def test_linux_install_uses_config_time(tmp_path: Path) -> None:
    paths.config_path().write_text('[collect]\nat = "13:20"\n')
    out, _, _ = linux_install(tmp_path, None)  # type: ignore[arg-type]
    assert out["at"] == "13:20"
    assert "13:20:00" in (tmp_path / "units" / "rankwell-collect.timer").read_text()


def test_linux_install_bad_time_writes_nothing(tmp_path: Path) -> None:
    sc = FakeSystemctl()
    with pytest.raises(RankwellError, match="not a time"):
        linux_install(tmp_path, "25:00", sc=sc)
    assert not (tmp_path / "units").exists() and sc.calls == []


def test_linux_install_reports_systemctl_failure(tmp_path: Path) -> None:
    with pytest.raises(RankwellError, match="exit 1"):
        linux_install(tmp_path, sc=FakeSystemctl(codes={"enable": 1}))


def test_linux_falls_back_to_cron(tmp_path: Path) -> None:
    cron = FakeCrontab("0 3 * * * /usr/bin/other")
    out, _, cron = linux_install(tmp_path, "07:30", systemd=False, cron=cron)
    assert out["backend"] == "cron" and out["catch_up"] is False and "catch up" in out["note"]
    assert not (tmp_path / "units").exists()
    first, second = cron.text.splitlines()
    assert first == "0 3 * * * /usr/bin/other"  # the user's own entry survives
    assert second.startswith("30 7 * * * ") and second.endswith(schedule.CRON_TAG)
    assert "/opt/rankwell/bin/rankwell collect --catch-up --quiet" in second


def test_linux_cron_reinstall_does_not_duplicate(tmp_path: Path) -> None:
    cron = FakeCrontab()
    linux_install(tmp_path, "07:30", systemd=False, cron=cron)
    linux_install(tmp_path, "08:00", systemd=False, cron=cron)
    assert len(cron.text.splitlines()) == 1 and cron.text.startswith("0 8 * * * ")


def test_linux_systemd_install_drops_old_cron_line(tmp_path: Path) -> None:
    cron = FakeCrontab(f"0 3 * * * /usr/bin/other\n30 7 * * * x {schedule.CRON_TAG}")
    _, _, cron = linux_install(tmp_path, cron=cron)
    assert cron.text == "0 3 * * * /usr/bin/other"


def test_linux_status_systemd(tmp_path: Path) -> None:
    _, sc, cron = linux_install(tmp_path, "06:45")
    st = schedule.status(platform="linux", unit_dir=tmp_path / "units", systemctl=sc, crontab=cron)
    assert st["installed"] and st["backend"] == "systemd" and st["at"] == "06:45"
    assert st["enabled"] and st["loaded"] and st["catch_up"] is True
    assert sc.calls[-2:] == [["is-enabled", "rankwell-collect.timer"], ["is-active", "rankwell-collect.timer"]]
    st = schedule.status(platform="linux", unit_dir=tmp_path / "units", crontab=cron, check_loaded=False)
    assert "loaded" not in st and "enabled" not in st


def test_linux_status_cron_says_no_catch_up(tmp_path: Path) -> None:
    _, sc, cron = linux_install(tmp_path, "06:45", systemd=False)
    st = schedule.status(platform="linux", unit_dir=tmp_path / "units", systemctl=sc, crontab=cron)
    assert st["backend"] == "cron" and st["at"] == "06:45" and st["catch_up"] is False
    assert "will not catch up missed days" in st["note"]


def test_linux_status_not_installed(tmp_path: Path) -> None:
    st = schedule.status(platform="linux", unit_dir=tmp_path / "units", systemctl=FakeSystemctl(),
                         crontab=FakeCrontab())
    assert st["installed"] is False


def test_linux_uninstall_systemd(tmp_path: Path) -> None:
    _, sc, cron = linux_install(tmp_path)
    out = schedule.uninstall(platform="linux", unit_dir=tmp_path / "units", systemctl=sc, crontab=cron)
    assert out["removed"] is True
    assert not list((tmp_path / "units").iterdir())
    assert ["disable", "--now", "rankwell-collect.timer"] in sc.calls and sc.calls[-1] == ["daemon-reload"]
    again = schedule.uninstall(platform="linux", unit_dir=tmp_path / "units", systemctl=sc, crontab=cron)
    assert again["removed"] is False


def test_linux_uninstall_cron_keeps_other_lines(tmp_path: Path) -> None:
    cron = FakeCrontab(f"0 3 * * * /usr/bin/other\n30 7 * * * x {schedule.CRON_TAG}")
    out = schedule.uninstall(platform="linux", unit_dir=tmp_path / "units", systemctl=FakeSystemctl(False),
                             crontab=cron)
    assert out["removed"] is True and cron.text == "0 3 * * * /usr/bin/other"


def test_dispatch_picks_backend(tmp_path: Path) -> None:
    # launchd's runner or folder means launchd, whatever the OS; otherwise the platform decides
    assert schedule._backend("darwin", None, None) == "launchd"
    assert schedule._backend("linux", None, None) == "linux"
    assert schedule._backend(None, tmp_path, None) == "launchd"
    assert schedule._backend(None, None, FakeLaunchctl()) == "launchd"
    with pytest.raises(RankwellError, match="macOS and Linux"):
        schedule._backend("win32", None, None)


def test_cli_schedule_linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(schedule, "current_platform", lambda: "linux")
    monkeypatch.setattr(schedule, "systemd_user_dir", lambda: tmp_path / "units")
    monkeypatch.setattr(schedule, "run_systemctl", FakeSystemctl())
    monkeypatch.setattr(schedule, "run_crontab", FakeCrontab())
    runner = CliRunner()
    r = runner.invoke(main, ["schedule", "install", "--at", "08:15"])
    assert r.exit_code == 0, r.output
    assert "08:15" in r.output and (tmp_path / "units" / "rankwell-collect.timer").exists()
    r = runner.invoke(main, ["schedule", "status"])
    assert r.exit_code == 0 and "08:15" in r.output
    r = runner.invoke(main, ["schedule", "uninstall"])
    assert "removed" in r.output and not (tmp_path / "units" / "rankwell-collect.timer").exists()


def test_cli_schedule_status_says_cron_wont_catch_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(schedule, "current_platform", lambda: "linux")
    monkeypatch.setattr(schedule, "systemd_user_dir", lambda: tmp_path / "units")
    monkeypatch.setattr(schedule, "run_systemctl", FakeSystemctl(usable=False))
    monkeypatch.setattr(schedule, "run_crontab", FakeCrontab())
    runner = CliRunner()
    assert runner.invoke(main, ["schedule", "install", "--at", "08:15"]).exit_code == 0
    r = runner.invoke(main, ["schedule", "status"])
    assert "cron" in r.output and "will not catch up" in r.output


def test_doctor_reports_linux_schedule(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from rankwell import setup

    monkeypatch.setattr(schedule, "current_platform", lambda: "linux")
    monkeypatch.setattr(schedule, "systemd_user_dir", lambda: tmp_path / "units")
    row = next(r for r in setup.checks() if r["name"] == "Daily schedule")
    assert row["ok"] is False and "schedule install" in row["fix"]

    monkeypatch.setattr(schedule, "run_systemctl", FakeSystemctl())
    monkeypatch.setattr(schedule, "run_crontab", FakeCrontab())
    schedule.install("11:00", command=LINUX_CMD)
    row = next(r for r in setup.checks() if r["name"] == "Daily schedule")
    assert row["ok"] is True and row["detail"] == "at 11:00"


def test_doctor_flags_cron_has_no_catch_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from rankwell import setup

    monkeypatch.setattr(schedule, "current_platform", lambda: "linux")
    monkeypatch.setattr(schedule, "systemd_user_dir", lambda: tmp_path / "units")
    monkeypatch.setattr(schedule, "run_systemctl", FakeSystemctl(usable=False))
    monkeypatch.setattr(schedule, "run_crontab", FakeCrontab())
    schedule.install("11:00", command=LINUX_CMD)
    row = next(r for r in setup.checks() if r["name"] == "Daily schedule")
    assert row["ok"] is True and "11:00" in row["detail"] and "not caught up" in row["detail"]


def test_unsupported_platform(tmp_path: Path) -> None:
    assert schedule.status(platform="win32") == {"installed": False}  # a question, so it never raises
    with pytest.raises(RankwellError, match="macOS and Linux"):
        schedule.install("11:00", platform="win32", command=LINUX_CMD)


def test_cron_read_failure_never_rewrites_the_crontab(tmp_path: Path) -> None:
    cron = FakeCrontab("0 3 * * * /usr/bin/other", list_error=(1, "crontab: error reading /var/spool/cron"))
    with pytest.raises(RankwellError, match="could not be read"):
        linux_install(tmp_path, systemd=False, cron=cron)
    assert cron.writes == 0 and cron.text == "0 3 * * * /usr/bin/other"
    with pytest.raises(RankwellError, match="could not be read"):
        schedule.uninstall(platform="linux", unit_dir=tmp_path / "units", systemctl=FakeSystemctl(False), crontab=cron)
    assert cron.writes == 0


def test_no_crontab_for_user_counts_as_empty(tmp_path: Path) -> None:
    cron = FakeCrontab(list_error=(1, "no crontab for mfalme1k0"))
    linux_install(tmp_path, systemd=False, cron=cron)
    assert cron.writes == 1 and cron.text.endswith(schedule.CRON_TAG)


def test_systemd_install_works_without_a_crontab_program(tmp_path: Path) -> None:
    cron = FakeCrontab(list_error=(schedule.NOT_FOUND, "crontab not found"))
    out, _, _ = linux_install(tmp_path, cron=cron)
    assert out["backend"] == "systemd" and cron.writes == 0


def test_status_reports_an_unreadable_crontab_instead_of_raising(tmp_path: Path) -> None:
    cron = FakeCrontab(list_error=(1, "crontab: error reading /var/spool/cron"))
    st = schedule.status(platform="linux", unit_dir=tmp_path / "units", systemctl=FakeSystemctl(), crontab=cron)
    assert st["installed"] is False and "could not be read" in st["cron_error"]

import subprocess

from scripts.hunter_preflight import all_passed, render_report, run_checks


class FakeSystem:
    def __init__(self, *, commands=None, paths=None, chrome_installed=True):
        self.commands = commands or {}
        self.paths = paths or {"uv": "/usr/local/bin/uv", "dokobot": "/usr/local/bin/dokobot"}
        self.chrome_installed = chrome_installed

    def which(self, command):
        return self.paths.get(command)

    def run(self, command, *, capture_output, text, check, timeout):
        return self.commands.get(
            tuple(command),
            subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
        )

    def has_chrome(self):
        return self.chrome_installed


def test_preflight_reports_missing_uv_in_chinese():
    fake = FakeSystem(paths={"dokobot": "/usr/local/bin/dokobot"})

    results = run_checks(runner=fake.run, which=fake.which, chrome_detector=fake.has_chrome)
    report = render_report(results)

    assert not all_passed(results)
    assert "BLOCKED: 依赖预检失败" in report
    assert "缺少或未就绪的依赖" in report
    assert "uv" in report
    assert "uv sync --python 3.12" in report


def test_preflight_reports_missing_dokobot_cli_in_chinese():
    fake = FakeSystem(paths={"uv": "/usr/local/bin/uv"})

    results = run_checks(runner=fake.run, which=fake.which, chrome_detector=fake.has_chrome)
    report = render_report(results)

    assert not all_passed(results)
    assert "Dokobot CLI" in report
    assert "npm i -g @dokobot/cli" in report
    assert "Dokobot local bridge: 跳过" in report


def test_preflight_reports_local_bridge_failure_separately_from_extension():
    fake = FakeSystem(
        commands={
            ("dokobot", "doko", "list"): subprocess.CompletedProcess(
                ["dokobot", "doko", "list"],
                1,
                stdout="",
                stderr="bridge unavailable",
            )
        }
    )

    results = run_checks(runner=fake.run, which=fake.which, chrome_detector=fake.has_chrome)
    report = render_report(results)

    assert not all_passed(results)
    assert "Dokobot local bridge" in report
    assert "dokobot install-bridge" in report
    assert "Dokobot Chrome 插件: 跳过" in report


def test_preflight_reports_chrome_extension_not_ready_when_no_local_device():
    fake = FakeSystem(
        commands={
            ("dokobot", "doko", "list"): subprocess.CompletedProcess(
                ["dokobot", "doko", "list"],
                0,
                stdout="Local:\n",
                stderr="",
            )
        }
    )

    results = run_checks(runner=fake.run, which=fake.which, chrome_detector=fake.has_chrome)
    report = render_report(results)

    assert not all_passed(results)
    assert "Dokobot Chrome 插件未连接或未 ready" in report
    assert "安装或启用 Dokobot Chrome 插件" in report
    assert "dokobot doko list" in report


def test_preflight_passes_when_required_dependencies_are_ready():
    fake = FakeSystem(
        commands={
            ("dokobot", "doko", "list"): subprocess.CompletedProcess(
                ["dokobot", "doko", "list"],
                0,
                stdout="Local:\n  62882f0c-f9f1-49fc-83b4-c0f98b4d9ece  pid 23912, Chrome, ext 0.3.0\n",
                stderr="",
            )
        }
    )

    results = run_checks(runner=fake.run, which=fake.which, chrome_detector=fake.has_chrome)
    report = render_report(results)

    assert all_passed(results)
    assert "依赖预检通过" in report
    assert "Dokobot Chrome 插件: 通过" in report

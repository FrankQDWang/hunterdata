from __future__ import annotations

import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence


LOCAL_CHROME_DEVICE_RE = re.compile(r"^\s*[0-9a-fA-F-]{36}\s+pid\s+\d+,\s+Chrome,\s+ext\s+\S+", re.MULTILINE)


@dataclass(frozen=True)
class CheckResult:
    label: str
    status: str
    message: str
    why: str = ""
    fixes: tuple[str, ...] = field(default_factory=tuple)


Runner = Callable[..., subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]
ChromeDetector = Callable[[], bool]


def _command_output(completed: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip())


def _run(
    runner: Runner,
    command: Sequence[str],
    *,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(list(command), capture_output=True, text=True, check=False, timeout=timeout)
    except FileNotFoundError:
        return subprocess.CompletedProcess(list(command), 127, stdout="", stderr="command not found")
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return subprocess.CompletedProcess(list(command), 124, stdout=stdout, stderr=stderr or "command timed out")


def detect_chrome() -> bool:
    if platform.system() == "Darwin":
        mac_paths = [
            Path("/Applications/Google Chrome.app"),
            Path.home() / "Applications" / "Google Chrome.app",
        ]
        if any(path.exists() for path in mac_paths):
            return True
    return any(
        shutil.which(command)
        for command in (
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
        )
    )


def _pass(label: str, message: str) -> CheckResult:
    return CheckResult(label=label, status="pass", message=message)


def _fail(label: str, message: str, *, why: str, fixes: Sequence[str]) -> CheckResult:
    return CheckResult(label=label, status="fail", message=message, why=why, fixes=tuple(fixes))


def _skip(label: str, message: str) -> CheckResult:
    return CheckResult(label=label, status="skip", message=message)


def run_checks(
    *,
    runner: Runner = subprocess.run,
    which: Which = shutil.which,
    chrome_detector: ChromeDetector = detect_chrome,
) -> list[CheckResult]:
    results: list[CheckResult] = []

    if not which("uv"):
        results.append(
            _fail(
                "uv",
                "未找到 uv 命令",
                why="本流程用 uv 管理 Python 3.12 环境并运行仓库脚本。",
                fixes=(
                    "按官方 uv 文档安装 uv：https://docs.astral.sh/uv/getting-started/installation/",
                    "安装后在仓库根目录运行：uv sync --python 3.12",
                    "确认可用：uv run python --version",
                ),
            )
        )
        results.append(_skip("Python 3.12 (uv)", "uv 缺失，无法检查 Python 3.12"))
    else:
        results.append(_pass("uv", "已找到 uv 命令"))
        python_check = _run(runner, ["uv", "python", "find", "3.12"])
        if python_check.returncode == 0:
            results.append(_pass("Python 3.12 (uv)", "uv 可以找到 Python 3.12"))
        else:
            detail = _command_output(python_check)
            suffix = f"：{detail}" if detail else ""
            results.append(
                _fail(
                    "Python 3.12 (uv)",
                    f"uv 找不到 Python 3.12{suffix}",
                    why="仓库声明需要 Python >=3.12,<3.13，脚本和测试按这个版本运行。",
                    fixes=(
                        "在仓库根目录运行：uv python install 3.12",
                        "然后运行：uv sync --python 3.12",
                        "确认可用：uv run python --version",
                    ),
                )
            )

    if chrome_detector():
        results.append(_pass("Chrome", "已检测到 Google Chrome"))
    else:
        results.append(
            _fail(
                "Chrome",
                "未检测到 Google Chrome",
                why="Dokobot local bridge 需要连接本机 Chrome 来读取网页并保存原始证据。",
                fixes=(
                    "安装 Google Chrome。",
                    "安装后打开 Chrome，并确认 Dokobot Chrome 插件可用。",
                ),
            )
        )

    if not which("dokobot"):
        results.append(
            _fail(
                "Dokobot CLI",
                "未找到 dokobot 命令",
                why="本流程需要 Dokobot CLI 通过本机 Chrome 读取网页，并写出可验证的 raw evidence 和 metadata。",
                fixes=(
                    "安装 Dokobot CLI，例如：npm i -g @dokobot/cli",
                    "确认可用：dokobot --help",
                    "之后安装 local bridge：dokobot install-bridge",
                ),
            )
        )
        results.append(_skip("Dokobot local bridge", "Dokobot CLI 缺失，无法检查 local bridge"))
        results.append(_skip("Dokobot Chrome 插件", "Dokobot CLI 缺失，无法检查 Chrome 插件连接状态"))
        return results

    cli_check = _run(runner, ["dokobot", "--help"])
    if cli_check.returncode != 0:
        detail = _command_output(cli_check)
        suffix = f"：{detail}" if detail else ""
        results.append(
            _fail(
                "Dokobot CLI",
                f"dokobot --help 执行失败{suffix}",
                why="Dokobot CLI 必须能正常启动，后续 local bridge 和网页读取都依赖它。",
                fixes=(
                    "重新安装 Dokobot CLI，例如：npm i -g @dokobot/cli",
                    "确认可用：dokobot --help",
                    "确认命令在当前 shell 的 PATH 中。",
                ),
            )
        )
        results.append(_skip("Dokobot local bridge", "Dokobot CLI 异常，无法检查 local bridge"))
        results.append(_skip("Dokobot Chrome 插件", "Dokobot CLI 异常，无法检查 Chrome 插件连接状态"))
        return results

    results.append(_pass("Dokobot CLI", "dokobot --help 可正常执行"))

    bridge_check = _run(runner, ["dokobot", "doko", "list"])
    bridge_output = _command_output(bridge_check)
    if bridge_check.returncode != 0:
        suffix = f"：{bridge_output}" if bridge_output else ""
        results.append(
            _fail(
                "Dokobot local bridge",
                f"dokobot doko list 执行失败{suffix}",
                why="local bridge 是 Dokobot CLI 与本机 Chrome/Chrome 插件通信的通道；没有它，raw evidence 无法通过本地浏览器读取。",
                fixes=(
                    "安装或修复 Dokobot local bridge：dokobot install-bridge",
                    "重启 Chrome。",
                    "确认 bridge 可用：dokobot doko list",
                ),
            )
        )
        results.append(_skip("Dokobot Chrome 插件", "local bridge 未通过，无法检查 Chrome 插件连接状态"))
        return results

    results.append(_pass("Dokobot local bridge", "dokobot doko list 可正常执行"))

    if LOCAL_CHROME_DEVICE_RE.search(bridge_output):
        results.append(_pass("Dokobot Chrome 插件", "已发现本机 Chrome Dokobot device"))
    else:
        results.append(
            _fail(
                "Dokobot Chrome 插件",
                "Dokobot Chrome 插件未连接或未 ready",
                why="CLI 和 local bridge 已可用，但没有发现本机 Chrome device；通常表示 Chrome 插件未安装、未启用、未完成连接，或 Chrome 未打开。",
                fixes=(
                    "安装或启用 Dokobot Chrome 插件。",
                    "打开 Chrome，并确认插件处于 ready/connected 状态。",
                    "重新运行：dokobot doko list，直到 Local 区域出现 Chrome device。",
                ),
            )
        )

    return results


def all_passed(results: Sequence[CheckResult]) -> bool:
    return all(result.status != "fail" for result in results)


def _status_word(status: str) -> str:
    return {
        "pass": "通过",
        "fail": "失败",
        "skip": "跳过",
    }[status]


def render_report(results: Sequence[CheckResult]) -> str:
    lines: list[str] = []
    if all_passed(results):
        lines.append("依赖预检通过：可以继续运行 /hunter-contact-backfill。")
    else:
        lines.append("BLOCKED: 依赖预检失败")
    lines.append("")
    lines.append("检查项：")
    for result in results:
        lines.append(f"- {result.label}: {_status_word(result.status)} - {result.message}")

    failures = [result for result in results if result.status == "fail"]
    if not failures:
        return "\n".join(lines) + "\n"

    lines.append("")
    lines.append("缺少或未就绪的依赖：")
    for index, failure in enumerate(failures, start=1):
        lines.append(f"{index}. {failure.label}: {failure.message}")

    for failure in failures:
        lines.append("")
        lines.append(f"依赖：{failure.label}")
        lines.append(f"为什么需要：{failure.why}")
        lines.append("如何修复：")
        for index, fix in enumerate(failure.fixes, start=1):
            lines.append(f"{index}. {fix}")

    lines.append("")
    lines.append("修复后重新运行：/hunter-contact-backfill")
    return "\n".join(lines) + "\n"


def main() -> int:
    results = run_checks()
    print(render_report(results), end="")
    return 0 if all_passed(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

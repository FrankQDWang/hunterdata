from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class RawRead:
    url: str
    path: Path
    command: list[str]
    accessed_at: str


class DokobotError(RuntimeError):
    def __init__(self, message: str, *, error_path: Path | None = None) -> None:
        super().__init__(message)
        self.error_path = error_path


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slug(value: str) -> str:
    lowered = value.casefold()
    parts = re.findall(r"[a-z0-9]+", lowered)
    return "-".join(parts)[:72] or "source"


class DokobotClient:
    def __init__(
        self,
        raw_dir: Path,
        *,
        local: bool = False,
        runner: Runner = subprocess.run,
        timestamp: Callable[[], str] = utc_timestamp,
        timeout: int = 60,
    ) -> None:
        self.raw_dir = Path(raw_dir)
        self.local = local
        self.runner = runner
        self.timestamp = timestamp
        self.timeout = timeout

    def read(self, url: str) -> RawRead:
        output_path = self._raw_path("read", url)
        command = ["dokobot", "read"]
        if self.local:
            command.append("--local")
        command.extend(["--timeout", str(self.timeout), url, "-o", str(output_path)])
        self._run(command, output_path)
        if not output_path.exists():
            raise self._persist_error(command, "Dokobot did not create the expected output file", "", "")
        return RawRead(url=url, path=output_path, command=command, accessed_at=self._iso_accessed_at())

    def search(self, query: str, *, num: int = 5) -> RawRead:
        output_path = self._raw_path("search", query)
        command = ["dokobot", "search", "--num", str(num), query]
        completed = self._run(command, output_path)
        output_path.write_text(completed.stdout or "", encoding="utf-8")
        return RawRead(url=query, path=output_path, command=command, accessed_at=self._iso_accessed_at())

    def _raw_path(self, kind: str, label: str) -> Path:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        return self.raw_dir / f"{self.timestamp()}-{kind}-{_slug(label)}.txt"

    def _error_path(self, command: Sequence[str]) -> Path:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        return self.raw_dir / f"{self.timestamp()}-error-{_slug(' '.join(command))}.txt"

    def _run(self, command: list[str], output_path: Path) -> subprocess.CompletedProcess[str]:
        try:
            return self.runner(
                command,
                timeout=self.timeout + 5,
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError as exc:
            raise self._persist_error(command, "dokobot executable was not found", "", str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            raise self._persist_error(command, f"Dokobot command timed out after {exc.timeout} seconds", stdout, stderr) from exc
        except subprocess.CalledProcessError as exc:
            stdout = exc.output if isinstance(exc.output, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            raise self._persist_error(command, f"Dokobot command failed with exit {exc.returncode}", stdout, stderr) from exc

    def _persist_error(self, command: Sequence[str], message: str, stdout: str, stderr: str) -> DokobotError:
        path = self._error_path(command)
        body = "\n".join(
            [
                message,
                "",
                "Command:",
                " ".join(command),
                "",
                "STDOUT:",
                stdout,
                "",
                "STDERR:",
                stderr,
                "",
            ]
        )
        path.write_text(body, encoding="utf-8")
        detail = stderr.strip() or stdout.strip() or message
        return DokobotError(f"{message}: {detail}", error_path=path)

    @staticmethod
    def _iso_accessed_at() -> str:
        return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

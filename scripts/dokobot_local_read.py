from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


LOCAL_DEVICE_RE = re.compile(r"^\s*([0-9a-fA-F-]{36})\s+pid\s+\d+,\s+Chrome,\s+ext\s+\S+", re.MULTILINE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_local_device_id(doko_list_output: str) -> str:
    match = LOCAL_DEVICE_RE.search(doko_list_output)
    if not match:
        raise ValueError("No local Chrome Dokobot device found in `dokobot doko list` output")
    return match.group(1)


def discover_local_device_id(*, runner=subprocess.run) -> tuple[str, str]:
    completed = runner(["dokobot", "doko", "list"], capture_output=True, text=True, check=False)
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        raise RuntimeError(f"`dokobot doko list` failed with code {completed.returncode}:\n{output}")
    return parse_local_device_id(output), output


def build_read_command(*, url: str, output: Path, device_id: str, timeout: int, reuse_tab: bool = False) -> list[str]:
    command = [
        "dokobot",
        "read",
        "--local",
        "--device",
        device_id,
        "--timeout",
        str(timeout),
        "-o",
        str(output),
    ]
    if reuse_tab:
        command.append("--reuse-tab")
    command.append(url)
    return command


def open_visible_chrome_tab(url: str, *, runner=subprocess.run, delay_seconds: float = 1.0) -> dict[str, object]:
    command = ["open", "-a", "Google Chrome", url]
    completed = runner(command, capture_output=True, text=True, check=False)
    if completed.returncode == 0 and delay_seconds > 0:
        time.sleep(delay_seconds)
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def read_with_local_dokobot(
    *,
    url: str,
    output: Path,
    device_id: str | None = None,
    timeout: int = 120,
    visible_tab: bool = True,
    visible_tab_delay: float = 1.0,
    runner=subprocess.run,
) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    doko_list_output = ""
    if not device_id:
        device_id, doko_list_output = discover_local_device_id(runner=runner)
    visible_tab_result = None
    if visible_tab:
        visible_tab_result = open_visible_chrome_tab(url, runner=runner, delay_seconds=visible_tab_delay)
        if visible_tab_result["returncode"] != 0:
            print(visible_tab_result["stderr"], end="", file=sys.stderr)
            return int(visible_tab_result["returncode"])
    command = build_read_command(url=url, output=output, device_id=device_id, timeout=timeout, reuse_tab=visible_tab)
    completed = runner(command, capture_output=True, text=True, check=False)
    finished_at = utc_now()
    meta = {
        "tool": "dokobot",
        "mode": "local",
        "device_id": device_id,
        "url": url,
        "output_path": str(output),
        "meta_path": str(meta_path_for(output)),
        "command": command,
        "visible_tab": visible_tab,
        "visible_tab_result": visible_tab_result,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "doko_list_output": doko_list_output,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    meta_path_for(output).write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return completed.returncode


def meta_path_for(output: Path) -> Path:
    return output.with_name(f"{output.name}.meta.json")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read a URL through the local Chrome Dokobot bridge and write audit metadata.")
    parser.add_argument("url")
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--device")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--no-visible-tab", action="store_true")
    parser.add_argument("--visible-tab-delay", type=float, default=1.0)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    raise SystemExit(
        read_with_local_dokobot(
            url=args.url,
            output=args.output,
            device_id=args.device,
            timeout=args.timeout,
            visible_tab=not args.no_visible_tab,
            visible_tab_delay=args.visible_tab_delay,
        )
    )


if __name__ == "__main__":
    main()

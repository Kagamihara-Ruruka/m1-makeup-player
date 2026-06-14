from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    checks = [
        [sys.executable, "-m", "compileall", "m1_player", "scripts"],
        [sys.executable, "scripts/smoke_test.py"],
        [sys.executable, "scripts/ui_smoke_test.py"],
        ["git", "diff", "--check"],
    ]
    for command in checks:
        run(command)
    tracked_forbidden = git_lines(["git", "ls-files", ".venv", "state", "tmp", "subtitles", "dist"])
    if tracked_forbidden:
        raise SystemExit(f"forbidden release paths are tracked: {tracked_forbidden}")
    required = [
        "README.zh-TW.md",
        "requirements.txt",
        "local_settings.example.json",
        "bootstrap_windows.bat",
        "run_player.bat",
        "m1_player/version.py",
    ]
    missing = [item for item in required if not (ROOT / item).exists()]
    if missing:
        raise SystemExit(f"missing release files: {missing}")
    print("release check PASS")
    return 0


def run(command: list[str]) -> None:
    print("+ " + " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def git_lines(command: list[str]) -> list[str]:
    result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


if __name__ == "__main__":
    raise SystemExit(main())

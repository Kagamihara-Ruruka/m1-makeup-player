from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a portable source release zip.")
    parser.add_argument("--output-dir", default=str(ROOT / "dist"))
    args = parser.parse_args()

    from m1_player.version import APP_NAME, __version__

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    git_sha = git_text(["git", "rev-parse", "HEAD"])
    package_name = f"{APP_NAME}-{__version__}"
    zip_path = output_dir / f"{package_name}.zip"
    tracked_files = git_lines(["git", "ls-files"])
    manifest = {
        "app_name": APP_NAME,
        "version": __version__,
        "git_sha": git_sha,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "package_kind": "portable_source_windows",
        "secret_policy": "state, local settings, token, subtitles, tmp, and venv are excluded",
        "entrypoints": ["bootstrap_windows.bat", "run_player.bat"],
    }
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in tracked_files:
            path = ROOT / relative
            if path.is_file():
                archive.write(path, f"{package_name}/{relative}")
        archive.writestr(
            f"{package_name}/release_manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
    print(zip_path)
    return 0


def git_text(command: list[str]) -> str:
    result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def git_lines(command: list[str]) -> list[str]:
    return [line for line in git_text(command).splitlines() if line.strip()]


if __name__ == "__main__":
    raise SystemExit(main())

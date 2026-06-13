from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import PROJECT_ROOT


@dataclass(frozen=True)
class SetupGuide:
    lines: tuple[str, ...]
    commands: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "lines": list(self.lines),
            "commands": list(self.commands),
        }

    def to_text(self) -> str:
        return "\n".join(self.lines)


def build_setup_guide(settings: dict[str, object], project_root: Path = PROJECT_ROOT) -> SetupGuide:
    python_exe = project_root / ".venv" / "Scripts" / "python.exe"
    scripts = project_root / "scripts"
    commands = ["$env:PYTHONUTF8='1'"]
    lines = ["外部設定導引："]

    if not _configured(settings, "notion_token"):
        commands.append(f'{python_exe} {scripts / "set_token.py"}')
        lines.append("- 設定 Notion API token，啟用官方同步與短效影片 URL 解析。")

    if not _configured(settings, "completion_data_source"):
        if _configured(settings, "notion_token"):
            commands.append(f'{python_exe} {scripts / "bootstrap_completion_database.py"} --parent-from-schedule --apply --save')
        commands.append(f'{python_exe} {scripts / "set_completion_database.py"} "<completion_data_source_url_or_id>"')
        lines.append("- 建立或設定補課完成紀錄 data source，讓完成事件有可回寫目標。")

    commands.extend(
        [
            f'{python_exe} {scripts / "check_writeback_schema.py"}',
            f'{python_exe} {scripts / "writeback_apply_smoke.py"} --json',
            f'{python_exe} {scripts / "scan_schedule.py"} --max-pages 5 --timeout-sec 45 --json',
            f'{python_exe} {scripts / "resolve_sources.py"} --show-reason',
            f'{python_exe} {scripts / "readiness.py"}',
            f'{python_exe} {scripts / "run_ui.py"}',
        ]
    )
    lines.extend(
        [
            "- 檢查補課完成紀錄 data source schema。",
            "- 試跑課程同步與影片來源解析。",
            "- 確認 readiness 後啟動播放器。",
            "",
            "可複製命令：",
            *commands,
        ]
    )
    return SetupGuide(lines=tuple(lines), commands=tuple(commands))


def _configured(settings: dict[str, object], key: str) -> bool:
    value = settings.get(key)
    return isinstance(value, dict) and value.get("status") == "configured"

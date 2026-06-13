from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = PROJECT_ROOT / "state"
SUBTITLE_DIR = PROJECT_ROOT / "subtitles"
PROGRESS_CACHE = STATE_DIR / "progress_cache.json"
WRITEBACK_OUTBOX = STATE_DIR / "notion_writeback_outbox.jsonl"
RESOLVED_URL_CACHE = STATE_DIR / "resolved_url_cache.json"

DEFAULT_SCHEDULE_VIEW_URL = (
    "https://www.notion.so/32278539890480b4b5f2edf1c14ecfd2"
    "?v=32278539890480f5b07a000cb57d695d"
)


@dataclass(frozen=True)
class AppConfig:
    schedule_view_url: str = DEFAULT_SCHEDULE_VIEW_URL
    progress_cache: Path = PROGRESS_CACHE
    subtitle_dir: Path = SUBTITLE_DIR
    writeback_outbox: Path = WRITEBACK_OUTBOX
    resolved_url_cache: Path = RESOLVED_URL_CACHE
    page_size: int = 25
    max_pages: int | None = None
    completion_threshold: float = 0.95
    notion_request_timeout_sec: int = 45

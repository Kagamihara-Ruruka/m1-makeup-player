from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .video_source import VideoSourceInfo, is_http_stream_url


@dataclass(frozen=True)
class ResolvedUrlEntry:
    key: str
    source_kind: str
    filename_hint: str | None
    playable_url: str
    expires_at: str | None
    resolved_at: str

    def is_valid(self, min_ttl_seconds: int = 300) -> bool:
        if not self.expires_at:
            return True
        expiry = parse_datetime(self.expires_at)
        if expiry is None:
            return False
        return expiry > datetime.now(timezone.utc) + timedelta(seconds=min_ttl_seconds)


class ResolvedUrlCache:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.entries: dict[str, ResolvedUrlEntry] = {}

    def load(self) -> None:
        if not self.path.exists():
            self.entries = {}
            return
        data = json.loads(self.path.read_text(encoding="utf-8", errors="strict"))
        self.entries = {
            key: ResolvedUrlEntry(**value)
            for key, value in data.get("entries", {}).items()
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "entries": {
                key: asdict(entry)
                for key, entry in sorted(self.entries.items())
            }
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def get_valid(self, source: VideoSourceInfo, min_ttl_seconds: int = 300) -> ResolvedUrlEntry | None:
        entry = self.entries.get(cache_key(source.raw_ref))
        if entry and entry.is_valid(min_ttl_seconds=min_ttl_seconds):
            return entry
        return None

    def put(
        self,
        source: VideoSourceInfo,
        playable_url: str,
        expires_at: str | None,
    ) -> ResolvedUrlEntry:
        if not is_http_stream_url(playable_url):
            raise ValueError("resolved URL cache only accepts http/https stream URLs")
        entry = ResolvedUrlEntry(
            key=cache_key(source.raw_ref),
            source_kind=source.source_kind,
            filename_hint=source.filename_hint,
            playable_url=playable_url,
            expires_at=expires_at,
            resolved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self.entries[entry.key] = entry
        return entry

    def stats(self) -> dict[str, int]:
        total = len(self.entries)
        valid = sum(1 for entry in self.entries.values() if entry.is_valid())
        return {"total": total, "valid": valid, "expired": total - valid}


def cache_key(raw_ref: str) -> str:
    return hashlib.sha256(raw_ref.encode("utf-8")).hexdigest()


def parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

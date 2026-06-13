from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .local_settings import load_local_settings
from .models import PlaybackRecord
from .notion_api import NotionApiClient, first_data_source_id
from .notion_property_adapter import notion_properties_for_completion_event
from .sync_service import notion_token
from .writeback import WritebackEvent, WritebackOutbox
from .writeback_schema import completion_record_properties


@dataclass(frozen=True)
class FlushResult:
    attempted: int
    succeeded: int
    remaining: int
    dry_run: bool
    message: str


class CompletionWritebackSink:
    def __init__(self, data_source_id: str | None = None, local_settings_path: str | Path | None = None) -> None:
        settings = load_local_settings(local_settings_path) if local_settings_path is not None else load_local_settings()
        self.data_source_id = data_source_id or settings.completion_database_id

    def configured(self) -> bool:
        return bool(self.data_source_id)

    def dry_run_payload_for_record(self, record: PlaybackRecord) -> dict[str, Any]:
        return {
            "parent": {"data_source_id": self.data_source_id or "<missing_completion_database_id>"},
            "pages": [{"properties": completion_record_properties(record)}],
        }

    def dry_run_payload_for_event(self, event: WritebackEvent) -> dict[str, Any]:
        return {
            "parent": {"type": "data_source_id", "data_source_id": self.data_source_id or "<missing_completion_database_id>"},
            "properties": notion_properties_for_completion_event(event),
        }

    def send_event(self, client: NotionApiClient, event: WritebackEvent) -> None:
        if not self.data_source_id:
            raise RuntimeError("completion_database_id is not configured")
        data_source_id = first_data_source_id(client, self.data_source_id)
        client.create_page(data_source_id, notion_properties_for_completion_event(event))


def flush_outbox(
    outbox: WritebackOutbox,
    sink: CompletionWritebackSink,
    dry_run: bool = False,
    local_settings_path: str | Path | None = None,
) -> FlushResult:
    events = outbox.load_events()
    if not events:
        return FlushResult(0, 0, 0, dry_run, "outbox empty")
    if dry_run or not sink.configured():
        reason = "dry-run" if dry_run else "completion_database_id missing"
        return FlushResult(len(events), 0, len(events), True, reason)

    token = notion_token(local_settings_path)
    if not token:
        return FlushResult(len(events), 0, len(events), True, "notion_token missing")
    client = NotionApiClient(token)
    succeeded = 0
    remaining: list[WritebackEvent] = []
    for event in events:
        try:
            sink.send_event(client, event)
            succeeded += 1
        except Exception:
            remaining.append(event)

    outbox.replace_events(remaining)
    return FlushResult(
        attempted=len(events),
        succeeded=succeeded,
        remaining=len(remaining),
        dry_run=False,
        message="flush completed",
    )

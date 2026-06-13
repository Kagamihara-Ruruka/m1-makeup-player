from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .playback import find_mpv
from .progress import ProgressStore
from .resolved_url_cache import ResolvedUrlCache
from .settings_status import collect_settings_status
from .source_readiness import audit_source_readiness, source_readiness_passes, summarize_source_readiness
from .streaming_policy import (
    audit_resolved_url_cache,
    audit_streaming_sources,
    streaming_policy_passes,
    summarize_streaming_policy,
)
from .subtitle_readiness import audit_subtitle_readiness, subtitle_readiness_passes, summarize_subtitle_readiness


@dataclass(frozen=True)
class MvpReadinessGate:
    key: str
    status: str
    scope: str
    message: str

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"

    @property
    def warning(self) -> bool:
        return self.status == "warning"

    def to_json(self) -> dict[str, str]:
        return {
            "key": self.key,
            "status": self.status,
            "scope": self.scope,
            "message": self.message,
        }


@dataclass(frozen=True)
class MvpReadinessReport:
    overall_status: str
    gates: tuple[MvpReadinessGate, ...]
    source_summary: dict[str, int]
    subtitle_summary: dict[str, int]
    streaming_summary: dict[str, int]
    settings: dict[str, object]

    @property
    def blocking_gates(self) -> tuple[MvpReadinessGate, ...]:
        return tuple(gate for gate in self.gates if gate.blocked)

    @property
    def warning_gates(self) -> tuple[MvpReadinessGate, ...]:
        return tuple(gate for gate in self.gates if gate.warning)

    def to_json(self) -> dict[str, object]:
        return {
            "overall_status": self.overall_status,
            "blocking_gates": [gate.key for gate in self.blocking_gates],
            "warning_gates": [gate.key for gate in self.warning_gates],
            "source_summary": self.source_summary,
            "subtitle_summary": self.subtitle_summary,
            "streaming_summary": self.streaming_summary,
            "settings": self.settings,
            "gates": [gate.to_json() for gate in self.gates],
        }


def collect_mvp_readiness(config: AppConfig, local_settings_path: str | Path | None = None) -> MvpReadinessReport:
    settings = collect_settings_status(config, local_settings_path=local_settings_path)
    store = ProgressStore(config.progress_cache)
    store.load()
    records = list(store.records.values())
    source_rows = audit_source_readiness(records)
    source_summary = summarize_source_readiness(source_rows)
    subtitle_rows = audit_subtitle_readiness(records, config.subtitle_dir)
    subtitle_summary = summarize_subtitle_readiness(subtitle_rows)
    streaming_source_rows = audit_streaming_sources(records)
    streaming_cache_rows = audit_resolved_url_cache(ResolvedUrlCache(config.resolved_url_cache))
    streaming_summary = summarize_streaming_policy(streaming_source_rows, streaming_cache_rows)
    mpv = find_mpv()
    schedule_message = _schedule_cache_message(len(records), store.metadata.to_json())

    gates = [
        _gate(
            "playback_core",
            "pass" if mpv.available else "blocked",
            "local_playback",
            f"mpv ready: {mpv.mpv_path}" if mpv.available and mpv.mpv_path else "mpv.exe not found",
        ),
        _gate(
            "schedule_cache",
            "pass" if records else "warning",
            "startup_sync",
            schedule_message,
        ),
        _gate(
            "source_shape",
            "pass" if source_readiness_passes(source_rows) else "blocked",
            "stream_resolution",
            f"source readiness: {source_summary}",
        ),
        _gate(
            "streaming_boundary",
            "pass" if streaming_policy_passes(streaming_source_rows, streaming_cache_rows) else "blocked",
            "streaming_first",
            f"streaming policy: {streaming_summary}",
        ),
        _gate(
            "subtitle_files",
            "pass" if subtitle_readiness_passes(subtitle_rows) else "warning",
            "subtitle_prompt_box",
            f"subtitle readiness: {subtitle_summary}",
        ),
        _gate(
            "notion_token",
            "pass" if _configured(settings, "notion_token") else "blocked",
            "official_sync_and_stream_resolution",
            "Notion token configured" if _configured(settings, "notion_token") else "Notion token missing; cannot resolve attachment URLs",
        ),
        _gate(
            "completion_data_source",
            "pass" if _configured(settings, "completion_data_source") else "blocked",
            "completion_writeback",
            (
                "completion data source configured"
                if _configured(settings, "completion_data_source")
                else "completion data source missing; writeback remains dry-run"
            ),
        ),
        _gate(
            "writeback_outbox",
            "warning" if int(settings["queued_writeback_events"]) else "pass",
            "completion_writeback",
            f"queued writeback events: {settings['queued_writeback_events']}",
        ),
    ]
    overall_status = _overall_status(gates)
    return MvpReadinessReport(
        overall_status=overall_status,
        gates=tuple(gates),
        source_summary=source_summary,
        subtitle_summary=subtitle_summary,
        streaming_summary=streaming_summary,
        settings=settings,
    )


def _gate(key: str, status: str, scope: str, message: str) -> MvpReadinessGate:
    return MvpReadinessGate(key=key, status=status, scope=scope, message=message)


def _overall_status(gates: list[MvpReadinessGate]) -> str:
    if any(gate.blocked for gate in gates):
        return "external_setup_required"
    if any(gate.warning for gate in gates):
        return "usable_with_warnings"
    return "ready_for_real_notion_trial"


def _schedule_cache_message(record_count: int, metadata: dict[str, object]) -> str:
    if record_count <= 0:
        return "no cached video records; run schedule sync"
    synced_at = metadata.get("last_synced_at")
    backend = metadata.get("last_sync_backend")
    if synced_at and backend:
        return f"cached video records: {record_count}; last_sync={backend} at {synced_at}"
    return f"cached video records: {record_count}; last_sync metadata missing"


def _configured(settings: dict[str, object], key: str) -> bool:
    value = settings.get(key)
    if not isinstance(value, dict):
        return False
    return value.get("status") == "configured"

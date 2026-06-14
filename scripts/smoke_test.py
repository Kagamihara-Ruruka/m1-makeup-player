from __future__ import annotations

import os
import shutil
import sys
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.config import AppConfig  # noqa: E402
from m1_player.completion import queue_completion_event  # noqa: E402
from m1_player.completion_database import (  # noqa: E402
    build_completion_database_payload,
    completion_database_request_properties,
    extract_first_created_data_source_id,
)
from m1_player.models import CoursePageRef, LessonStatus, PlaybackRecord, VideoSegment  # noqa: E402
from m1_player.attachment_resolver import NotionAttachmentResolver  # noqa: E402
from m1_player.local_settings import LocalSettings, load_local_settings, save_local_settings  # noqa: E402
from m1_player.mvp_readiness import collect_mvp_readiness  # noqa: E402
from m1_player.notion_api import extract_database_id_from_url, extract_notion_id, parse_api_course_page  # noqa: E402
from m1_player.notion_property_adapter import notion_properties_for_completion_event  # noqa: E402
from m1_player.notion_parser import parse_course_page  # noqa: E402
from m1_player.progress import ProgressStore  # noqa: E402
from m1_player.progress_overview import collect_progress_overview  # noqa: E402
from m1_player.playback import mpv_start_args  # noqa: E402
from m1_player.resolved_url_cache import ResolvedUrlCache  # noqa: E402
from m1_player.runtime_config import schedule_view_url_with_source  # noqa: E402
from m1_player.settings_actions import (  # noqa: E402
    set_completion_data_source,
    set_notion_token,
    set_schedule_view_url,
)
from m1_player.settings_status import collect_settings_status, redact_identifier, redact_secret  # noqa: E402
from m1_player.setup_guide import build_setup_guide  # noqa: E402
from m1_player.source_readiness import audit_source_readiness, source_readiness_passes, summarize_source_readiness  # noqa: E402
from m1_player.streaming_policy import audit_streaming_sources, streaming_policy_passes  # noqa: E402
from m1_player.sync_service import NotionScheduleSync  # noqa: E402
from m1_player.subtitle import active_cue, parse_markdown_transcript, parse_srt_or_vtt  # noqa: E402
from m1_player.subtitle_generation import (  # noqa: E402
    GeneratedSubtitleSegment,
    SubtitleGenerationOptions,
    decode_audio_window,
    decode_audio_window_with_timing,
    format_srt_timestamp,
    render_markdown_transcript,
    render_srt,
    render_vtt,
    subtitle_generation_dependency_status,
    subtitle_output_path,
    write_subtitle_segments,
)
from m1_player.subtitle_lint import lint_cues, lint_subtitle_file  # noqa: E402
from m1_player.subtitle_manifest import build_subtitle_manifest, write_missing_markdown_placeholders  # noqa: E402
from m1_player.subtitle_job_queue import RollingSubtitleJobQueue  # noqa: E402
from m1_player.subtitle_pipeline_planner import plan_rolling_subtitle_pipeline  # noqa: E402
from m1_player.subtitle_rolling_scheduler import (  # noqa: E402
    CoveredRange,
    plan_rolling_subtitle_windows,
)
from m1_player.subtitle_resolver import SubtitleResolver, safe_filename_stem  # noqa: E402
from m1_player.video_source import parse_video_source  # noqa: E402
from m1_player.writeback import WritebackOutbox  # noqa: E402
from m1_player.writeback_schema import completion_event_properties, completion_record_properties  # noqa: E402
from m1_player.writeback_schema_check import (  # noqa: E402
    check_completion_data_source_schema,
    expected_completion_data_source_fixture,
    result_to_payload,
)
from m1_player.writeback_sink import CompletionWritebackSink, flush_outbox  # noqa: E402
from scripts.profile_decode_concurrency import choose_decode_concurrency  # noqa: E402


SAMPLE_PAGE = """
<page url="https://app.notion.com/p/31b7853989048098bab5c39f0c4f2d7c">
<properties>
{"date:日期:start":"2026-03-06","名稱":"MySQL_0306"}
</properties>
<content>
<video src="file://%7B%22source%22%3A%22attachment%3Aabc%3Alecture_part_1.mp4%22%7D"></video>
<meeting-notes readOnlyViewMeetingNoteUrl="https://app.notion.com/p/page#note1"></meeting-notes>
<video src="file://%7B%22source%22%3A%22attachment%3Adef%3Alecture_part_2.mp4%22%7D"></video>
<meeting-notes readOnlyViewMeetingNoteUrl="https://app.notion.com/p/page#note2"></meeting-notes>
</content>
</page>
"""

SAMPLE_SRT = """
1
00:00:00,000 --> 00:00:02,000
第一句

2
00:00:02,000 --> 00:00:05,000
第二句
"""

SAMPLE_MARKDOWN_TRANSCRIPT = """
# 逐字稿

[00:00:00] 開場說明
[00:00:03 --> 00:00:05] 第二段說明

00:00:05
第三段第一行
第三段第二行
"""

SAMPLE_API_PAGE = {
    "id": "31b78539-8904-8098-bab5-c39f0c4f2d7c",
    "url": "https://www.notion.so/31b7853989048098bab5c39f0c4f2d7c",
    "properties": {
        "名稱": {"title": [{"plain_text": "K8S_0306"}]},
        "日期": {"date": {"start": "2026-03-06"}},
        "標籤": {"multi_select": [{"name": "K8S"}]},
    },
}

SAMPLE_API_BLOCKS = [
    {
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "type": "video",
        "video": {
            "caption": [{"plain_text": "k8s_part_1.mp4"}],
            "type": "file",
            "file": {"url": "https://s3.us-west-2.amazonaws.com/secure.notion-static.com/k8s_part_1.mp4?X=short"},
        },
    }
]

SAMPLE_COMPLETION_DATA_SOURCE = expected_completion_data_source_fixture("fake-data-source-id")


def main() -> int:
    tmp_dir = ROOT / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    parsed = parse_course_page(SAMPLE_PAGE)
    assert parsed.course.title == "MySQL_0306"
    assert parsed.course.course_date == "2026-03-06"
    assert len(parsed.videos) == 2
    assert parsed.videos[0].video_name == "lecture_part_1.mp4"
    source_info = parse_video_source(parsed.videos[0].source_ref)
    assert source_info.source_kind == "notion_attachment_marker"
    assert source_info.requires_resolution
    assert source_info.filename_hint == "lecture_part_1.mp4"
    sync_cache = tmp_dir / "sync_backend_progress.json"
    if sync_cache.exists():
        sync_cache.unlink()
    sync_result = NotionScheduleSync(AppConfig(progress_cache=sync_cache)).save_records(
        "unit_test_backend",
        [parsed],
        list(parsed.videos),
    )
    assert sync_result.sync_backend == "unit_test_backend"
    assert sync_result.cache_metadata.last_sync_backend == "unit_test_backend"
    assert sync_result.cache_metadata.last_course_page_count == 1
    assert sync_result.cache_metadata.last_video_segment_count == len(parsed.videos)
    assert sync_result.cache_metadata.last_synced_at
    assert len(sync_result.records) == len(parsed.videos)
    assert sync_cache.exists()
    synced_store = ProgressStore(sync_cache)
    synced_store.load()
    assert synced_store.metadata.last_sync_backend == "unit_test_backend"
    legacy_cache = tmp_dir / "legacy_progress_cache.json"
    legacy_cache.write_text('{"records": {}}\n', encoding="utf-8")
    legacy_store = ProgressStore(legacy_cache)
    legacy_store.load()
    assert legacy_store.metadata.last_sync_backend is None
    resolution = NotionAttachmentResolver(token="dummy").resolve(source_info)
    assert resolution.status == "missing_block_id"
    api_parsed = parse_api_course_page(SAMPLE_API_PAGE, SAMPLE_API_BLOCKS)
    assert api_parsed.course.title == "K8S_0306"
    assert api_parsed.course.course_date == "2026-03-06"
    assert api_parsed.course.tags == ("K8S",)
    assert len(api_parsed.videos) == 1
    assert api_parsed.videos[0].video_name == "k8s_part_1.mp4"
    api_source = parse_video_source(api_parsed.videos[0].source_ref)
    assert api_source.source_kind == "notion_attachment_marker"
    assert api_source.permission_record and api_source.permission_record["id"] == "aaaaaaaabbbbccccddddeeeeeeeeeeee"
    readiness_rows = audit_source_readiness([PlaybackRecord.from_segment(parsed.videos[0])])
    assert summarize_source_readiness(readiness_rows) == {"missing_permission_block": 1}
    assert not source_readiness_passes(readiness_rows)
    api_readiness_rows = audit_source_readiness([PlaybackRecord.from_segment(api_parsed.videos[0])])
    assert summarize_source_readiness(api_readiness_rows) == {"ready_for_token_resolution": 1}
    assert source_readiness_passes(api_readiness_rows)
    fetched_blocks: list[str] = []

    def fake_block_fetcher(block_id: str) -> dict[str, object]:
        fetched_blocks.append(block_id)
        return {
            "id": block_id,
            "type": "video",
            "video": {
                "type": "file",
                "file": {
                    "url": "https://example.com/notion-signed-video.mp4",
                    "expiry_time": "2099-01-01T00:00:00Z",
                },
            },
        }

    api_cache_path = tmp_dir / "api_resolved_url_cache.json"
    if api_cache_path.exists():
        api_cache_path.unlink()
    api_cache = ResolvedUrlCache(api_cache_path)
    api_resolution = NotionAttachmentResolver(
        token="dummy",
        cache=api_cache,
        block_fetcher=fake_block_fetcher,
    ).resolve(api_source)
    assert api_resolution.status == "resolved"
    assert api_resolution.playable_url == "https://example.com/notion-signed-video.mp4"
    assert api_resolution.expires_at == "2099-01-01T00:00:00Z"
    assert fetched_blocks == ["aaaaaaaabbbbccccddddeeeeeeeeeeee"]
    cached_api_resolution = NotionAttachmentResolver(cache=api_cache).resolve(api_source)
    assert cached_api_resolution.status == "resolved_from_cache"
    assert cached_api_resolution.playable_url == "https://example.com/notion-signed-video.mp4"
    assert extract_notion_id("31b78539-8904-8098-bab5-c39f0c4f2d7c") == "31b7853989048098bab5c39f0c4f2d7c"
    assert extract_notion_id("https://www.notion.so/31b7853989048098bab5c39f0c4f2d7c?v=abc") == "31b7853989048098bab5c39f0c4f2d7c"
    assert extract_database_id_from_url("https://www.notion.so/32278539890480b4b5f2edf1c14ecfd2?v=abc") == "32278539890480b4b5f2edf1c14ecfd2"
    completion_create_properties = completion_database_request_properties()
    assert completion_create_properties["影片名稱"] == {"title": {}}
    assert completion_create_properties["課程頁 URL"] == {"url": {}}
    assert "type" not in completion_create_properties["影片名稱"]
    completion_database_payload = build_completion_database_payload(
        "https://www.notion.so/31b7853989048098bab5c39f0c4f2d7c",
        title="補課完成紀錄測試",
    )
    assert completion_database_payload["parent"]["page_id"] == "31b7853989048098bab5c39f0c4f2d7c"
    assert completion_database_payload["initial_data_source"]["properties"]["影片名稱"] == {"title": {}}
    assert extract_first_created_data_source_id({"id": "database-id", "data_sources": [{"id": "data-source-id"}]}) == "data-source-id"
    settings_path = tmp_dir / "local_settings.json"
    save_local_settings(
        LocalSettings(
            notion_token="secret_test",
            completion_database_id="completion_id",
            schedule_view_url="https://www.notion.so/local-schedule",
        ),
        settings_path,
    )
    loaded_settings = load_local_settings(settings_path)
    assert loaded_settings.notion_token == "secret_test"
    assert loaded_settings.completion_database_id == "completion_id"
    assert loaded_settings.schedule_view_url == "https://www.notion.so/local-schedule"
    schedule_url, schedule_source = schedule_view_url_with_source(loaded_settings)
    assert schedule_url == "https://www.notion.so/local-schedule"
    assert schedule_source.endswith("local_settings.json")
    previous_schedule_env = os.environ.get("M1_SCHEDULE_VIEW_URL")
    os.environ["M1_SCHEDULE_VIEW_URL"] = "https://www.notion.so/env-schedule"
    try:
        env_schedule_url, env_schedule_source = schedule_view_url_with_source(loaded_settings)
        assert env_schedule_url == "https://www.notion.so/env-schedule"
        assert env_schedule_source == "environment:M1_SCHEDULE_VIEW_URL"
    finally:
        if previous_schedule_env is None:
            os.environ.pop("M1_SCHEDULE_VIEW_URL", None)
        else:
            os.environ["M1_SCHEDULE_VIEW_URL"] = previous_schedule_env
    url_cache = ResolvedUrlCache(tmp_dir / "resolved_url_cache.json")
    url_cache.load()
    url_cache.put(
        source_info,
        "https://example.com/signed-video.mp4",
        (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds"),
    )
    url_cache.save()
    cached_resolution = NotionAttachmentResolver(cache=url_cache).resolve(source_info)
    assert cached_resolution.status == "resolved_from_cache"
    assert cached_resolution.playable_url == "https://example.com/signed-video.mp4"
    try:
        url_cache.put(source_info, r"C:\videos\full-download.mp4", None)
    except ValueError as exc:
        assert "http/https stream URLs" in str(exc)
    else:
        raise AssertionError("resolved URL cache accepted a local media path")
    cues = parse_srt_or_vtt(SAMPLE_SRT)
    assert active_cue(cues, 1.0).text == "第一句"
    assert active_cue(cues, 3.0).text == "第二句"
    assert not lint_cues(cues)
    md_cues = parse_markdown_transcript(SAMPLE_MARKDOWN_TRANSCRIPT)
    assert len(md_cues) == 3
    assert md_cues[0].end_sec == 3.0
    assert active_cue(md_cues, 1.0).text == "開場說明"
    assert active_cue(md_cues, 4.0).text == "第二段說明"
    assert "第三段第二行" in active_cue(md_cues, 6.0).text
    assert safe_filename_stem("紀宜昕 20250225 1.mp4") == "紀宜昕_20250225_1"
    assert format_srt_timestamp(3723.456) == "01:02:03,456"
    generated_segments = [
        GeneratedSubtitleSegment(1, 0.0, 2.5, "第一段字幕"),
        GeneratedSubtitleSegment(2, 2.5, 5.0, "第二段字幕"),
    ]
    rendered_srt = render_srt(generated_segments)
    assert "00:00:00,000 --> 00:00:02,500" in rendered_srt
    assert "第一段字幕" in rendered_srt
    rendered_vtt = render_vtt(generated_segments)
    assert rendered_vtt.startswith("WEBVTT")
    rendered_md = render_markdown_transcript(generated_segments)
    assert "[00:00:00.000 --> 00:00:02.500] 第一段字幕" in rendered_md
    subtitle_dir = tmp_dir / "subtitles"
    subtitle_dir.mkdir(exist_ok=True)
    subtitle_path = subtitle_dir / f"{parsed.videos[0].stable_key.replace(':', '_')}.srt"
    subtitle_path.write_text(SAMPLE_SRT, encoding="utf-8", newline="\n")
    lint_result = lint_subtitle_file(subtitle_path)
    assert lint_result.passes
    assert lint_result.status == "pass"
    assert lint_result.cue_count == 2
    subtitle_resolver = SubtitleResolver(subtitle_dir)
    subtitle_record = PlaybackRecord.from_segment(parsed.videos[0])
    subtitle_candidates = subtitle_resolver.candidates_for(subtitle_record)
    assert subtitle_path in subtitle_candidates
    assert subtitle_dir / "lecture_part_1.srt" in subtitle_candidates
    assert subtitle_dir / "lecture_part_1.md" in subtitle_candidates
    found_path, found_cues = subtitle_resolver.load_for(subtitle_record)
    assert found_path == subtitle_path
    assert len(found_cues) == 2
    generated_output_path = subtitle_output_path(subtitle_record, subtitle_dir, ".srt")
    write_subtitle_segments(generated_output_path, generated_segments)
    generated_lint = lint_subtitle_file(generated_output_path)
    assert generated_lint.passes
    assert generated_lint.cue_count == 2
    assert SubtitleGenerationOptions().model_size == "medium"
    assert SubtitleGenerationOptions().language == "zh"
    assert "Kubernetes" in (SubtitleGenerationOptions().hotwords or "")
    dependency_status = subtitle_generation_dependency_status()
    assert isinstance(dependency_status.ready, bool)
    assert isinstance(dependency_status.cuda_runtime_available, bool)
    assert dependency_status.message
    rolling_plan = plan_rolling_subtitle_pipeline(
        audio_window_sec=5.0,
        decode_elapsed_sec=9.4,
        inference_elapsed_sec=1.3,
    )
    assert rolling_plan.recommended_decode_workers == 3
    assert rolling_plan.recommended_gpu_workers == 1
    assert rolling_plan.can_keep_up
    saturated_plan = plan_rolling_subtitle_pipeline(
        audio_window_sec=5.0,
        decode_elapsed_sec=30.0,
        inference_elapsed_sec=1.3,
        max_decode_workers=4,
    )
    assert saturated_plan.recommended_decode_workers == 4
    assert not saturated_plan.can_keep_up
    concurrency_recommendations = choose_decode_concurrency(
        [
            {"window_sec": 30.0, "concurrency": 4, "failure_count": 0, "capacity_ratio": 8.48, "p95_decode_sec": 14.15},
            {"window_sec": 60.0, "concurrency": 3, "failure_count": 0, "capacity_ratio": 11.59, "p95_decode_sec": 15.53},
            {"window_sec": 120.0, "concurrency": 1, "failure_count": 0, "capacity_ratio": 11.03, "p95_decode_sec": 10.88},
        ],
        [8.0],
        1.35,
        max_overall_window_sec=60.0,
    )
    assert concurrency_recommendations[0]["overall"]["window_sec"] == 60.0
    assert concurrency_recommendations[0]["overall"]["concurrency"] == 3
    rolling_schedule = plan_rolling_subtitle_windows(
        playback_position_sec=480.0,
        duration_sec=900.0,
        covered_ranges=[CoveredRange(0.0, 240.0)],
        playback_rate=8.0,
        window_sec=60.0,
        overlap_sec=3.0,
        headless_worker_count=7,
        future_horizon_sec=180.0,
        future_window_strategy="fibonacci",
        future_base_window_sec=15.0,
    )
    assert rolling_schedule.headless_worker_count == 7
    assert rolling_schedule.backfill_partition_count == 8
    assert rolling_schedule.future_window_strategy == "fibonacci"
    assert len(rolling_schedule.future_jobs) == 5
    assert len(rolling_schedule.backfill_jobs) == 4
    assert rolling_schedule.future_jobs[0].lane == "future_gpu"
    assert rolling_schedule.future_jobs[0].asr_device == "cuda"
    assert rolling_schedule.future_jobs[0].decode_start_sec == 477.0
    assert rolling_schedule.future_jobs[0].end_sec == 495.0
    assert rolling_schedule.future_jobs[-1].start_sec == 585.0
    assert rolling_schedule.future_jobs[-1].end_sec == 660.0
    assert rolling_schedule.backfill_jobs[0].lane == "backfill_cpu"
    assert rolling_schedule.backfill_jobs[0].asr_device == "cpu"
    job_queue = RollingSubtitleJobQueue(rolling_schedule.jobs)
    first_claim = job_queue.claim_next("decode-worker-0")
    second_claim = job_queue.claim_next("decode-worker-1")
    assert first_claim is not None and first_claim.job.job_id == "future_000"
    assert second_claim is not None and second_claim.job.job_id == "future_001"
    job_queue.complete(first_claim.job.job_id)
    third_claim = job_queue.claim_next("decode-worker-0")
    assert third_claim is not None and third_claim.job.job_id == "future_002"
    job_queue.fail(second_claim.job.job_id, "transient remote decode timeout")
    assert job_queue.failed_count() == 1
    job_queue.requeue_failed(second_claim.job.job_id)
    assert job_queue.failed_count() == 0
    assert job_queue.pending_count() > 0
    cpu_only_queue = RollingSubtitleJobQueue(rolling_schedule.jobs)
    cpu_claim = cpu_only_queue.claim_next("cpu-worker-0", lane="backfill_cpu")
    assert cpu_claim is not None and cpu_claim.job.job_id == "backfill_004"
    steal_queue = RollingSubtitleJobQueue(rolling_schedule.backfill_jobs)
    steal_claim = steal_queue.claim_next("gpu-worker-0", lane="future_gpu", fallback_lanes=("backfill_cpu",))
    assert steal_claim is not None and steal_claim.job.lane == "backfill_cpu"
    wav_path = tmp_dir / "subtitle_decode_probe.wav"
    write_probe_wav(wav_path, duration_sec=2.0)
    decoded_probe = decode_audio_window(str(wav_path), max_duration_sec=0.5)
    assert 7_000 <= len(decoded_probe) <= 9_000
    timed_probe = decode_audio_window_with_timing(str(wav_path), max_duration_sec=0.5)
    assert 7_000 <= timed_probe.sample_count <= 9_000
    assert timed_probe.total_elapsed_sec >= timed_probe.decode_loop_elapsed_sec
    subtitle_path.unlink()
    markdown_path = subtitle_dir / "lecture_part_1.md"
    markdown_path.write_text(SAMPLE_MARKDOWN_TRANSCRIPT, encoding="utf-8", newline="\n")
    markdown_lint_result = lint_subtitle_file(markdown_path)
    assert markdown_lint_result.passes
    assert markdown_lint_result.status == "pass"
    invalid_subtitle_path = subtitle_dir / "invalid_overlap.srt"
    invalid_subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:00:05,000\n第一句\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\n重疊句\n",
        encoding="utf-8",
        newline="\n",
    )
    invalid_lint_result = lint_subtitle_file(invalid_subtitle_path)
    assert not invalid_lint_result.passes
    assert invalid_lint_result.status == "fail"
    assert any(issue.code == "subtitle_non_monotonic_time" for issue in invalid_lint_result.issues)
    long_subtitle_path = subtitle_dir / "long_cue.srt"
    long_subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:01:00,000\n" + ("很長" * 130) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    long_lint_result = lint_subtitle_file(long_subtitle_path)
    assert long_lint_result.passes
    assert long_lint_result.status == "warning"
    assert any(issue.code == "subtitle_long_cue_duration" for issue in long_lint_result.issues)
    assert any(issue.code == "subtitle_long_cue_text" for issue in long_lint_result.issues)
    found_md_path, found_md_cues = subtitle_resolver.load_for(subtitle_record)
    assert found_md_path == markdown_path
    assert len(found_md_cues) == 3
    manifest_dir = tmp_dir / "subtitle_manifest"
    if manifest_dir.exists():
        shutil.rmtree(manifest_dir)
    manifest_rows = build_subtitle_manifest([subtitle_record], manifest_dir)
    assert manifest_rows[0].status == "missing"
    assert manifest_rows[0].preferred_markdown_path.endswith(".md")
    placeholder_result = write_missing_markdown_placeholders([subtitle_record], manifest_dir)
    assert len(placeholder_result.written) == 1
    assert not placeholder_result.skipped_existing
    assert "待補字幕" in Path(placeholder_result.written[0]).read_text(encoding="utf-8")
    placeholder_rows = build_subtitle_manifest([subtitle_record], manifest_dir)
    assert placeholder_rows[0].status == "found"
    assert Path(placeholder_rows[0].existing_path or "").exists()
    skipped_result = write_missing_markdown_placeholders([subtitle_record], manifest_dir)
    assert not skipped_result.written
    assert len(skipped_result.skipped_existing) == 1
    store = ProgressStore(tmp_dir / "smoke_progress.json")
    store.records = {}
    records = store.sync_segments(list(parsed.videos))
    assert len(records) == 2
    records[0].subtitle_path = r"D:\RRKAL_tools\m1-makeup-player\subtitles\lecture_part_1.srt"
    records[0].update_position(32.0, 100.0)
    records[0].mark_completed(100.0)
    completed_at_before_sync = records[0].completed_at
    refreshed_segment = VideoSegment(
        stable_key=parsed.videos[0].stable_key,
        course=CoursePageRef(
            title=parsed.videos[0].course.title,
            page_id=parsed.videos[0].course.page_id,
            page_url="https://notion.local/refreshed-page",
            course_date="2026-03-07",
            tags=parsed.videos[0].course.tags,
        ),
        segment_index=parsed.videos[0].segment_index,
        video_name="lecture_part_1_renamed.mp4",
        video_block_ref="https://notion.local/refreshed-page#video-001",
        source_ref=parsed.videos[0].source_ref,
        transcript_ref=parsed.videos[0].transcript_ref,
    )
    refreshed = store.sync_segments([refreshed_segment])[0]
    assert refreshed.video_name == "lecture_part_1_renamed.mp4"
    assert refreshed.course_page_url == "https://notion.local/refreshed-page"
    assert refreshed.course_date == "2026-03-07"
    assert refreshed.video_block_ref == "https://notion.local/refreshed-page#video-001"
    assert refreshed.last_position_sec == 100.0
    assert refreshed.duration_sec == 100.0
    assert refreshed.progress_percent == 100.0
    assert refreshed.status == LessonStatus.COMPLETED
    assert refreshed.completed_at == completed_at_before_sync
    assert refreshed.subtitle_path == r"D:\RRKAL_tools\m1-makeup-player\subtitles\lecture_part_1.srt"
    streaming_rows = audit_streaming_sources(records)
    assert streaming_policy_passes(streaming_rows, [])
    local_record = PlaybackRecord.from_segment(parsed.videos[0])
    local_record.source_ref = r"C:\videos\full-download.mp4"
    local_rows = audit_streaming_sources([local_record])
    assert local_rows[0].policy_status == "blocked_non_stream_source"
    assert not streaming_policy_passes(local_rows, [])
    mpv_args = mpv_start_args("mpv.exe", r"\\.\pipe\m1_smoke")
    assert "--audio-pitch-correction=yes" in mpv_args
    assert "--no-audio-pitch-correction" not in mpv_args
    record = PlaybackRecord.from_segment(parsed.videos[0])
    record.update_position(96, 100)
    assert record.should_complete(96, 100)
    record.mark_completed(100)
    assert record.completed_at
    properties = completion_record_properties(record)
    assert properties["影片名稱"] == "lecture_part_1.mp4"
    assert properties["補課狀態"] == "已完成"
    assert properties["進度百分比"] == 100.0
    assert "date:完整補課時間:start" in properties
    outbox_path = tmp_dir / "writeback_outbox.jsonl"
    if outbox_path.exists():
        outbox_path.unlink()
    WritebackOutbox(outbox_path).append_completion(record)
    assert record.stable_key in outbox_path.read_text(encoding="utf-8")
    outbox = WritebackOutbox(outbox_path)
    events = outbox.load_events()
    assert len(events) == 1
    assert outbox.has_event("completed", record.stable_key)
    duplicate_result = queue_completion_event(record, outbox, record.duration_sec)
    assert duplicate_result.status == "already_completed"
    assert not duplicate_result.queued
    assert len(outbox.load_events()) == 1
    new_record = PlaybackRecord.from_segment(parsed.videos[1])
    queued_result = queue_completion_event(new_record, outbox, 88.0)
    assert queued_result.status == "queued"
    assert queued_result.queued
    assert outbox.has_event("completed", new_record.stable_key)
    assert len(outbox.load_events()) == 2
    overview = collect_progress_overview([record, new_record], queued_writebacks=outbox.count_events())
    assert overview.total_records == 2
    assert overview.completed_count == 2
    assert overview.completed_percent == 100.0
    assert overview.average_progress_percent == 100.0
    assert overview.queued_writebacks == 2
    assert "待回寫完成紀錄：2" in overview.to_text()
    outbox.replace_events(events)
    event_properties = completion_event_properties(events[0])
    assert event_properties["影片名稱"] == "lecture_part_1.mp4"
    notion_properties = notion_properties_for_completion_event(events[0])
    assert notion_properties["影片名稱"]["title"][0]["text"]["content"] == "lecture_part_1.mp4"
    assert notion_properties["課程頁 URL"]["url"] == parsed.course.page_url
    assert notion_properties["段落序號"]["number"] == 1.0
    assert notion_properties["補課狀態"]["select"]["name"] == "已完成"
    good_schema_check = check_completion_data_source_schema(SAMPLE_COMPLETION_DATA_SOURCE)
    assert good_schema_check.status == "pass"
    assert not good_schema_check.issues
    bad_schema_check = check_completion_data_source_schema({
        "properties": {
            "影片名稱": {"type": "rich_text", "rich_text": {}},
            "課程頁 URL": {"type": "url", "url": {}},
        }
    })
    bad_schema_payload = result_to_payload(bad_schema_check)
    assert bad_schema_payload["status"] == "fail"
    assert any(issue["property_name"] == "影片名稱" for issue in bad_schema_payload["issues"])
    fake_client = FakeNotionCreatePageClient()
    CompletionWritebackSink(data_source_id="fake-database-id").send_event(fake_client, events[0])
    assert fake_client.created_pages[0]["data_source_id"] == "fake-data-source-id"
    assert fake_client.created_pages[0]["properties"]["影片名稱"]["title"][0]["text"]["content"] == "lecture_part_1.mp4"
    result = flush_outbox(outbox, CompletionWritebackSink(data_source_id=None), dry_run=True)
    assert result.dry_run
    assert result.remaining == 1

    assert redact_secret(None) is None
    assert redact_secret("abcd1234xyz") == "abcd...4xyz"
    assert redact_identifier("1234567890abcdef") == "123456...abcdef"
    status_config = AppConfig(
        progress_cache=tmp_dir / "settings_status_progress.json",
        subtitle_dir=tmp_dir / "settings_status_subtitles",
        writeback_outbox=tmp_dir / "settings_status_outbox.jsonl",
        resolved_url_cache=tmp_dir / "settings_status_resolved_url_cache.json",
    )
    missing_settings_path = tmp_dir / "missing_local_settings.json"
    settings_status = collect_settings_status(status_config, local_settings_path=missing_settings_path)
    assert settings_status["settings_path"]
    assert settings_status["sync_backend"] in {"official_notion_api", "notion_mcp_fallback"}
    assert settings_status["planned_sync_backend"] == settings_status["sync_backend"]
    assert isinstance(settings_status["last_sync"], dict)
    assert settings_status["attachment_resolution"] in {"enabled", "disabled_missing_token"}
    assert settings_status["writeback_mode"] in {"apply_possible", "dry_run_only"}
    assert isinstance(settings_status["next_actions"], list)
    guide = build_setup_guide(settings_status)
    guide_text = guide.to_text()
    assert "可複製命令：" in guide_text
    assert "set_token.py" in guide_text
    assert "set_completion_database.py" in guide_text
    assert "check_writeback_schema.py" in guide_text
    settings_path = tmp_dir / "local_settings_actions.json"
    set_notion_token("secret-token", settings_path)
    set_completion_data_source("https://notion.local/1234567890abcdef1234567890abcdef", settings_path)
    set_schedule_view_url("https://notion.local/schedule", settings_path)
    action_settings = load_local_settings(settings_path)
    assert action_settings.notion_token == "secret-token"
    assert action_settings.completion_database_id == "1234567890abcdef1234567890abcdef"
    assert action_settings.schedule_view_url == "https://notion.local/schedule"
    action_status = collect_settings_status(status_config, local_settings_path=settings_path)
    assert action_status["settings_path"].endswith("local_settings_actions.json")
    assert action_status["notion_token"]["status"] == "configured"
    assert action_status["notion_token"]["source"].endswith("local_settings_actions.json")
    assert action_status["completion_data_source"]["status"] == "configured"
    assert action_status["schedule_view"]["source"].endswith("local_settings_actions.json")
    assert action_status["schedule_view"]["redacted_id"] == "https:...hedule"
    readiness_report = collect_mvp_readiness(status_config, local_settings_path=missing_settings_path)
    assert readiness_report.overall_status in {"external_setup_required", "usable_with_warnings", "ready_for_real_notion_trial"}
    readiness_payload = readiness_report.to_json()
    assert "gates" in readiness_payload
    assert any(gate["key"] == "playback_core" for gate in readiness_payload["gates"])
    assert any(gate["key"] == "subtitle_files" for gate in readiness_payload["gates"])
    assert any(gate["key"] == "notion_token" for gate in readiness_payload["gates"])

    print("smoke PASS")
    return 0


class FakeNotionCreatePageClient:
    def __init__(self) -> None:
        self.created_pages = []

    def retrieve_database(self, database_id: str) -> dict[str, object]:
        assert database_id == "fake-database-id"
        return {"data_sources": [{"id": "fake-data-source-id"}]}

    def create_page(self, data_source_id: str, properties: dict[str, object]) -> dict[str, object]:
        self.created_pages.append({"data_source_id": data_source_id, "properties": properties})
        return {"id": "fake-page-id"}


def write_probe_wav(path: Path, duration_sec: float = 1.0, sample_rate: int = 16_000) -> None:
    sample_count = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * sample_count)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from m1_player.app_qt import (  # noqa: E402
    M1MakeupPlayerWindow,
    subtitle_cues_need_generation,
    writeback_count_text,
    writeback_status_text,
)
from m1_player.attachment_resolver import AttachmentResolution, NotionAttachmentResolver  # noqa: E402
from m1_player.config import AppConfig  # noqa: E402
from m1_player.local_settings import load_local_settings  # noqa: E402
from m1_player.models import LessonStatus, PlaybackRecord  # noqa: E402
from m1_player.playability import evaluate_playability  # noqa: E402
from m1_player.subtitle import parse_srt_or_vtt  # noqa: E402
from m1_player.subtitle_generation import SubtitleGenerationResult  # noqa: E402
from m1_player.video_source import parse_video_source  # noqa: E402
from m1_player.writeback_sink import FlushResult  # noqa: E402


class FakePlaybackCore:
    def __init__(self) -> None:
        self.loaded_url: str | None = None
        self.load_calls: list[str] = []
        self.load_at_calls: list[tuple[str, float]] = []
        self.stop_count = 0
        self.seeked_to: float | None = None
        self.position: float | None = 0.0
        self.duration: float | None = 120.0
        self.toggle_count = 0
        self.play_count = 0
        self.pause_count = 0
        self.window_ids: list[int] = []
        self.speed = 1.0
        self.speed_calls: list[float] = []
        self.fullscreen_calls: list[bool] = []
        self.subtitle_paths: list[str] = []
        self.subtitle_visibility_calls: list[bool] = []
        self.caption_calls: list[tuple[str, int]] = []
        self.idle_active = False
        self.path_loaded = True

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return "fake playback core"

    def set_window_id(self, window_id: int) -> None:
        self.window_ids.append(int(window_id))

    def load(self, url: str) -> None:
        self.loaded_url = url
        self.load_calls.append(url)

    def load_at(self, url: str, position_sec: float) -> None:
        self.loaded_url = url
        self.load_at_calls.append((url, float(position_sec)))
        self.position = float(position_sec)

    def stop(self) -> None:
        self.stop_count += 1
        self.position = 0.0

    def play(self) -> None:
        self.play_count += 1
        return

    def pause(self) -> None:
        self.pause_count += 1
        return

    def toggle_pause(self) -> None:
        self.toggle_count += 1
        return

    def seek(self, position_sec: float) -> None:
        self.seeked_to = float(position_sec)
        self.position = float(position_sec)

    def set_speed(self, speed: float) -> None:
        self.speed = float(speed)
        self.speed_calls.append(float(speed))

    def set_fullscreen(self, enabled: bool) -> None:
        self.fullscreen_calls.append(bool(enabled))

    def load_subtitle(self, subtitle_path: str) -> None:
        self.subtitle_paths.append(str(subtitle_path))

    def set_subtitle_visible(self, enabled: bool) -> None:
        self.subtitle_visibility_calls.append(bool(enabled))

    def show_caption(self, text: str, duration_ms: int = 1400) -> None:
        self.caption_calls.append((text, int(duration_ms)))

    def position_sec(self) -> float | None:
        return self.position

    def duration_sec(self) -> float | None:
        return self.duration

    def status_snapshot(self) -> dict[str, object]:
        return {
            "available": True,
            "pause": False,
            "idle_active": self.idle_active,
            "core_idle": self.idle_active,
            "path_loaded": self.path_loaded,
            "time_pos": self.position,
            "duration": self.duration,
        }

    def close(self) -> None:
        return


def main() -> int:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        config = AppConfig(
            progress_cache=temp / "progress_cache.json",
            subtitle_dir=temp / "subtitles",
            writeback_outbox=temp / "outbox.jsonl",
            resolved_url_cache=temp / "resolved_url_cache.json",
            max_pages=0,
        )
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

        fake_core = FakePlaybackCore()
        window = M1MakeupPlayerWindow(
            config,
            playback_core=fake_core,
            attachment_resolver=NotionAttachmentResolver(
                token="fake-token",
                cache=None,
                block_fetcher=fake_block_fetcher,
            ),
            local_settings_path=str(temp / "local_settings.json"),
        )
        assert window.windowTitle() == "BDDE38補課系統 by RRK"
        assert fake_core.window_ids
        assert fake_core.window_ids[0] > 0
        assert window.sync_button.text() == "重新同步"
        assert window.preflight_button.text() == "重新檢查"
        assert window.api_settings_button.text() == "API 設定精靈"
        assert window.set_token_button.text() == "設定 token"
        assert window.set_completion_source_button.text() == "設定完成庫"
        assert window.set_schedule_view_button.text() == "設定課表"
        assert window.play_button.text() == "▶"
        assert window.play_button.toolTip() == "播放"
        assert window.restart_button.text() == "⏮ 00:00"
        assert window.rewind_button.text() == "⏪"
        assert window.forward_button.text() == "⏩"
        assert window.fullscreen_button.text() == "⛶"
        assert window.cc_button.menu() is not None
        assert window.cc_button.toolTip() == "字幕：自動"
        assert window.complete_button.text() == "標記完成"
        assert window.subtitle_placeholder_button.text() == "建立字幕佔位"
        assert window.subtitle_placeholder_button.isHidden()
        assert window.speed_combo.currentText() == "1x"
        assert window.speed_combo.findData(8.0) >= 0
        assert window.subtitle_generate_button.text() == "生成字幕"
        assert window.subtitle_generate_button.isHidden()
        assert window.subtitle_progress_label.isHidden()
        assert window.subtitle_progress_bar.isHidden()
        assert window.flush_writeback_button.text() == "送出完成紀錄"
        assert window.writeback_count_label.text() == "待送出完成紀錄：0"
        assert "完成回寫：乾跑模式" in window.writeback_summary_box.toPlainText()
        assert "待送出事件：0" in window.writeback_summary_box.toPlainText()
        assert "影片總數：0" in window.progress_overview_box.toPlainText()
        assert "待回寫完成紀錄：0" in window.progress_overview_box.toPlainText()
        assert window.pending_seek_sec is None
        assert window.position_time_label.text() == "00:00 / --:--"
        assert window.subtitle_box is not None
        assert window.active_subtitle_label.text() == "尚未載入字幕"
        assert window.caption_overlay.isHidden()
        assert window.detail_box.toPlainText() == "尚未選取影片"
        assert window.list_widget is not None
        assert window.log_box.isHidden()
        readiness_text = window.readiness_box.toPlainText()
        assert "MVP 狀態：需要外部設定" in readiness_text
        assert "BLOCKED notion_token" in readiness_text
        assert "下一步：" in readiness_text
        assert "外部設定導引：" in readiness_text
        assert "可複製命令：" in readiness_text
        assert "set_token.py" in readiness_text
        assert "set_completion_database.py" in readiness_text
        startup_log = window.log_box.toPlainText()
        assert "readiness overall:" in startup_log
        assert "readiness blocked:" in startup_log
        assert "notion_token" in startup_log
        assert "subtitle_files" in startup_log
        wizard = window.create_api_settings_dialog()
        assert wizard.windowTitle() == "API 設定精靈"
        assert "Notion token：missing" in wizard.status_box.toPlainText()
        wizard.token_input.setText("wizard-secret-token")
        wizard.schedule_input.setText("https://notion.local/wizard-schedule")
        wizard.completion_input.setText("wizard-completion-data-source")
        wizard.save_settings()
        wizard_settings = load_local_settings(temp / "local_settings.json")
        assert wizard_settings.notion_token == "wizard-secret-token"
        assert wizard_settings.schedule_view_url == "https://notion.local/wizard-schedule"
        assert wizard_settings.completion_database_id == "wizard-completion-data-source"
        assert "wizard-secret-token" not in window.log_box.toPlainText()
        assert window.save_notion_token("secret-token-for-ui-smoke")
        assert window.attachment_resolver.token == "secret-token-for-ui-smoke"
        assert window.save_completion_data_source("completion-data-source-id")
        assert window.save_schedule_view_url("https://notion.local/schedule-view")
        assert window.config.schedule_view_url == "https://notion.local/schedule-view"
        local_settings = load_local_settings(temp / "local_settings.json")
        assert local_settings.notion_token == "secret-token-for-ui-smoke"
        assert local_settings.completion_database_id == "completion-data-source-id"
        assert local_settings.schedule_view_url == "https://notion.local/schedule-view"
        assert "secret-token-for-ui-smoke" not in window.log_box.toPlainText()
        window.attachment_resolver = NotionAttachmentResolver(
            token="fake-token",
            cache=None,
            block_fetcher=fake_block_fetcher,
        )

        record = PlaybackRecord(
            stable_key="course-a:001:test",
            video_name="test-video.mp4",
            course_page_url="https://notion.local/course-a",
            course_date="2026-06-14",
            segment_index=1,
            video_block_ref="https://notion.local/course-a#video-001",
            source_ref="https://example.com/test-video.mp4",
            last_position_sec=42.0,
            status=LessonStatus.IN_PROGRESS,
        )
        previous_session_position = record.last_position_sec
        (config.subtitle_dir / "course-a_001_test.srt").parent.mkdir(parents=True, exist_ok=True)
        (config.subtitle_dir / "course-a_001_test.srt").write_text(
            "1\n00:00:00,000 --> 00:02:00,000\nhello cc\n",
            encoding="utf-8",
        )
        window.store.records[record.stable_key] = record
        window.records = [record]
        window.refresh_list()
        window.list_widget.setCurrentRow(0)
        assert fake_core.loaded_url is None
        assert fake_core.pause_count == 0
        assert "雙擊" in window.playback_hint_label.text()
        assert window.load_record_for_playback(record)
        assert fake_core.loaded_url == "https://example.com/test-video.mp4"
        assert fake_core.pause_count == 1
        assert fake_core.stop_count == 1
        first_video_load_count = len(fake_core.load_calls)
        window.preview_selected_item()
        assert len(fake_core.load_calls) == first_video_load_count
        assert fake_core.pause_count == 1
        assert fake_core.subtitle_paths == []
        assert not window.cc_button.isEnabled()
        assert "播放後會重新解析字幕" in window.subtitle_box.item(0).text()
        assert window.current_subtitle_path is None
        assert window.cues == []
        assert window.position_time_label.text() == "00:00 / 02:00", window.position_time_label.text()
        assert record.last_position_sec == previous_session_position
        window.load_subtitles(record)
        assert fake_core.subtitle_paths[-1].endswith("course-a_001_test.srt")
        assert fake_core.subtitle_visibility_calls[-1] is False
        assert window.cc_button.isEnabled()
        assert window.caption_actions["native"].isEnabled()
        window.set_caption_mode("off")
        assert fake_core.subtitle_visibility_calls[-1] is False
        assert window.caption_overlay.isHidden()
        assert window.cc_button.toolTip() == "字幕：關閉"
        window.set_caption_mode("auto")
        assert fake_core.subtitle_visibility_calls[-1] is False
        window.highlight_subtitle(0.0)
        assert not window.caption_overlay.isHidden()
        window.set_caption_mode("osd")
        assert fake_core.subtitle_visibility_calls[-1] is False
        window.highlight_subtitle(0.0)
        assert fake_core.caption_calls[-1] == ("hello cc", 1600)
        assert window.caption_overlay.isHidden()
        window.set_caption_mode("auto")
        assert fake_core.speed_calls[-1] == 1.0
        window.speed_combo.setCurrentIndex(window.speed_combo.findData(8.0))
        assert fake_core.speed == 8.0
        window.toggle_player_fullscreen()
        assert window.player_fullscreen
        assert window.fullscreen_button.text() == "⛶"
        assert window.fullscreen_button.toolTip() == "離開全螢幕"
        assert window.left_panel.isHidden()
        assert window.detail_box.isHidden()
        assert window.subtitle_box.isHidden()
        assert window.playback_hint_label.isHidden()
        assert window.complete_button.isHidden()
        assert window.flush_writeback_button.isHidden()
        window.toggle_player_fullscreen()
        assert not window.player_fullscreen
        assert window.fullscreen_button.text() == "⛶"
        assert window.fullscreen_button.toolTip() == "切換全螢幕"
        assert not window.left_panel.isHidden()
        assert not window.detail_box.isHidden()
        assert not window.subtitle_box.isHidden()
        assert not window.playback_hint_label.isHidden()
        assert not window.complete_button.isHidden()
        assert not window.flush_writeback_button.isHidden()
        progress_request = window.subtitle_session.request_window(record.stable_key, "test_progress", 0.0, 30.0)
        window.on_subtitle_generation_progress(
            progress_request.generation_id,
            "audio_decode_start",
            5,
            "讀取遠端音訊串流",
        )
        assert not window.subtitle_progress_label.isHidden()
        assert window.subtitle_progress_bar.value() == 5
        assert "讀取遠端音訊串流" in window.subtitle_progress_label.text()
        assert not window.caption_overlay.isHidden()
        assert "讀取遠端音訊串流" in window.caption_overlay.text()
        window.on_subtitle_generation_progress(
            progress_request.generation_id,
            "audio_decode_start",
            5,
            "讀取遠端音訊串流",
        )
        assert not window.caption_overlay.isHidden()
        assert fake_core.caption_calls[-1] == ("hello cc", 1600)
        stale_request = progress_request
        fresh_request = window.subtitle_session.request_window(record.stable_key, "test_progress_fresh", 10.0, 30.0)
        assert not window.subtitle_session.accepts_result(stale_request.generation_id, record.stable_key)
        window.on_subtitle_generation_progress(stale_request.generation_id, "audio_decode_start", 80, "舊任務")
        assert window.subtitle_progress_bar.value() == 5
        window.on_subtitle_generation_progress(fresh_request.generation_id, "inference_segment", -1, "字幕解析中")
        assert window.subtitle_progress_bar.minimum() == 0
        assert window.subtitle_progress_bar.maximum() == 0
        window.cues = parse_srt_or_vtt(
            "1\n00:00:42,000 --> 00:06:00,000\nready for playback\n"
        )
        window.play_selected_item(window.list_widget.currentItem())
        assert fake_core.play_count == 1
        assert window.play_button.text() == "⏸"
        assert window.play_button.toolTip() == "暫停"
        window.toggle_playback()
        assert fake_core.pause_count == 2
        assert window.play_button.text() == "▶"
        window.toggle_playback()
        assert fake_core.play_count == 2
        assert window.play_button.text() == "⏸"
        assert window.pending_seek_sec is None
        window.poll_playback_position()
        assert fake_core.seeked_to is None
        assert window.pending_seek_sec is None
        assert window.position_time_label.text() == "00:00 / 02:00"
        assert "MPV 🟢 ready" in window.state_lights_label.text()
        runtime_prefetches = []
        runtime_worker_starts = []
        window.start_background_subtitle_prefetch = (
            lambda position_sec, trigger="background_prefetch": runtime_prefetches.append((float(position_sec), trigger))
        )
        window._start_subtitle_generation_worker = lambda plan: runtime_worker_starts.append(plan)
        fake_core.position = 350.0
        window.set_player_playing(True)
        window.cues = parse_srt_or_vtt(
            "1\n00:00:00,000 --> 00:06:00,000\nalmost exhausted\n"
        )
        runtime_pause_count = fake_core.pause_count
        runtime_play_count = fake_core.play_count
        window.poll_playback_position()
        assert fake_core.pause_count == runtime_pause_count + 1
        assert not window.player_is_playing
        assert window.pending_autoplay_after_preheat
        assert not window.play_button.isEnabled()
        assert runtime_prefetches[-1] == (350.0, "runtime_gate_background_prefetch")
        assert runtime_worker_starts[-1].request.trigger == "runtime_gate_timeline"
        window.cues = parse_srt_or_vtt(
            "1\n00:05:50,000 --> 00:10:00,000\nready after runtime gate\n"
        )
        window.maybe_autoplay_after_preheat("runtime_smoke")
        assert fake_core.play_count == runtime_play_count + 1
        assert window.player_is_playing
        assert not window.pending_autoplay_after_preheat
        assert window.play_button.isEnabled()
        window.subtitle_controller.reset_for_video()
        fake_core.position = None
        fake_core.duration = None
        fake_core.idle_active = True
        fake_core.path_loaded = False
        window.mpv_idle_started_at = time.time() - 5
        window.mpv_idle_recovery_attempted = True
        window.poll_playback_position()
        assert "MPV 🔴 blocked" in window.state_lights_label.text()
        fake_core.position = 0.0
        fake_core.duration = 120.0
        fake_core.idle_active = False
        fake_core.path_loaded = True
        window.poll_playback_position()
        window.seek_to_slider(60)
        assert fake_core.seeked_to == 60.0
        assert window.position_time_label.text() == "01:00 / 02:00"
        window.jump_relative(-15)
        assert fake_core.seeked_to == 45.0
        window.jump_relative(15)
        assert fake_core.seeked_to == 60.0
        detail_text = window.detail_box.toPlainText()
        assert "影片：test-video.mp4" in detail_text
        assert "播放狀態：ready" in detail_text
        assert "播放進度：01:00 / 02:00（50.00%）" in detail_text
        assert "字幕：course-a_001_test.srt（1 cues）" in detail_text
        assert record.last_position_sec == previous_session_position
        (config.subtitle_dir / "course-a_001_test.srt").unlink()
        record.subtitle_path = ""
        window.current_record.subtitle_path = ""
        window.load_subtitles(window.current_record)
        window.refresh_detail_box()
        assert not window.cc_button.isEnabled()
        window.create_current_subtitle_placeholder()
        placeholder_path = config.subtitle_dir / "course-a_001_test.md"
        assert placeholder_path.exists()
        assert "待補字幕" in placeholder_path.read_text(encoding="utf-8")
        assert window.subtitle_box.count() == 1
        assert "待補字幕" in window.subtitle_box.item(0).text()
        assert window.cc_button.isEnabled()
        assert not window.caption_actions["native"].isEnabled()
        assert "字幕：course-a_001_test.md（1 cues）" in window.detail_box.toPlainText()
        caption_call_count = len(fake_core.caption_calls)
        window.highlight_subtitle(0.0)
        assert window.active_subtitle_label.text() == "待補字幕"
        assert window.caption_overlay.text() == "待補字幕"
        assert len(fake_core.caption_calls) == caption_call_count
        window.toggle_player_fullscreen()
        window.highlight_subtitle(0.0)
        assert window.caption_overlay.isHidden()
        assert fake_core.caption_calls[-1] == ("待補字幕", 1600)
        window.toggle_player_fullscreen()
        assert subtitle_cues_need_generation(window.cues)
        fake_core.position = 60.0
        assert window.current_playback_position_for_subtitles() == 60.0
        assert window.current_playability is not None
        assert window.current_playability.can_play
        worker_starts = []
        background_prefetches = []

        def fake_worker_start(plan: object) -> None:
            worker_starts.append(plan)

        def fake_background_prefetch(position_sec: float, trigger: str = "background_prefetch") -> None:
            background_prefetches.append((float(position_sec), trigger))

        window._start_subtitle_generation_worker = fake_worker_start
        window.start_background_subtitle_prefetch = fake_background_prefetch
        window.subtitle_controller.reset_for_video()
        window.set_player_playing(False)
        preheat_initial_play_count = fake_core.play_count
        window.toggle_playback()
        assert len(worker_starts) == 1
        assert worker_starts[0].request.trigger == "initial_handshake_timeline"
        assert worker_starts[0].request.start_sec == 55.0
        assert worker_starts[0].request.max_duration_sec == 180.0
        assert worker_starts[0].options.overwrite is True
        assert worker_starts[0].request.generation_id == window.subtitle_session.active_request.generation_id
        assert worker_starts[0].options.start_sec == 55.0
        first_request_id = worker_starts[0].request.generation_id
        assert background_prefetches[-1] == (60.0, "initial_handshake_background_prefetch")
        assert fake_core.play_count == preheat_initial_play_count
        assert window.pending_autoplay_after_preheat
        assert not window.play_button.isEnabled()
        window.pending_playback_speed = None
        window.speed_combo.setCurrentIndex(window.speed_combo.findData(1.0))
        window.last_subtitle_generation_result = SubtitleGenerationResult(
            record_key=record.stable_key,
            status="generated",
            subtitle_path=str(config.subtitle_dir / "capacity_probe.srt"),
            cue_count=60,
            elapsed_sec=64.0,
            message="capacity probe",
            audio_duration_sec=180.0,
            processing_capacity_ratio=2.8,
        )
        assert window.speculative_subtitle_playback_speed() == 4.0
        assert window.desired_background_subtitle_workers() == 1
        assert window.subtitle_capacity_readiness()["planned_worker_count"] == 2
        window.cues = parse_srt_or_vtt(
            "1\n00:00:55,000 --> 00:04:30,000\nready ahead\n"
        )
        window.maybe_autoplay_after_preheat("ui_smoke")
        assert fake_core.play_count == preheat_initial_play_count + 1
        assert not window.pending_autoplay_after_preheat
        assert window.play_button.isEnabled()
        window.speed_combo.setCurrentIndex(window.speed_combo.findData(1.0))
        assert fake_core.speed == 1.0
        window.cues = []
        window.speed_combo.setCurrentIndex(window.speed_combo.findData(8.0))
        assert fake_core.speed == 1.0
        assert window.pending_playback_speed == 8.0
        assert window.desired_background_subtitle_workers() == 2
        assert window.subtitle_capacity_readiness()["planned_worker_count"] == 3
        assert window.speed_warmup_status == "warming"
        assert background_prefetches[-1][1] == "speed_change_background_prefetch"
        assert "🟡" in window.status_label.text()
        assert "字幕預熱中" in window.status_label.text()
        window.speed_change_warmup_timer.stop()
        window.pending_playback_speed_deadline = 0.0
        window.maybe_apply_pending_playback_speed()
        assert fake_core.speed == 1.0
        assert window.pending_playback_speed == 8.0
        fake_core.position = 0.0
        window.cues = parse_srt_or_vtt(
            "1\n00:00:00,000 --> 00:02:00,000\nready ahead\n"
        )
        window.maybe_apply_pending_playback_speed()
        assert fake_core.speed == 8.0
        assert window.pending_playback_speed is None
        assert window.speed_warmup_status == "ready"
        assert "🟢" in window.status_label.text()
        window.subtitle_controller.reset_for_video()
        worker_starts.clear()
        window.cues = []
        running_plan = window.subtitle_controller.timeline_plan(
            record.stable_key,
            [],
            60.0,
            trigger="running_before_seek",
            force=True,
        )
        assert running_plan is not None
        assert window.subtitle_controller.dispatch_plan(running_plan).action == "start"
        urgent_starts = []

        def fake_urgent_background_start(start_sec: float, max_duration_sec: float, trigger: str) -> None:
            urgent_starts.append((float(start_sec), float(max_duration_sec), trigger))

        window._start_background_subtitle_generation_worker = fake_urgent_background_start
        window.background_subtitle_worker_cap = lambda: 4
        window.background_subtitle_generation_jobs.clear()
        window.background_subtitle_generation_starts.clear()
        window.seek_to_slider(90)
        assert len(worker_starts) == 0
        assert urgent_starts[-1] == (85.0, 180.0, "seek_urgent_background")
        assert window.subtitle_controller.deferred_plan is not None
        assert window.subtitle_controller.deferred_plan.request.trigger == "seek_timeline"
        assert window.subtitle_controller.deferred_plan.request.start_sec == 85.0
        assert window.subtitle_controller.deferred_plan.request.max_duration_sec == 180.0
        window.seek_to_slider(100)
        assert len(worker_starts) == 0
        assert urgent_starts[-1] == (95.0, 180.0, "seek_urgent_background")
        latest_deferred = window.subtitle_controller.deferred_plan
        assert latest_deferred is not None
        assert latest_deferred.request.trigger == "seek_timeline"
        assert latest_deferred.request.start_sec == 95.0
        assert latest_deferred.request.generation_id > running_plan.request.generation_id
        assert latest_deferred.options.output_stem_suffix == f"g{latest_deferred.request.generation_id:05d}"
        assert not window.subtitle_session.accepts_result(running_plan.request.generation_id, record.stable_key)
        stale_subtitle = config.subtitle_dir / "stale_generation.srt"
        stale_subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nstale\n", encoding="utf-8", newline="\n")
        stale_label = window.active_subtitle_label.text()
        window.on_subtitle_generation_finished(
            running_plan.request.generation_id,
            SubtitleGenerationResult(
                record_key=record.stable_key,
                status="generated",
                subtitle_path=str(stale_subtitle),
                cue_count=1,
                elapsed_sec=0.1,
                message="stale generated",
            ),
        )
        assert not stale_subtitle.exists()
        assert window.active_subtitle_label.text() == stale_label
        next_plan = window.subtitle_controller.finish_running_generation(
            running_plan.request.generation_id,
            current_record_key=record.stable_key,
        )
        assert next_plan == latest_deferred
        window._start_subtitle_generation_worker(next_plan)
        window.cleanup_subtitle_generation_worker(running_plan.request.generation_id)
        app.processEvents()
        assert len(worker_starts) == 1
        assert worker_starts[0] == latest_deferred
        window.restart_current_video()
        assert fake_core.load_at_calls[-1] == ("https://example.com/test-video.mp4", 0.0)
        assert fake_core.play_count == 4
        assert window.position_time_label.text() == "00:00 / 02:00"
        restart_plan = worker_starts[-1]
        assert restart_plan.request.trigger == "restart_timeline"
        assert restart_plan.request.start_sec == 0.0

        window.mark_current_completed()
        assert window.current_record is not None
        assert window.current_record.status == LessonStatus.COMPLETED
        completed_detail = window.detail_box.toPlainText()
        assert "補課狀態：已完成" in completed_detail
        assert "播放進度：02:00 / 02:00（100.00%）" in completed_detail
        assert "完成：1（100.00%）" in window.progress_overview_box.toPlainText()
        assert config.writeback_outbox.exists()
        assert window.writeback_count_label.text() == "待送出完成紀錄：1"
        assert "待送出事件：1" in window.writeback_summary_box.toPlainText()
        assert "待回寫完成紀錄：1" in window.progress_overview_box.toPlainText()
        assert "2026-06-14 P01 test-video.mp4" in window.writeback_summary_box.toPlainText()
        window.mark_current_completed()
        assert window.writeback_count_label.text() == "待送出完成紀錄：1"
        assert "completion already_completed:" in window.log_box.toPlainText()
        assert writeback_count_text(3) == "待送出完成紀錄：3"
        assert writeback_status_text(FlushResult(0, 0, 0, False, "outbox empty")) == "沒有待送出的完成紀錄"
        assert (
            writeback_status_text(FlushResult(1, 0, 1, True, "notion_token missing"))
            == "完成紀錄尚未送出，請看事件欄"
        )

        notion_record = PlaybackRecord(
            stable_key="course-a:002:notion",
            video_name="notion-video.mp4",
            course_page_url="https://notion.local/course-a",
            course_date="2026-06-14",
            segment_index=2,
            video_block_ref="https://notion.local/course-a#video-002",
            source_ref=_notion_attachment_ref(),
            status=LessonStatus.NOT_STARTED,
        )
        window.store.records[notion_record.stable_key] = notion_record
        window.records = [record, notion_record]
        window.refresh_list()
        window.list_widget.setCurrentRow(1)
        assert fetched_blocks == []
        window.play_selected_item(window.list_widget.item(1))
        assert fetched_blocks == ["block-id"]
        assert fake_core.loaded_url == "https://example.com/notion-signed-video.mp4"
        assert "resolved through Notion API block fetch" in window.log_box.toPlainText()
        notion_detail = window.detail_box.toPlainText()
        assert "影片來源：notion_attachment_marker" in notion_detail
        assert "解析狀態：resolved - resolved through Notion API block fetch" in notion_detail
        assert "播放狀態：ready" in notion_detail
        assert "載入提示：正在解析 Notion 串流" in notion_detail
        assert "等待 duration / position 出現" in window.player_label.text()
        assert "影片總數：2" in window.progress_overview_box.toPlainText()
        assert "未開始：1" in window.progress_overview_box.toPlainText()

        local_url_source = parse_video_source(_notion_attachment_ref("local-block", "local-video.mp4"))
        blocked = evaluate_playability(
            video_name="local-video.mp4",
            source=local_url_source,
            resolution=AttachmentResolution(r"C:\videos\downloaded.mp4", "resolved", "bad local resolver result"),
            playback_available=True,
            playback_description="fake playback core",
        )
        assert blocked.state == "blocked_non_stream_url"
        assert not blocked.can_play
        assert blocked.playable_url is None

        bad_record = PlaybackRecord(
            stable_key="course-a:003:bad-local",
            video_name="bad-local.mp4",
            course_page_url="https://notion.local/course-a",
            course_date="2026-06-14",
            segment_index=3,
            video_block_ref="https://notion.local/course-a#video-003",
            source_ref=_notion_attachment_ref("bad-block", "bad-local.mp4"),
            status=LessonStatus.NOT_STARTED,
        )

        def bad_block_fetcher(block_id: str) -> dict[str, object]:
            return {
                "id": block_id,
                "type": "video",
                "video": {
                    "type": "file",
                    "file": {
                        "url": r"C:\videos\downloaded.mp4",
                        "expiry_time": "2099-01-01T00:00:00Z",
                    },
                },
            }

        load_count_before_bad_record = len(fake_core.load_calls)
        window.attachment_resolver = NotionAttachmentResolver(
            token="fake-token",
            cache=None,
            block_fetcher=bad_block_fetcher,
        )
        window.store.records[bad_record.stable_key] = bad_record
        window.records = [record, notion_record, bad_record]
        window.refresh_list()
        window.list_widget.setCurrentRow(2)
        window.play_selected_item(window.list_widget.item(2))
        assert len(fake_core.load_calls) == load_count_before_bad_record
        assert fake_core.loaded_url == "https://example.com/notion-signed-video.mp4"
        assert "blocked non-stream playable URL" in window.log_box.toPlainText()
        assert "播放狀態：blocked_non_stream_url" in window.detail_box.toPlainText()

        window.close()
        assert_playability_statuses()
    app.processEvents()
    print("ui smoke PASS")
    return 0


def assert_playability_statuses() -> None:
    http_source = parse_video_source("https://example.com/video.mp4")
    ready = evaluate_playability(
        video_name="video.mp4",
        source=http_source,
        resolution=AttachmentResolution(None, "not_applicable", "not needed"),
        playback_available=True,
        playback_description="fake playback core",
    )
    assert ready.state == "ready"
    assert ready.can_play
    assert ready.playable_url == "https://example.com/video.mp4"
    assert ready.loading_hint is None

    no_player = evaluate_playability(
        video_name="video.mp4",
        source=http_source,
        resolution=AttachmentResolution(None, "not_applicable", "not needed"),
        playback_available=False,
        playback_description="mpv unavailable",
    )
    assert no_player.state == "playback_unavailable"
    assert not no_player.can_play
    assert "mpv unavailable" in no_player.player_label

    notion_source = parse_video_source(_notion_attachment_ref())
    missing_token = evaluate_playability(
        video_name="notion-video.mp4",
        source=notion_source,
        resolution=AttachmentResolution(None, "missing_token", "token missing"),
        playback_available=True,
        playback_description="fake playback core",
    )
    assert missing_token.state == "notion_token_required"
    assert not missing_token.can_play
    assert "Notion API token" in missing_token.player_label
    assert any("missing_token" in line for line in missing_token.log_lines)
    resolved_notion = evaluate_playability(
        video_name="notion-video.mp4",
        source=notion_source,
        resolution=AttachmentResolution("https://example.com/notion-signed-video.mp4", "resolved", "ok"),
        playback_available=True,
        playback_description="fake playback core",
    )
    assert resolved_notion.state == "ready"
    assert resolved_notion.loading_hint
    assert "Notion 串流" in resolved_notion.player_label


def _notion_attachment_ref(block_id: str = "block-id", filename: str = "notion-video.mp4") -> str:
    payload = {
        "source": f"attachment:{block_id}:{filename}",
        "permissionRecord": {"table": "block", "id": block_id},
    }
    return "file://" + quote(json.dumps(payload, separators=(",", ":")))


if __name__ == "__main__":
    exit_code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)

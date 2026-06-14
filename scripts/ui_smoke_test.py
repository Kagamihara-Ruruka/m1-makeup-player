from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from m1_player.app_qt import M1MakeupPlayerWindow, writeback_count_text, writeback_status_text  # noqa: E402
from m1_player.attachment_resolver import AttachmentResolution, NotionAttachmentResolver  # noqa: E402
from m1_player.config import AppConfig  # noqa: E402
from m1_player.local_settings import load_local_settings  # noqa: E402
from m1_player.models import LessonStatus, PlaybackRecord  # noqa: E402
from m1_player.playability import evaluate_playability  # noqa: E402
from m1_player.video_source import parse_video_source  # noqa: E402
from m1_player.writeback_sink import FlushResult  # noqa: E402


class FakePlaybackCore:
    def __init__(self) -> None:
        self.loaded_url: str | None = None
        self.load_calls: list[str] = []
        self.seeked_to: float | None = None
        self.position = 0.0
        self.duration = 120.0
        self.toggle_count = 0

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return "fake playback core"

    def load(self, url: str) -> None:
        self.loaded_url = url
        self.load_calls.append(url)

    def play(self) -> None:
        return

    def pause(self) -> None:
        return

    def toggle_pause(self) -> None:
        self.toggle_count += 1
        return

    def seek(self, position_sec: float) -> None:
        self.seeked_to = float(position_sec)
        self.position = float(position_sec)

    def position_sec(self) -> float | None:
        return self.position

    def duration_sec(self) -> float | None:
        return self.duration

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
        assert window.windowTitle() == "m_1 Notion 補課播放器"
        assert window.sync_button.text() == "重新同步"
        assert window.preflight_button.text() == "重新檢查"
        assert window.set_token_button.text() == "設定 token"
        assert window.set_completion_source_button.text() == "設定完成庫"
        assert window.set_schedule_view_button.text() == "設定課表"
        assert window.play_button.text() == "播放 / 暫停"
        assert window.complete_button.text() == "標記完成"
        assert window.subtitle_placeholder_button.text() == "建立字幕佔位"
        assert window.subtitle_placeholder_button.isHidden()
        assert window.subtitle_generate_button.text() == "生成字幕"
        assert window.flush_writeback_button.text() == "送出完成紀錄"
        assert window.writeback_count_label.text() == "待送出完成紀錄：0"
        assert "完成回寫：乾跑模式" in window.writeback_summary_box.toPlainText()
        assert "待送出事件：0" in window.writeback_summary_box.toPlainText()
        assert "影片總數：0" in window.progress_overview_box.toPlainText()
        assert "待回寫完成紀錄：0" in window.progress_overview_box.toPlainText()
        assert window.pending_seek_sec is None
        assert window.subtitle_box is not None
        assert window.active_subtitle_label.text() == "尚未載入字幕"
        assert window.detail_box.toPlainText() == "尚未選取影片"
        assert window.list_widget is not None
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
        window.store.records[record.stable_key] = record
        window.records = [record]
        window.refresh_list()
        window.list_widget.setCurrentRow(0)
        assert fake_core.loaded_url == "https://example.com/test-video.mp4"
        assert window.pending_seek_sec == 42.0
        window.poll_playback_position()
        assert fake_core.seeked_to == 42.0
        assert window.pending_seek_sec is None
        detail_text = window.detail_box.toPlainText()
        assert "影片：test-video.mp4" in detail_text
        assert "播放狀態：ready" in detail_text
        assert "播放進度：00:42 / 02:00（35.00%）" in detail_text
        assert "字幕：缺少本地字幕" in detail_text
        overview_text = window.progress_overview_box.toPlainText()
        assert "影片總數：1" in overview_text
        assert "補課中：1" in overview_text
        assert "平均進度：35.00%" in overview_text
        window.create_current_subtitle_placeholder()
        placeholder_path = config.subtitle_dir / "course-a_001_test.md"
        assert placeholder_path.exists()
        assert "待補字幕" in placeholder_path.read_text(encoding="utf-8")
        assert window.subtitle_box.count() == 1
        assert "待補字幕" in window.subtitle_box.item(0).text()
        assert "字幕：course-a_001_test.md（1 cues）" in window.detail_box.toPlainText()
        window.highlight_subtitle(0.0)
        assert window.active_subtitle_label.text() == "待補字幕"
        timeline_triggers = []
        window.start_subtitle_generation = lambda trigger="manual": timeline_triggers.append(trigger)
        window.toggle_playback()
        assert timeline_triggers == ["playback_timeline"]
        assert fake_core.toggle_count == 1

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
    raise SystemExit(main())

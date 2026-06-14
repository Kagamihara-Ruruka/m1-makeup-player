from __future__ import annotations

import json
import math
import os
import sys
import time

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .config import AppConfig
from .attachment_resolver import AttachmentResolution, NotionAttachmentResolver
from .completion import queue_completion_event
from .models import LessonStatus, PlaybackRecord
from .mvp_readiness import collect_mvp_readiness
from .playback import PlaybackCore, create_default_playback_core
from .playability import PlayabilityStatus, evaluate_playability
from .preflight import run_preflight
from .progress_overview import collect_progress_overview
from .progress import ProgressStore
from .readiness_summary import readiness_display_text
from .resolved_url_cache import ResolvedUrlCache
from .runtime_config import load_app_config
from .settings_actions import set_completion_data_source, set_notion_token, set_schedule_view_url
from .settings_status import collect_settings_status
from .subtitle import SubtitleCue, active_cue, load_subtitle
from .subtitle_controller import (
    SubtitleController,
    SubtitleGenerationPlan,
    subtitle_cues_need_generation,
)
from .subtitle_generation import SubtitleGenerationOptions, SubtitleGenerationResult, generate_subtitle_sidecar
from .subtitle_manifest import write_missing_markdown_placeholders
from .subtitle_merge import merge_subtitle_files
from .subtitle_pipeline_planner import RollingPipelinePlan, plan_rolling_subtitle_pipeline
from .subtitle_resolver import SubtitleResolver, safe_filename_stem
from .subtitle_session import SubtitleWindowRequest
from .sync_service import NotionScheduleSync, SyncResult
from .video_detail_summary import build_video_detail_summary
from .video_source import VideoSourceInfo, parse_video_source
from .writeback import WritebackOutbox
from .writeback_summary import collect_writeback_outbox_summary
from .writeback_sink import CompletionWritebackSink, FlushResult, flush_outbox


try:
    from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
    from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QDialog,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMenu,
        QProgressBar,
        QPushButton,
        QSlider,
        QSplitter,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - exercised only without optional UI dependency
    raise SystemExit(
        "PySide6 尚未安裝。請先執行："
        "D:\\RRKAL_tools\\m1-makeup-player\\.venv\\Scripts\\python.exe "
        "-m pip install -r D:\\RRKAL_tools\\m1-makeup-player\\requirements.txt"
    ) from exc


class PlayerDoubleClickOverlay(QWidget):
    double_clicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background: transparent; border: none;")
        self.setToolTip("雙擊切換全螢幕")

    def mouseDoubleClickEvent(self, event: object) -> None:  # noqa: N802 - Qt override name.
        self.double_clicked.emit()
        accept = getattr(event, "accept", None)
        if callable(accept):
            accept()


class PlayerCaptionOverlay(QLabel):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(
            "QLabel {"
            "background: rgba(0, 0, 0, 180);"
            "color: white;"
            "border-radius: 6px;"
            "padding: 8px 14px;"
            "font-size: 22px;"
            "font-weight: 600;"
            "}"
        )
        self.setHidden(True)


class PlayerSurface(QLabel):
    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event: object) -> None:  # noqa: N802 - Qt override name.
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event: object) -> None:  # noqa: N802 - Qt override name.
        super().resizeEvent(event)
        for child in self.findChildren(PlayerCaptionOverlay):
            width = max(260, self.width() - 96)
            height = min(150, max(58, int(self.height() * 0.2)))
            x_pos = max(12, int((self.width() - width) / 2))
            y_pos = max(12, self.height() - height - 34)
            child.setGeometry(x_pos, y_pos, width, height)
            child.raise_()
        for child in self.findChildren(PlayerDoubleClickOverlay):
            child.setGeometry(self.rect())
            child.raise_()


class SeekSlider(QSlider):
    seek_requested = Signal(int)

    def mousePressEvent(self, event: object) -> None:  # noqa: N802 - Qt override name.
        button = getattr(event, "button", lambda: None)()
        if button == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            point = getattr(event, "position", lambda: None)()
            if point is None:
                point = getattr(event, "pos", lambda: None)()
            x_value = float(point.x()) if point is not None else 0.0
            ratio = max(0.0, min(1.0, x_value / max(1, self.width())))
            value = int(round(self.minimum() + ratio * (self.maximum() - self.minimum())))
            self.setValue(value)
            self.seek_requested.emit(value)
            accept = getattr(event, "accept", None)
            if callable(accept):
                accept()
            return
        super().mousePressEvent(event)


class SyncWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, config: AppConfig, local_settings_path: str | None = None) -> None:
        super().__init__()
        self.config = config
        self.local_settings_path = local_settings_path

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(NotionScheduleSync(self.config, self.local_settings_path).sync())
        except Exception as exc:  # noqa: BLE001 - UI boundary reports the failure.
            self.failed.emit(str(exc))


class WritebackFlushWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, config: AppConfig, local_settings_path: str | None = None) -> None:
        super().__init__()
        self.config = config
        self.local_settings_path = local_settings_path

    @Slot()
    def run(self) -> None:
        try:
            outbox = WritebackOutbox(self.config.writeback_outbox)
            sink = CompletionWritebackSink(local_settings_path=self.local_settings_path)
            self.finished.emit(flush_outbox(outbox, sink, dry_run=False, local_settings_path=self.local_settings_path))
        except Exception as exc:  # noqa: BLE001 - UI boundary reports the failure.
            self.failed.emit(str(exc))


class SubtitleGenerationWorker(QObject):
    progress = Signal(int, str, int, str)
    finished = Signal(int, object)
    failed = Signal(int, str)

    def __init__(
        self,
        generation_id: int,
        record: PlaybackRecord,
        media_ref: str,
        subtitle_dir: str,
        options: SubtitleGenerationOptions,
    ) -> None:
        super().__init__()
        self.generation_id = int(generation_id)
        self.record = record
        self.media_ref = media_ref
        self.subtitle_dir = subtitle_dir
        self.options = options

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(
                self.generation_id,
                generate_subtitle_sidecar(
                    self.record,
                    self.media_ref,
                    self.subtitle_dir,
                    self.options,
                    progress_callback=self.emit_progress,
                )
            )
        except Exception as exc:  # noqa: BLE001 - UI boundary reports the failure.
            self.failed.emit(self.generation_id, str(exc))

    def emit_progress(self, stage: str, percent: float | None, message: str) -> None:
        percent_value = -1 if percent is None else max(0, min(100, int(round(percent))))
        self.progress.emit(self.generation_id, stage, percent_value, message)


class ApiSettingsDialog(QDialog):
    def __init__(self, owner: "M1MakeupPlayerWindow") -> None:
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("API 設定精靈")
        self.resize(720, 520)

        intro = QLabel(
            "一般版需要連到你的 Notion。請在這裡設定 API token、課程安排 view，"
            "以及可選的補課完成紀錄 data source。"
        )
        intro.setWordWrap(True)

        self.status_box = QTextEdit()
        self.status_box.setReadOnly(True)
        self.status_box.setMinimumHeight(150)

        self.token_input = QLineEdit()
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_input.setPlaceholderText("ntn_... 或 secret_...")

        self.schedule_input = QLineEdit()
        self.schedule_input.setPlaceholderText("貼上 Notion 課程安排 database view URL")

        self.completion_input = QLineEdit()
        self.completion_input.setPlaceholderText("貼上補課完成紀錄 data source URL 或 id，可先留空")

        self.save_button = QPushButton("保存設定")
        self.refresh_button = QPushButton("重新檢查")
        self.sync_button = QPushButton("同步課表")
        self.close_button = QPushButton("關閉")

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(QLabel("目前狀態"))
        layout.addWidget(self.status_box)
        layout.addWidget(QLabel("Notion API token"))
        layout.addWidget(self.token_input)
        layout.addWidget(QLabel("課程安排 view URL"))
        layout.addWidget(self.schedule_input)
        layout.addWidget(QLabel("補課完成紀錄 data source"))
        layout.addWidget(self.completion_input)

        buttons = QWidget()
        buttons_layout = QHBoxLayout(buttons)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.addWidget(self.save_button)
        buttons_layout.addWidget(self.refresh_button)
        buttons_layout.addWidget(self.sync_button)
        buttons_layout.addWidget(self.close_button)
        layout.addWidget(buttons)

        self.save_button.clicked.connect(self.save_settings)
        self.refresh_button.clicked.connect(self.refresh_status)
        self.sync_button.clicked.connect(self.start_sync)
        self.close_button.clicked.connect(self.accept)
        self.refresh_status()

    def refresh_status(self) -> None:
        status = collect_settings_status(self.owner.config, self.owner.local_settings_path)
        token = status["notion_token"]
        schedule = status["schedule_view"]
        completion = status["completion_data_source"]
        lines = [
            f"設定檔：{status['settings_path']}",
            f"Notion token：{token['status']} ({token['source']})",
            f"課程安排 view：{schedule['status']} ({schedule['redacted_id'] or 'missing'})",
            f"完成紀錄 data source：{completion['status']} ({completion['redacted_id'] or 'missing'})",
            f"同步路徑：{status['planned_sync_backend']}",
            f"回寫模式：{status['writeback_mode']}",
            "",
            "說明：token 只會存在本機設定檔或環境變數，不會寫入 Git，也不會印在事件紀錄。",
            "最低可用設定是 token 加課程安排 view；完成紀錄庫可稍後再補。",
        ]
        self.status_box.setPlainText("\n".join(lines))

    def save_settings(self) -> None:
        changed = False
        token = self.token_input.text().strip()
        schedule = self.schedule_input.text().strip()
        completion = self.completion_input.text().strip()
        if token:
            changed = self.owner.save_notion_token(token) or changed
            self.token_input.clear()
        if schedule:
            changed = self.owner.save_schedule_view_url(schedule) or changed
        if completion:
            changed = self.owner.save_completion_data_source(completion) or changed
        if not changed:
            self.owner.log("api settings wizard: no non-empty setting was saved")
        self.refresh_status()

    def start_sync(self) -> None:
        self.owner.run_local_preflight()
        self.owner.start_sync()
        self.refresh_status()


class M1MakeupPlayerWindow(QMainWindow):
    def __init__(
        self,
        config: AppConfig,
        playback_core: PlaybackCore | None = None,
        attachment_resolver: NotionAttachmentResolver | None = None,
        local_settings_path: str | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.local_settings_path = local_settings_path
        self.store = ProgressStore(config.progress_cache)
        self.store.load()
        self.subtitle_resolver = SubtitleResolver(config.subtitle_dir)
        self.resolved_url_cache = ResolvedUrlCache(config.resolved_url_cache)
        self.attachment_resolver = attachment_resolver or NotionAttachmentResolver(
            cache=self.resolved_url_cache,
            local_settings_path=self.local_settings_path,
        )
        self.writeback = WritebackOutbox(config.writeback_outbox)
        self.playback_core = playback_core or create_default_playback_core()
        self.records: list[PlaybackRecord] = []
        self.current_record: PlaybackRecord | None = None
        self.current_source: VideoSourceInfo | None = None
        self.current_resolution: AttachmentResolution | None = None
        self.current_playability: PlayabilityStatus | None = None
        self.current_subtitle_path = None
        self.current_mpv_subtitle_path: str | None = None
        self.cues: list[SubtitleCue] = []
        self.subtitle_controller = SubtitleController()
        self.subtitle_session = self.subtitle_controller.session
        self.last_subtitle_generation_result: SubtitleGenerationResult | None = None
        self.background_subtitle_generation_counter = 100_000
        self.background_subtitle_generation_jobs: dict[int, tuple[QThread, SubtitleGenerationWorker]] = {}
        self.background_subtitle_generation_starts: set[tuple[str, int]] = set()
        self.session_subtitle_paths: set[Path] = set()
        self.player_fullscreen = False
        self.player_is_playing = False
        self.stream_light = "idle"
        self.subtitle_light = "idle"
        self.mpv_light = "idle"
        self.mpv_idle_started_at: float | None = None
        self.mpv_idle_recovery_attempted = False
        self.live_playback_position_sec = 0.0
        self.live_playback_duration_sec: float | None = None
        self.active_playback_speed = 1.0
        self.pending_playback_speed: float | None = None
        self.pending_playback_speed_deadline: float | None = None
        self.speed_warmup_status = "ready"
        self.pending_autoplay_after_preheat = False
        self.playback_readiness_gate_enabled = False
        self.caption_mode = "auto"
        self.pending_seek_sec: float | None = None
        self.pending_seek_key: str | None = None
        self.sync_thread: QThread | None = None
        self.sync_worker: SyncWorker | None = None
        self.writeback_thread: QThread | None = None
        self.writeback_worker: WritebackFlushWorker | None = None
        self.subtitle_generation_jobs: dict[int, tuple[QThread, SubtitleGenerationWorker]] = {}

        self.setWindowTitle("BDDE38補課系統 by RRK")
        self.resize(1280, 760)

        self.status_label = QLabel("啟動中")
        self.status_label.setStyleSheet("padding: 4px 8px; border-radius: 6px;")
        self.list_widget = QListWidget()
        self.readiness_box = QTextEdit()
        self.readiness_box.setReadOnly(True)
        self.readiness_box.setMinimumHeight(180)
        self.progress_overview_box = QTextEdit()
        self.progress_overview_box.setReadOnly(True)
        self.progress_overview_box.setMaximumHeight(170)
        self.progress_overview_box.setPlainText("補課總覽\n影片總數：0")
        self.sync_button = QPushButton("重新同步")
        self.preflight_button = QPushButton("重新檢查")
        self.api_settings_button = QPushButton("API 設定精靈")
        self.set_token_button = QPushButton("設定 token")
        self.set_completion_source_button = QPushButton("設定完成庫")
        self.set_schedule_view_button = QPushButton("設定課表")
        self.play_button = QPushButton("▶")
        self.restart_button = QPushButton("⏮ 00:00")
        self.rewind_button = QPushButton("⏪")
        self.forward_button = QPushButton("⏩")
        self.fullscreen_button = QPushButton("⛶")
        self.complete_button = QPushButton("標記完成")
        self.subtitle_placeholder_button = QPushButton("建立字幕佔位")
        self.subtitle_placeholder_button.setHidden(True)
        self.subtitle_generate_button = QPushButton("生成字幕")
        self.subtitle_generate_button.setHidden(True)
        self.subtitle_progress_label = QLabel("字幕解析：待命")
        self.subtitle_progress_label.setHidden(True)
        self.subtitle_progress_bar = QProgressBar()
        self.subtitle_progress_bar.setRange(0, 100)
        self.subtitle_progress_bar.setValue(0)
        self.subtitle_progress_bar.setHidden(True)
        self.flush_writeback_button = QPushButton("送出完成紀錄")
        self.speed_combo = QComboBox()
        for label, speed in playback_speed_options():
            self.speed_combo.addItem(label, speed)
        self.speed_combo.setCurrentIndex(2)
        self.cc_button = QPushButton("💬")
        self.cc_button.setEnabled(False)
        self._configure_caption_menu()
        self._configure_player_icon_buttons()
        self.position_slider = SeekSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_time_label = QLabel("00:00 / --:--")
        self.position_time_label.setMinimumWidth(110)
        self.playback_hint_label = QLabel("選取影片後，按播放會從 00:00 全新開始。")
        self.playback_hint_label.setWordWrap(True)
        self.playback_hint_label.setStyleSheet("color: #b8b8b8;")
        self.state_lights_label = QLabel()
        self.state_lights_label.setStyleSheet(
            "QLabel {"
            "background: #161616;"
            "color: #f0f0f0;"
            "border: 1px solid #444;"
            "border-radius: 6px;"
            "padding: 6px 10px;"
            "font-weight: 600;"
            "}"
        )
        self.update_state_lights(detail="idle")
        self.active_subtitle_label = QLabel("尚未載入字幕")
        self.active_subtitle_label.setWordWrap(True)
        self.active_subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.active_subtitle_label.setMinimumHeight(72)
        self.active_subtitle_label.setStyleSheet("border: 1px solid #444; background: #181818; color: #f0f0f0;")
        self.subtitle_box = QListWidget()
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.writeback_count_label = QLabel("待送出完成紀錄：0")
        self.writeback_summary_box = QTextEdit()
        self.writeback_summary_box.setReadOnly(True)
        self.writeback_summary_box.setMaximumHeight(110)
        self.player_area = QWidget()
        self.player_area.setStyleSheet("background: #050505; border: 1px solid #222;")
        self.player_area_layout = QVBoxLayout(self.player_area)
        self.player_area_layout.setContentsMargins(0, 0, 0, 0)
        self.player_area_layout.setSpacing(0)
        self.player_label = PlayerSurface("播放器待命")
        self.player_label.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.player_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.player_label.setMinimumHeight(260)
        self.player_label.setStyleSheet("border: 1px solid #555; background: #111; color: #ddd;")
        self.caption_overlay = PlayerCaptionOverlay(self.player_area)
        self.caption_overlay.setMaximumHeight(110)
        self.player_click_overlay = PlayerDoubleClickOverlay(self.player_label)
        self.player_area_layout.addWidget(self.player_label, 1)
        self.player_area_layout.addWidget(self.caption_overlay)
        self.detail_box = QTextEdit()
        self.detail_box.setReadOnly(True)
        self.detail_box.setMaximumHeight(190)
        self.detail_box.setPlainText("尚未選取影片")

        self.position_timer = QTimer(self)
        self.position_timer.setInterval(1000)
        self.speed_change_warmup_timer = QTimer(self)
        self.speed_change_warmup_timer.setSingleShot(True)
        self.speed_change_warmup_timer.timeout.connect(self.maybe_apply_pending_playback_speed)

        self._build_layout()
        self._configure_embedded_player_window()
        self._connect_signals()
        self._load_cached_records()
        self.log(f"playback core: {self.playback_core.describe()}")
        self.run_local_preflight()
        QTimer.singleShot(200, self.start_sync)

    def _configure_embedded_player_window(self) -> None:
        set_window_id = getattr(self.playback_core, "set_window_id", None)
        if not callable(set_window_id):
            return
        set_window_id(int(self.player_label.winId()))

    def _configure_caption_menu(self) -> None:
        self.caption_menu = QMenu(self)
        self.caption_action_group = QActionGroup(self)
        self.caption_action_group.setExclusive(True)
        self.caption_actions: dict[str, QAction] = {}
        for mode, label in caption_mode_options():
            action = QAction(label, self)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, selected=mode: self.set_caption_mode(selected))
            self.caption_action_group.addAction(action)
            self.caption_menu.addAction(action)
            self.caption_actions[mode] = action
        self.caption_actions["auto"].setChecked(True)
        self.cc_button.setMenu(self.caption_menu)

    def _configure_player_icon_buttons(self) -> None:
        for button, tooltip in (
            (self.play_button, "播放"),
            (self.restart_button, "從頭播放到 00:00"),
            (self.rewind_button, "快退 15 秒"),
            (self.forward_button, "快進 15 秒"),
            (self.fullscreen_button, "切換全螢幕"),
            (self.cc_button, "字幕：自動"),
        ):
            button.setToolTip(tooltip)
            button.setMinimumWidth(42)
        self.restart_button.setMinimumWidth(74)

    def _build_layout(self) -> None:
        left = QWidget()
        self.left_panel = left
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.status_label)
        left_controls = QWidget()
        left_controls_layout = QHBoxLayout(left_controls)
        left_controls_layout.setContentsMargins(0, 0, 0, 0)
        left_controls_layout.addWidget(self.sync_button)
        left_controls_layout.addWidget(self.preflight_button)
        left_controls_layout.addWidget(self.api_settings_button)
        left_layout.addWidget(left_controls)
        settings_controls = QWidget()
        settings_controls_layout = QHBoxLayout(settings_controls)
        settings_controls_layout.setContentsMargins(0, 0, 0, 0)
        settings_controls_layout.addWidget(self.set_token_button)
        settings_controls_layout.addWidget(self.set_completion_source_button)
        settings_controls_layout.addWidget(self.set_schedule_view_button)
        left_layout.addWidget(settings_controls)
        left_layout.addWidget(self.progress_overview_box)
        left_layout.addWidget(self.readiness_box)
        left_layout.addWidget(self.list_widget, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(self.player_area, 3)
        self.playback_controls_panel = QWidget()
        self.playback_controls_panel.setStyleSheet(
            "QWidget { background: rgba(20, 20, 20, 220); border: 1px solid #333; border-radius: 8px; }"
            "QPushButton { padding: 5px 10px; }"
            "QSlider { min-height: 20px; }"
        )
        playback_controls_layout = QVBoxLayout(self.playback_controls_panel)
        playback_controls_layout.setContentsMargins(10, 8, 10, 8)
        playback_controls_layout.setSpacing(6)
        position_row = QWidget()
        position_layout = QHBoxLayout(position_row)
        position_layout.setContentsMargins(0, 0, 0, 0)
        position_layout.addWidget(self.position_slider, 1)
        position_layout.addWidget(self.position_time_label)
        playback_controls_layout.addWidget(position_row)
        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.addWidget(self.play_button)
        controls_layout.addWidget(self.restart_button)
        controls_layout.addWidget(self.rewind_button)
        controls_layout.addWidget(self.forward_button)
        controls_layout.addWidget(self.fullscreen_button)
        controls_layout.addWidget(QLabel("倍速"))
        controls_layout.addWidget(self.speed_combo)
        controls_layout.addWidget(self.cc_button)
        controls_layout.addWidget(self.complete_button)
        controls_layout.addWidget(self.subtitle_placeholder_button)
        controls_layout.addWidget(self.subtitle_generate_button)
        controls_layout.addWidget(self.flush_writeback_button)
        controls_layout.addWidget(self.writeback_count_label)
        playback_controls_layout.addWidget(controls)
        self.player_area_layout.addWidget(self.playback_controls_panel)
        right_layout.addWidget(self.playback_hint_label)
        right_layout.addWidget(self.state_lights_label)
        right_layout.addWidget(self.subtitle_progress_label)
        right_layout.addWidget(self.subtitle_progress_bar)
        self.detail_title = QLabel("目前影片狀態")
        right_layout.addWidget(self.detail_title)
        right_layout.addWidget(self.detail_box)
        right_layout.addWidget(self.writeback_summary_box)
        self.subtitle_title = QLabel("字幕提詞")
        right_layout.addWidget(self.subtitle_title)
        right_layout.addWidget(self.active_subtitle_label)
        right_layout.addWidget(self.subtitle_box, 2)
        self.log_box.setHidden(True)

        splitter = QSplitter()
        self.main_splitter = splitter
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([360, 920])
        self.setCentralWidget(splitter)
        self.player_click_overlay.setGeometry(self.player_label.rect())
        self.position_caption_overlay()
        self.player_click_overlay.raise_()
        self.refresh_player_button_chrome()

    def _connect_signals(self) -> None:
        self.sync_button.clicked.connect(self.start_sync)
        self.preflight_button.clicked.connect(self.run_local_preflight)
        self.api_settings_button.clicked.connect(self.open_api_settings_dialog)
        self.set_token_button.clicked.connect(self.prompt_notion_token)
        self.set_completion_source_button.clicked.connect(self.prompt_completion_data_source)
        self.set_schedule_view_button.clicked.connect(self.prompt_schedule_view_url)
        self.list_widget.itemSelectionChanged.connect(self.preview_selected_item)
        self.list_widget.itemDoubleClicked.connect(self.play_selected_item)
        self.play_button.clicked.connect(self.toggle_playback)
        self.restart_button.pressed.connect(self.restart_current_video)
        self.rewind_button.clicked.connect(lambda: self.jump_relative(-15.0))
        self.forward_button.clicked.connect(lambda: self.jump_relative(15.0))
        self.fullscreen_button.clicked.connect(self.toggle_player_fullscreen)
        self.complete_button.clicked.connect(self.mark_current_completed)
        self.subtitle_placeholder_button.clicked.connect(self.create_current_subtitle_placeholder)
        self.subtitle_generate_button.clicked.connect(self.start_subtitle_generation)
        self.flush_writeback_button.clicked.connect(self.start_writeback_flush)
        self.speed_combo.currentIndexChanged.connect(self.change_playback_speed)
        self.player_label.double_clicked.connect(self.toggle_player_fullscreen)
        self.player_click_overlay.double_clicked.connect(self.toggle_player_fullscreen)
        self.position_slider.seek_requested.connect(self.seek_to_slider)
        self.position_slider.sliderMoved.connect(self.seek_to_slider)
        self.position_slider.sliderReleased.connect(self.seek_to_current_slider)
        self.subtitle_box.itemDoubleClicked.connect(self.seek_to_subtitle_item)
        self.position_timer.timeout.connect(self.poll_playback_position)
        self.fullscreen_shortcut = QShortcut(QKeySequence("F"), self)
        self.fullscreen_shortcut.activated.connect(self.toggle_player_fullscreen)
        self.exit_fullscreen_shortcut = QShortcut(QKeySequence("Esc"), self)
        self.exit_fullscreen_shortcut.activated.connect(self.exit_player_fullscreen)
        self.restart_shortcut = QShortcut(QKeySequence("Home"), self)
        self.restart_shortcut.activated.connect(self.restart_current_video)

    def log(self, message: str) -> None:
        self.log_box.append(message)

    def log_runtime_status(self, event: str, **payload: object) -> None:
        try:
            path = self.config.progress_cache.with_name("runtime_status.jsonl")
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                "event": event,
                **payload,
            }
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            return

    def update_state_lights(
        self,
        *,
        stream: str | None = None,
        subtitle: str | None = None,
        mpv: str | None = None,
        detail: str | None = None,
    ) -> None:
        if stream is not None:
            self.stream_light = stream
        if subtitle is not None:
            self.subtitle_light = subtitle
        if mpv is not None:
            self.mpv_light = mpv
        parts = (
            f"STREAM {state_light_icon(self.stream_light)} {state_light_label(self.stream_light)}",
            f"SUB {state_light_icon(self.subtitle_light)} {state_light_label(self.subtitle_light)}",
            f"MPV {state_light_icon(self.mpv_light)} {state_light_label(self.mpv_light)}",
        )
        suffix = f"  |  {detail}" if detail else ""
        self.state_lights_label.setText("  |  ".join(parts) + suffix)

    def playback_core_snapshot(self) -> dict[str, object]:
        snapshot = getattr(self.playback_core, "status_snapshot", None)
        if not callable(snapshot):
            return {
                "available": self.playback_core.available(),
                "path_loaded": self.playback_core.position_sec() is not None,
                "time_pos": self.playback_core.position_sec(),
                "duration": self.playback_core.duration_sec(),
            }
        try:
            return dict(snapshot())
        except Exception as exc:  # noqa: BLE001 - snapshot is diagnostic only.
            return {"available": self.playback_core.available(), "error": str(exc)}

    def create_api_settings_dialog(self) -> ApiSettingsDialog:
        return ApiSettingsDialog(self)

    def open_api_settings_dialog(self) -> None:
        self.create_api_settings_dialog().exec()

    def prompt_notion_token(self) -> None:
        token, ok = QInputDialog.getText(
            self,
            "設定 Notion token",
            "Notion token:",
            QLineEdit.EchoMode.Password,
        )
        if ok:
            self.save_notion_token(token)

    def prompt_completion_data_source(self) -> None:
        value, ok = QInputDialog.getText(
            self,
            "設定補課完成紀錄 data source",
            "Notion data source URL 或 id:",
        )
        if ok:
            self.save_completion_data_source(value)

    def prompt_schedule_view_url(self) -> None:
        value, ok = QInputDialog.getText(
            self,
            "設定課程安排 view",
            "Notion 課程安排 database view URL:",
        )
        if ok:
            self.save_schedule_view_url(value)

    def save_notion_token(self, token: str) -> bool:
        try:
            path = set_notion_token(token, self.local_settings_path)
        except ValueError:
            self.status_label.setText("Notion token 未寫入")
            self.log("local settings skipped: empty Notion token")
            return False
        self.attachment_resolver = NotionAttachmentResolver(
            cache=self.resolved_url_cache,
            local_settings_path=self.local_settings_path,
        )
        self.status_label.setText("Notion token 已保存")
        self.log(f"local settings updated: notion token saved to {path}")
        self.run_local_preflight()
        return True

    def save_completion_data_source(self, value: str) -> bool:
        try:
            path = set_completion_data_source(value, self.local_settings_path)
        except ValueError:
            self.status_label.setText("完成紀錄 data source 未寫入")
            self.log("local settings skipped: empty completion data source")
            return False
        self.status_label.setText("完成紀錄 data source 已保存")
        self.log(f"local settings updated: completion data source saved to {path}")
        self.run_local_preflight()
        return True

    def save_schedule_view_url(self, value: str) -> bool:
        try:
            path = set_schedule_view_url(value, self.local_settings_path)
        except ValueError:
            self.status_label.setText("課程安排 view 未寫入")
            self.log("local settings skipped: empty schedule view URL")
            return False
        self.config = replace(self.config, schedule_view_url=value.strip())
        self.status_label.setText("課程安排 view 已保存")
        self.log(f"local settings updated: schedule view URL saved to {path}")
        self.run_local_preflight()
        return True

    def refresh_writeback_count(self) -> None:
        try:
            count = self.writeback.count_events()
            summary = collect_writeback_outbox_summary(
                self.writeback,
                CompletionWritebackSink(local_settings_path=self.local_settings_path),
            )
        except Exception as exc:  # noqa: BLE001 - outbox corruption should be visible, not fatal to UI.
            self.writeback_count_label.setText("待送出完成紀錄：讀取失敗")
            self.writeback_summary_box.setPlainText("完成回寫：讀取失敗")
            self.log(f"writeback outbox count failed: {exc}")
            self.refresh_progress_overview(queued_writebacks=0)
            return
        self.writeback_count_label.setText(writeback_count_text(count))
        self.writeback_summary_box.setPlainText(summary.to_text())
        self.refresh_progress_overview(queued_writebacks=count)

    def refresh_progress_overview(self, queued_writebacks: int | None = None) -> None:
        if queued_writebacks is None:
            try:
                queued_writebacks = self.writeback.count_events()
            except Exception as exc:  # noqa: BLE001 - progress box should show a conservative state.
                self.log(f"progress overview outbox count failed: {exc}")
                queued_writebacks = 0
        overview = collect_progress_overview(self.records, queued_writebacks=queued_writebacks)
        self.progress_overview_box.setPlainText(overview.to_text())

    def run_local_preflight(self) -> None:
        self.refresh_writeback_count()
        items = run_preflight(self.config)
        has_error = False
        for item in items:
            self.log(f"preflight {item.status}: {item.key} - {item.message}")
            has_error = has_error or item.error
        readiness_status = self.run_mvp_readiness()
        if has_error:
            self.status_label.setText("本地檢查有錯誤，請看事件欄")
        elif readiness_status == "external_setup_required":
            self.status_label.setText("外部設定未完成，請看事件欄")
        elif readiness_status == "usable_with_warnings":
            self.status_label.setText("MVP 檢查有警告，仍可操作")
        elif any(item.warning for item in items):
            self.status_label.setText("本地檢查有警告，仍可同步")
        else:
            self.status_label.setText("本地檢查通過")

    def run_mvp_readiness(self) -> str:
        report = collect_mvp_readiness(self.config, local_settings_path=self.local_settings_path)
        self.readiness_box.setPlainText(readiness_display_text(report))
        self.log(f"readiness overall: {report.overall_status}")
        for gate in report.gates:
            self.log(f"readiness {gate.status}: {gate.key} [{gate.scope}] - {gate.message}")
        if report.blocking_gates:
            keys = ", ".join(gate.key for gate in report.blocking_gates)
            self.log(f"readiness blocked: {keys}")
        return report.overall_status

    def _load_cached_records(self) -> None:
        self.records = sorted(
            self.store.records.values(),
            key=lambda item: (item.course_date or "", item.segment_index, item.video_name),
        )
        self.refresh_list()

    def refresh_list(self) -> None:
        self.list_widget.clear()
        for record in self.records:
            label = f"{record.course_date or 'no-date'}  P{record.segment_index:02d}  {record.status.value}  {record.video_name}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, record.stable_key)
            self.list_widget.addItem(item)
        self.refresh_progress_overview()

    def start_sync(self) -> None:
        if self.sync_thread is not None:
            return
        self.status_label.setText("正在從 Notion 同步")
        self.sync_button.setEnabled(False)
        self.sync_thread = QThread()
        self.sync_worker = SyncWorker(self.config, self.local_settings_path)
        self.sync_worker.moveToThread(self.sync_thread)
        self.sync_thread.started.connect(self.sync_worker.run)
        self.sync_worker.finished.connect(self.on_sync_finished)
        self.sync_worker.failed.connect(self.on_sync_failed)
        self.sync_worker.finished.connect(self.sync_thread.quit)
        self.sync_worker.failed.connect(self.sync_thread.quit)
        self.sync_thread.finished.connect(self.cleanup_sync_worker)
        self.sync_thread.start()

    @Slot(object)
    def on_sync_finished(self, result: SyncResult) -> None:
        self.store.load()
        self.records = list(result.records)
        self.refresh_list()
        self.status_label.setText(f"已同步 {len(result.records)} 段影片")
        self.log(
            f"Notion sync ok: backend={result.sync_backend} "
            f"pages={len(result.course_pages)} videos={len(result.records)}"
        )
        self.run_mvp_readiness()

    @Slot(str)
    def on_sync_failed(self, message: str) -> None:
        self.status_label.setText("同步失敗，保留本地 cache")
        self.log(f"Notion sync failed: {message}")
        self.run_mvp_readiness()

    @Slot()
    def cleanup_sync_worker(self) -> None:
        self.sync_button.setEnabled(True)
        self.sync_thread = None
        self.sync_worker = None

    def start_writeback_flush(self) -> None:
        if self.writeback_thread is not None:
            return
        self.status_label.setText("正在送出完成紀錄")
        self.flush_writeback_button.setEnabled(False)
        self.writeback_thread = QThread()
        self.writeback_worker = WritebackFlushWorker(self.config, self.local_settings_path)
        self.writeback_worker.moveToThread(self.writeback_thread)
        self.writeback_thread.started.connect(self.writeback_worker.run)
        self.writeback_worker.finished.connect(self.on_writeback_flush_finished)
        self.writeback_worker.failed.connect(self.on_writeback_flush_failed)
        self.writeback_worker.finished.connect(self.writeback_thread.quit)
        self.writeback_worker.failed.connect(self.writeback_thread.quit)
        self.writeback_thread.finished.connect(self.cleanup_writeback_worker)
        self.writeback_thread.start()

    @Slot(object)
    def on_writeback_flush_finished(self, result: FlushResult) -> None:
        dry_run_note = " dry-run" if result.dry_run else ""
        self.log(
            "writeback flush"
            f"{dry_run_note}: attempted={result.attempted} "
            f"succeeded={result.succeeded} remaining={result.remaining} "
            f"message={result.message}"
        )
        self.refresh_writeback_count()
        self.run_local_preflight()
        self.status_label.setText(writeback_status_text(result))

    @Slot(str)
    def on_writeback_flush_failed(self, message: str) -> None:
        self.status_label.setText("完成紀錄送出失敗，保留 outbox")
        self.log(f"writeback flush failed: {message}")

    @Slot()
    def cleanup_writeback_worker(self) -> None:
        self.flush_writeback_button.setEnabled(True)
        self.writeback_thread = None
        self.writeback_worker = None

    def selected_record_from_item(self, item: QListWidgetItem | None) -> PlaybackRecord | None:
        if item is None:
            return None
        key = item.data(Qt.ItemDataRole.UserRole)
        return self.store.records.get(str(key))

    def preview_selected_item(self) -> None:
        item = self.list_widget.currentItem()
        record = self.selected_record_from_item(item)
        if record is None:
            return
        if self.current_record is not None and self.current_record.stable_key == record.stable_key:
            return
        self.status_label.setText(f"候選影片：{record.video_name}")
        self.playback_hint_label.setText("單擊只選取候選；雙擊影片清單才會切換並載入播放器。")

    def load_record_for_playback(self, record: PlaybackRecord) -> bool:
        if (
            self.current_record is not None
            and self.current_record.stable_key == record.stable_key
            and self.current_record.source_ref == record.source_ref
            and self.current_playability is not None
        ):
            return True
        if not self.playback_core.available():
            self.current_record = record
            self.status_label.setText("播放器核心不可用")
            self.playback_hint_label.setText("目前只能檢視課程列表，不能切換播放。")
            self.update_state_lights(stream="idle", subtitle="idle", mpv="red", detail="mpv unavailable")
            return False
        self.set_player_loading_state(record, "正在切換影片，播放器預熱中")
        self.mpv_idle_started_at = None
        self.mpv_idle_recovery_attempted = False
        self.update_state_lights(stream="yellow", subtitle="idle", mpv="yellow", detail="switching video")
        try:
            self.playback_core.stop()
        except Exception as exc:  # noqa: BLE001 - stop is best-effort before reloading the stream.
            self.log(f"player stop before switch failed: {exc}")
        self.current_record = record
        self.update_live_playback_position(0.0, record.duration_sec)
        self.pending_seek_sec = None
        self.pending_seek_key = None
        self.subtitle_controller.reset_for_video()
        source = parse_video_source(record.source_ref)
        resolution = self.attachment_resolver.resolve(source)
        playability = evaluate_playability(
            video_name=record.video_name,
            source=source,
            resolution=resolution,
            playback_available=self.playback_core.available(),
            playback_description=self.playback_core.describe(),
        )
        self.current_source = source
        self.current_resolution = resolution
        self.current_playability = playability
        self.update_state_lights(
            stream="green" if playability.can_play and playability.playable_url else "red",
            subtitle="idle",
            mpv="yellow" if playability.can_play and playability.playable_url else "red",
            detail=playability.state,
        )
        self.log_runtime_status(
            "video_selected",
            record_key=record.stable_key,
            video_name=record.video_name,
            can_play=playability.can_play,
            has_url=bool(playability.playable_url),
            playback_start_sec=0.0,
        )
        if playability.can_play and playability.playable_url:
            self.set_player_loading_state(record, playability.loading_hint or "正在載入串流")
            self.playback_core.load(playability.playable_url)
            self.apply_playback_speed()
            self.playback_core.pause()
            self.position_timer.start()
        else:
            self.position_timer.stop()
        self.player_label.setText(playability.player_label)
        for log_line in playability.log_lines:
            self.log(log_line)
        self.reset_subtitles_for_fresh_generation(record)
        duration_sec = record.duration_sec
        try:
            duration_sec = self.playback_core.duration_sec() or duration_sec
        except Exception:
            pass
        self.update_live_playback_position(0.0, duration_sec)
        self.set_player_playing(False)
        self.refresh_detail_box()
        self.status_label.setText(f"已選取：{record.video_name}")
        self.playback_hint_label.setText("按播放開始；每次從 00:00 全新開始，播放前會先做字幕預熱。")
        self.log(f"loaded video ref: {record.video_name}")
        return playability.can_play and bool(playability.playable_url)

    def reload_current_stream_after_idle(self) -> bool:
        if self.current_record is None or self.current_source is None:
            return False
        if not self.current_source.requires_resolution:
            return False
        self.update_state_lights(stream="yellow", mpv="yellow", detail="refreshing Notion URL")
        self.status_label.setText("播放器未吃到媒體，正在刷新 Notion 播放網址")
        resolution = self.attachment_resolver.resolve(self.current_source, force_refresh=True)
        playability = evaluate_playability(
            video_name=self.current_record.video_name,
            source=self.current_source,
            resolution=resolution,
            playback_available=self.playback_core.available(),
            playback_description=self.playback_core.describe(),
        )
        self.current_resolution = resolution
        self.current_playability = playability
        self.log_runtime_status(
            "stream_url_refreshed_after_idle",
            record_key=self.current_record.stable_key,
            resolver_status=resolution.status,
            can_play=playability.can_play,
            has_url=bool(playability.playable_url),
        )
        if not playability.can_play or not playability.playable_url:
            self.update_state_lights(stream="red", mpv="red", detail=f"resolver {resolution.status}")
            self.playback_hint_label.setText(f"Notion 播放網址刷新失敗：{resolution.status}")
            return False
        self.playback_core.load(playability.playable_url)
        self.apply_playback_speed()
        self.playback_core.play()
        self.set_player_playing(True)
        self.mpv_idle_started_at = time.time()
        self.update_state_lights(stream="green", mpv="yellow", detail="stream reloaded")
        return True

    def set_player_loading_state(self, record: PlaybackRecord, message: str) -> None:
        self.player_label.setText(f"播放器預熱中\n{record.video_name}\n{message}")
        self.status_label.setText(f"正在切換：{record.video_name}")
        self.playback_hint_label.setText("正在準備新影片串流，畫面出現前不會沿用上一支影片。")
        self.update_state_lights(mpv="yellow", detail="loading stream")
        self.update_live_playback_position(0.0, record.duration_sec)
        self.set_player_playing(False)
        self.show_caption_overlay(f"載入中\n{record.video_name}")
        QApplication.processEvents()

    def reset_subtitles_for_fresh_generation(self, record: PlaybackRecord) -> None:
        record.subtitle_path = ""
        self.current_subtitle_path = None
        self.current_mpv_subtitle_path = None
        self.cues = []
        self.session_subtitle_paths.clear()
        self.cc_button.setEnabled(False)
        self.subtitle_box.clear()
        self.active_subtitle_label.setText("播放後會重新解析字幕")
        self.subtitle_box.addItem("播放後會重新解析字幕，不載入舊字幕快取")
        self.hide_caption_overlay()
        self.refresh_caption_menu_state()
        self.update_state_lights(subtitle="yellow", detail="subtitle awaiting preheat")

    def load_subtitles(self, record: PlaybackRecord) -> None:
        path, self.cues = self.subtitle_resolver.load_for(record)
        self.current_subtitle_path = path
        self.current_mpv_subtitle_path = None
        self.cc_button.setEnabled(False)
        self.subtitle_box.clear()
        if not self.cues:
            self.active_subtitle_label.setText("尚未載入字幕")
            self.subtitle_box.addItem("沒有找到本地字幕檔")
            self.cc_button.setEnabled(False)
            self.refresh_caption_menu_state()
            self.update_state_lights(subtitle="idle", detail="subtitle missing")
            return
        self.cc_button.setEnabled(True)
        if path:
            record.subtitle_path = str(path)
            if is_mpv_subtitle_path(path):
                self.current_mpv_subtitle_path = str(path)
                self.load_mpv_subtitle_track()
        self.refresh_caption_menu_state()
        self.active_subtitle_label.setText("等待播放位置")
        self.update_state_lights(subtitle="green", detail="subtitle loaded")
        for cue in self.cues:
            item = QListWidgetItem(f"{format_seconds(cue.start_sec)}  {cue.text}")
            item.setData(Qt.ItemDataRole.UserRole, cue.start_sec)
            self.subtitle_box.addItem(item)

    def create_current_subtitle_placeholder(self) -> None:
        if self.current_record is None:
            self.log("subtitle placeholder skipped: no selected video")
            return
        result = write_missing_markdown_placeholders([self.current_record], self.config.subtitle_dir)
        if result.written:
            self.log(f"subtitle placeholder written: {result.written[0]}")
        elif result.skipped_existing:
            self.log(f"subtitle placeholder exists: {result.skipped_existing[0]}")
        else:
            self.log("subtitle placeholder skipped: no action")
        self.load_subtitles(self.current_record)
        self.refresh_detail_box()

    def start_subtitle_generation(
        self,
        trigger: str = "manual",
        start_sec: float | None = None,
        max_duration_sec: float | None = None,
        overwrite: bool | None = None,
        plan: SubtitleGenerationPlan | None = None,
    ) -> None:
        if self.current_record is None:
            self.log("subtitle generation skipped: no selected video")
            return
        if self.current_playability is None or not self.current_playability.can_play or not self.current_playability.playable_url:
            self.log("subtitle generation skipped: no playable stream URL")
            return
        record = self.current_record
        trigger_name = trigger if isinstance(trigger, str) else "manual"
        if plan is None:
            plan = self.subtitle_controller.explicit_plan(
                record.stable_key,
                trigger=trigger_name,
                start_sec=max(0.0, float(start_sec or 0.0)),
                max_duration_sec=max_duration_sec,
                overwrite=overwrite,
            )
        elif plan.request.record_key != record.stable_key:
            self.log("subtitle generation skipped: request video does not match current video")
            return
        decision = self.subtitle_controller.dispatch_plan(plan)
        if decision.action == "defer" and decision.plan is not None:
            request = decision.plan.request
            self.subtitle_generate_button.setEnabled(False)
            self.subtitle_progress_label.setHidden(False)
            self.subtitle_progress_bar.setHidden(False)
            self.subtitle_progress_bar.setRange(0, 0)
            self.subtitle_progress_label.setText(
                f"字幕解析：已排入最新時間窗 {format_seconds(request.start_sec)}"
            )
            self.status_label.setText("字幕解析已排隊，會在目前任務結束後改跑最新時間窗")
            self.log_runtime_status(
                "subtitle_generation_deferred",
                generation_id=request.generation_id,
                trigger=request.trigger,
                record_key=request.record_key,
                running_generation_id=self.subtitle_controller.running_generation_id,
                start_sec=request.start_sec,
                max_duration_sec=request.max_duration_sec,
            )
            self.log(
                "subtitle generation deferred: "
                f"id={request.generation_id} trigger={request.trigger} "
                f"start={request.start_sec:g}s"
            )
            return
        if decision.action != "start" or decision.plan is None:
            return
        self._start_subtitle_generation_worker(decision.plan)

    def _start_subtitle_generation_worker(self, plan: SubtitleGenerationPlan) -> None:
        if self.current_record is None:
            self.log("subtitle worker start skipped: no selected video")
            self.subtitle_controller.release_running_generation(plan.request.generation_id)
            return
        if self.current_playability is None or not self.current_playability.can_play or not self.current_playability.playable_url:
            self.log("subtitle worker start skipped: no playable stream URL")
            self.subtitle_controller.release_running_generation(plan.request.generation_id)
            return
        record = self.current_record
        if plan.request.record_key != record.stable_key:
            self.log("subtitle worker start skipped: request video does not match current video")
            self.subtitle_controller.release_running_generation(plan.request.generation_id)
            return
        request = plan.request
        options = plan.options
        self.status_label.setText("正在生成字幕，完成後會自動載入")
        self.subtitle_generate_button.setEnabled(False)
        self.log_runtime_status(
            "subtitle_generation_start",
            generation_id=request.generation_id,
            trigger=request.trigger,
            record_key=record.stable_key,
            video_name=record.video_name,
            start_sec=options.start_sec,
            max_duration_sec=options.max_duration_sec,
            device=options.device,
            compute_type=options.compute_type,
            batch_size=options.batch_size,
        )
        self.log(
            "subtitle generation started: "
            f"id={request.generation_id} trigger={request.trigger} "
            f"start={options.start_sec:g}s max={options.max_duration_sec or 'full'}s "
            f"model={options.model_size} language={options.language or 'auto'} "
            f"device={options.device} batch={options.batch_size}"
        )
        thread = QThread()
        worker = SubtitleGenerationWorker(
            request.generation_id,
            record,
            self.current_playability.playable_url,
            str(self.config.subtitle_dir),
            options,
        )
        self.subtitle_generation_jobs[request.generation_id] = (thread, worker)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.on_subtitle_generation_progress)
        worker.finished.connect(self.on_subtitle_generation_finished)
        worker.failed.connect(self.on_subtitle_generation_failed)
        worker.finished.connect(lambda *_args, thread=thread: thread.quit())
        worker.failed.connect(lambda *_args, thread=thread: thread.quit())
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda generation_id=request.generation_id: self.cleanup_subtitle_generation_worker(generation_id))
        thread.finished.connect(thread.deleteLater)
        self.subtitle_progress_label.setHidden(False)
        self.subtitle_progress_bar.setHidden(False)
        self.subtitle_progress_bar.setRange(0, 0)
        self.subtitle_progress_label.setText("字幕解析：準備中")
        thread.start()

    def start_background_subtitle_prefetch(self, position_sec: float, trigger: str = "background_prefetch") -> None:
        if self.current_record is None:
            return
        if self.current_playability is None or not self.current_playability.can_play or not self.current_playability.playable_url:
            return
        if self.max_background_subtitle_workers() <= 0:
            return
        window_sec = self.background_subtitle_window_sec()
        horizon_sec = self.background_subtitle_prefetch_horizon_sec()
        start_cursor = max(0.0, float(position_sec)) + window_sec
        planned = 0
        while self.background_subtitle_worker_available() and planned * window_sec < horizon_sec:
            start_sec = start_cursor + planned * window_sec
            if self.cached_subtitle_covers_position(start_sec + 1.0):
                planned += 1
                continue
            start_key = (self.current_record.stable_key, int(start_sec))
            if start_key in self.background_subtitle_generation_starts:
                planned += 1
                continue
            self.background_subtitle_generation_starts.add(start_key)
            self._start_background_subtitle_generation_worker(
                start_sec=start_sec,
                max_duration_sec=window_sec,
                trigger=trigger,
            )
            planned += 1

    def _start_background_subtitle_generation_worker(
        self,
        start_sec: float,
        max_duration_sec: float,
        trigger: str,
    ) -> None:
        if self.current_record is None or self.current_playability is None or not self.current_playability.playable_url:
            return
        record = self.current_record
        generation_id = self.next_background_generation_id()
        request = SubtitleWindowRequest(
            generation_id=generation_id,
            record_key=record.stable_key,
            trigger=trigger,
            start_sec=max(0.0, float(start_sec)),
            max_duration_sec=max_duration_sec,
        )
        options = self.subtitle_controller.options_factory()
        options = replace(
            options,
            start_sec=request.start_sec,
            max_duration_sec=request.max_duration_sec,
            overwrite=True,
            output_stem_suffix=f"bg{generation_id:05d}",
        )
        self.log_runtime_status(
            "background_subtitle_generation_start",
            generation_id=generation_id,
            trigger=trigger,
            record_key=record.stable_key,
            start_sec=options.start_sec,
            max_duration_sec=options.max_duration_sec,
            running_background_jobs=len(self.background_subtitle_generation_jobs) + 1,
            max_background_jobs=self.max_background_subtitle_workers(),
        )
        thread = QThread()
        worker = SubtitleGenerationWorker(
            generation_id,
            record,
            self.current_playability.playable_url,
            str(self.config.subtitle_dir),
            options,
        )
        self.background_subtitle_generation_jobs[generation_id] = (thread, worker)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.on_background_subtitle_generation_progress)
        worker.finished.connect(self.on_background_subtitle_generation_finished)
        worker.failed.connect(self.on_background_subtitle_generation_failed)
        worker.finished.connect(lambda *_args, thread=thread: thread.quit())
        worker.failed.connect(lambda *_args, thread=thread: thread.quit())
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda generation_id=generation_id: self.cleanup_background_subtitle_generation_worker(generation_id))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def start_urgent_seek_subtitle_worker(self, position_sec: float) -> None:
        if self.current_record is None:
            return
        if self.current_playability is None or not self.current_playability.can_play or not self.current_playability.playable_url:
            return
        if not self.urgent_background_subtitle_worker_available():
            self.log_runtime_status(
                "urgent_seek_subtitle_worker_skipped",
                record_key=self.current_record.stable_key,
                position_sec=round(float(position_sec), 3),
                reason="background_worker_cap_reached",
                running_background_jobs=len(self.background_subtitle_generation_jobs),
                background_worker_cap=self.background_subtitle_worker_cap(),
            )
            return
        start_sec = max(0.0, float(position_sec) - 5.0)
        start_key = (self.current_record.stable_key, int(start_sec))
        if start_key in self.background_subtitle_generation_starts:
            return
        self.background_subtitle_generation_starts.add(start_key)
        self._start_background_subtitle_generation_worker(
            start_sec=start_sec,
            max_duration_sec=self.background_subtitle_window_sec(),
            trigger="seek_urgent_background",
        )

    def accepts_subtitle_generation(self, generation_id: int, record_key: str | None = None) -> bool:
        request = self.subtitle_controller.active_request
        if request is None or request.generation_id != generation_id:
            return False
        current_record_key = self.current_record.stable_key if self.current_record else None
        return self.subtitle_controller.accepts_result(
            generation_id,
            record_key or request.record_key,
            current_record_key=current_record_key,
        )

    def current_subtitle_generation_active(self) -> bool:
        return self.subtitle_controller.has_pending_work()

    def background_subtitle_worker_cap(self) -> int:
        try:
            return max(0, min(8, int(os.environ.get("M1_BACKGROUND_SUBTITLE_WORKERS", "4"))))
        except (TypeError, ValueError):
            return 4

    def desired_background_subtitle_workers(self) -> int:
        cap = self.background_subtitle_worker_cap()
        if cap <= 0:
            return 0
        target_speed = self.speculative_subtitle_playback_speed()
        single_capacity = self.subtitle_single_worker_capacity_ratio()
        if single_capacity and single_capacity > 0:
            total_workers = int(math.ceil(target_speed / single_capacity))
        else:
            total_workers = int(math.ceil(target_speed / 2.0))
        return min(cap, max(0, total_workers - 1))

    def max_background_subtitle_workers(self) -> int:
        return self.desired_background_subtitle_workers()

    def background_subtitle_window_sec(self) -> float:
        try:
            return max(60.0, float(os.environ.get("M1_BACKGROUND_SUBTITLE_WINDOW_SEC", "180")))
        except (TypeError, ValueError):
            return 180.0

    def background_subtitle_prefetch_horizon_sec(self) -> float:
        workers = max(1, self.max_background_subtitle_workers())
        return self.background_subtitle_window_sec() * workers

    def next_background_generation_id(self) -> int:
        self.background_subtitle_generation_counter += 1
        return self.background_subtitle_generation_counter

    def background_subtitle_worker_available(self) -> bool:
        return len(self.background_subtitle_generation_jobs) < self.max_background_subtitle_workers()

    def urgent_background_subtitle_worker_available(self) -> bool:
        return len(self.background_subtitle_generation_jobs) < self.background_subtitle_worker_cap()

    @Slot(int, str, int, str)
    def on_subtitle_generation_progress(self, generation_id: int, stage: str, percent: int, message: str) -> None:
        accepted = self.accepts_subtitle_generation(generation_id)
        self.log_runtime_status(
            "subtitle_generation_progress",
            generation_id=generation_id,
            stage=stage,
            percent=percent,
            accepted=accepted,
            message=message,
        )
        if not accepted:
            return
        self.subtitle_progress_label.setHidden(False)
        self.subtitle_progress_bar.setHidden(False)
        self.update_state_lights(subtitle="yellow", detail=f"subtitle {stage}")
        if percent < 0:
            self.subtitle_progress_bar.setRange(0, 0)
        else:
            self.subtitle_progress_bar.setRange(0, 100)
            self.subtitle_progress_bar.setValue(percent)
        self.subtitle_progress_label.setText(f"字幕解析：{message}")
        self.show_caption_overlay(f"CC 準備中\n{message}")
        if stage != "inference_segment":
            self.status_label.setText(f"字幕解析：{message}")

    @Slot(int, object)
    def on_subtitle_generation_finished(self, generation_id: int, result: SubtitleGenerationResult) -> None:
        self.log(
            "subtitle generation "
            f"id={generation_id} {result.status}: cues={result.cue_count} elapsed={result.elapsed_sec}s "
            f"decode={result.decode_elapsed_sec}s inference={result.inference_elapsed_sec}s "
            f"device={result.device}/{result.compute_type} "
            f"path={result.subtitle_path or 'missing'}"
        )
        self.log_runtime_status(
            "subtitle_generation_finished",
            generation_id=generation_id,
            status=result.status,
            record_key=result.record_key,
            cue_count=result.cue_count,
            subtitle_path=result.subtitle_path,
            elapsed_sec=result.elapsed_sec,
            audio_duration_sec=result.audio_duration_sec,
            decode_elapsed_sec=result.decode_elapsed_sec,
            decode_loop_elapsed_sec=result.decode_loop_elapsed_sec,
            inference_elapsed_sec=result.inference_elapsed_sec,
            processing_capacity_ratio=result.processing_capacity_ratio,
        )
        if not self.accepts_subtitle_generation(generation_id, result.record_key):
            self.log_runtime_status(
                "subtitle_generation_stale_ignored",
                generation_id=generation_id,
                record_key=result.record_key,
                subtitle_path=result.subtitle_path,
            )
            self.delete_stale_generated_subtitle(result.subtitle_path)
            self.log(f"subtitle generation stale ignored: id={generation_id} record={result.record_key}")
            return
        if self.current_record is None:
            return
        self.last_subtitle_generation_result = result
        if not result.subtitle_path or result.cue_count <= 0:
            self.current_record.subtitle_path = ""
            self.store.records[self.current_record.stable_key] = self.current_record
            self.store.save()
            self.current_subtitle_path = None
            self.current_mpv_subtitle_path = None
            self.cues = []
            self.cc_button.setEnabled(False)
            self.subtitle_box.clear()
            self.subtitle_box.addItem("此時間窗沒有辨識到字幕；拖動時間軸會重新定位解析")
            self.active_subtitle_label.setText("此時間窗沒有辨識到字幕")
            self.hide_caption_overlay()
            self.refresh_caption_menu_state()
            self.status_label.setText("字幕解析完成，但此時間窗沒有辨識到字幕")
            self.update_state_lights(subtitle="yellow", detail="subtitle empty")
        elif result.subtitle_path:
            self.session_subtitle_paths.add(Path(result.subtitle_path))
            self.current_record.subtitle_path = result.subtitle_path
            self.store.records[self.current_record.stable_key] = self.current_record
            self.store.save()
            self.load_subtitles(self.current_record)
            self.status_label.setText("字幕已生成並載入")
            self.update_state_lights(subtitle="green", detail="subtitle ready")
            position_sec = self.current_playback_position_for_subtitles()
            self.highlight_subtitle(position_sec)
            self.start_background_subtitle_prefetch(
                position_sec,
                trigger="foreground_ready_dynamic_prefetch",
            )
            self.maybe_autoplay_after_preheat("foreground_subtitle_ready")
        self.refresh_detail_box()
        self.subtitle_progress_label.setHidden(False)
        self.subtitle_progress_bar.setHidden(False)
        self.subtitle_progress_bar.setRange(0, 100)
        self.subtitle_progress_bar.setValue(100)
        capacity_summary = self.subtitle_capacity_summary_text(result)
        suffix = f"；{capacity_summary}" if capacity_summary else ""
        self.subtitle_progress_label.setText(f"字幕解析：完成，{result.cue_count} cues{suffix}")
        self.run_mvp_readiness()

    @Slot(int, str)
    def on_subtitle_generation_failed(self, generation_id: int, message: str) -> None:
        self.log_runtime_status(
            "subtitle_generation_failed",
            generation_id=generation_id,
            accepted=self.accepts_subtitle_generation(generation_id),
            message=message,
        )
        if not self.accepts_subtitle_generation(generation_id):
            self.log(f"subtitle generation stale failure ignored: id={generation_id} message={message}")
            return
        self.status_label.setText("字幕生成失敗，請看事件欄")
        self.subtitle_progress_label.setHidden(False)
        self.subtitle_progress_bar.setHidden(False)
        self.subtitle_progress_bar.setRange(0, 100)
        self.subtitle_progress_bar.setValue(0)
        self.subtitle_progress_label.setText("字幕解析：失敗")
        self.update_state_lights(subtitle="red", detail="subtitle failed")
        self.show_caption_overlay("CC 解析失敗")
        self.log(f"subtitle generation failed: {message}")

    @Slot(int, str, int, str)
    def on_background_subtitle_generation_progress(
        self,
        generation_id: int,
        stage: str,
        percent: int,
        message: str,
    ) -> None:
        self.log_runtime_status(
            "background_subtitle_generation_progress",
            generation_id=generation_id,
            stage=stage,
            percent=percent,
            message=message,
        )

    @Slot(int, object)
    def on_background_subtitle_generation_finished(
        self,
        generation_id: int,
        result: SubtitleGenerationResult,
    ) -> None:
        self.log_runtime_status(
            "background_subtitle_generation_finished",
            generation_id=generation_id,
            status=result.status,
            record_key=result.record_key,
            cue_count=result.cue_count,
            subtitle_path=result.subtitle_path,
            elapsed_sec=result.elapsed_sec,
            audio_duration_sec=result.audio_duration_sec,
            decode_elapsed_sec=result.decode_elapsed_sec,
            inference_elapsed_sec=result.inference_elapsed_sec,
        )
        if self.current_record is None or result.record_key != self.current_record.stable_key:
            return
        if result.subtitle_path and result.cue_count > 0:
            self.session_subtitle_paths.add(Path(result.subtitle_path))
            position_sec = self.current_playback_position_for_subtitles()
            baked_path = self.bake_background_subtitles_for_current_record()
            if baked_path is not None:
                self.session_subtitle_paths.add(baked_path)
            candidate_path = str(baked_path or result.subtitle_path)
            if subtitle_file_covers_position(candidate_path, position_sec):
                self.current_record.subtitle_path = candidate_path
                self.store.records[self.current_record.stable_key] = self.current_record
                self.store.save()
                self.load_subtitles(self.current_record)
                self.highlight_subtitle(position_sec)
                self.status_label.setText("已載入背景預熱字幕")
                self.maybe_autoplay_after_preheat("background_subtitle_ready")
            else:
                self.log(f"background subtitle baked: id={generation_id} path={candidate_path}")
            self.maybe_apply_pending_playback_speed()
        if self.player_is_playing or self.pending_autoplay_after_preheat:
            self.start_background_subtitle_prefetch(
                self.current_playback_position_for_subtitles(),
                trigger="background_prefetch_continuation",
            )

    @Slot(int, str)
    def on_background_subtitle_generation_failed(self, generation_id: int, message: str) -> None:
        self.log_runtime_status(
            "background_subtitle_generation_failed",
            generation_id=generation_id,
            message=message,
        )
        self.log(f"background subtitle generation failed: id={generation_id} message={message}")

    def cleanup_background_subtitle_generation_worker(self, generation_id: int) -> None:
        self.background_subtitle_generation_jobs.pop(int(generation_id), None)

    def cleanup_subtitle_generation_worker(self, generation_id: int) -> None:
        self.subtitle_generation_jobs.pop(int(generation_id), None)
        current_record_key = self.current_record.stable_key if self.current_record else None
        next_plan = self.subtitle_controller.finish_running_generation(
            int(generation_id),
            current_record_key=current_record_key,
        )
        if next_plan is not None:
            self.log_runtime_status(
                "subtitle_generation_deferred_start",
                generation_id=next_plan.request.generation_id,
                trigger=next_plan.request.trigger,
                record_key=next_plan.request.record_key,
                start_sec=next_plan.request.start_sec,
                max_duration_sec=next_plan.request.max_duration_sec,
            )
            QTimer.singleShot(0, lambda plan=next_plan: self._start_subtitle_generation_worker(plan))
            self.subtitle_generate_button.setEnabled(False)
            return
        self.subtitle_generate_button.setEnabled(not self.subtitle_generation_jobs)

    def delete_stale_generated_subtitle(self, subtitle_path: str | None) -> None:
        if not subtitle_path:
            return
        try:
            path = Path(subtitle_path)
            if path.is_file() and path.parent.resolve() == self.config.subtitle_dir.resolve():
                path.unlink()
                self.log_runtime_status("stale_subtitle_file_removed", subtitle_path=str(path))
        except Exception as exc:  # noqa: BLE001 - stale cleanup is best-effort.
            self.log_runtime_status("stale_subtitle_file_remove_failed", subtitle_path=subtitle_path, error=str(exc))

    def toggle_playback(self) -> None:
        if self.current_record is None:
            candidate = self.selected_record_from_item(self.list_widget.currentItem())
            if candidate is None or not self.load_record_for_playback(candidate):
                return
        if not self.playback_core.available():
            return
        try:
            if self.player_is_playing:
                self.playback_core.pause()
                self.set_player_playing(False)
                self.log_runtime_status("playback_paused", record_key=self.current_record.stable_key)
                return
            if not self.ensure_playback_ready_or_preheat("toggle"):
                return
            self.start_actual_playback("toggle")
        except Exception as exc:  # noqa: BLE001 - UI boundary reports playback failure.
            self.log(f"playback toggle failed: {exc}")

    def play_selected_item(self, item: QListWidgetItem) -> None:
        if item is not self.list_widget.currentItem():
            self.list_widget.setCurrentItem(item)
        record = self.selected_record_from_item(item)
        if record is None:
            return
        if not self.load_record_for_playback(record):
            return
        if self.current_record is None or not self.playback_core.available():
            return
        try:
            if not self.ensure_playback_ready_or_preheat("double_click"):
                return
            self.start_actual_playback("double_click")
        except Exception as exc:  # noqa: BLE001 - UI boundary reports playback failure.
            self.log(f"play selected failed: {exc}")

    def ensure_playback_ready_or_preheat(self, source: str) -> bool:
        matrix = self.playback_readiness_matrix()
        self.log_runtime_status("playback_readiness_matrix", source=source, **matrix)
        if matrix["overall"] == "green":
            self.refresh_playback_readiness_gate(matrix)
            self.pending_autoplay_after_preheat = False
            self.playback_readiness_gate_enabled = False
            return True
        if matrix["overall"] == "red":
            self.status_label.setText(f"🔴 無法播放：{matrix['reason']}")
            self.log(f"playback blocked: {matrix['reason']}")
            return False
        self.pending_autoplay_after_preheat = True
        self.playback_readiness_gate_enabled = True
        self.start_initial_playback_subtitle_preheat()
        self.show_caption_overlay(f"字幕預熱中\n{matrix['reason']}")
        self.status_label.setText(f"🟡 黑屏預熱：{matrix['reason']}")
        self.apply_speed_warmup_indicator()
        self.refresh_playback_readiness_gate(matrix)
        self.log_runtime_status("playback_waiting_for_preheat", source=source, reason=matrix["reason"])
        return False

    def playback_readiness_matrix(self) -> dict[str, object]:
        if not self.playback_core.available():
            return {"overall": "red", "reason": "mpv unavailable"}
        if self.current_record is None:
            return {"overall": "red", "reason": "no selected video"}
        if self.current_playability is None or not self.current_playability.can_play or not self.current_playability.playable_url:
            return {"overall": "red", "reason": "no playable Notion URL"}
        position_sec = self.current_playback_position_for_subtitles()
        required = self.required_subtitle_ahead_sec()
        threshold = self.subtitle_gate_threshold_sec(required)
        available = self.subtitle_preheat_available_sec(position_sec)
        subtitle_light = "green" if available >= threshold else "yellow"
        if subtitle_light != "green":
            return {
                "overall": "yellow",
                "reason": f"subtitle preheat {available:.0f}/{threshold:.0f}s",
                "position_sec": round(position_sec, 3),
                "subtitle_available_sec": round(available, 3),
                "subtitle_required_sec": round(required, 3),
                "subtitle_ready_threshold_sec": round(threshold, 3),
                "speculative_speed": self.speculative_subtitle_playback_speed(),
                "worker_active": self.current_subtitle_generation_active()
                or bool(self.background_subtitle_generation_jobs),
            }
        capacity = self.subtitle_capacity_readiness()
        if capacity and not bool(capacity["ready"]):
            return {
                "overall": "yellow",
                "reason": (
                    f"subtitle capacity {capacity['effective_capacity_ratio']:.2f}/"
                    f"{capacity['target_speed']:.2f}x"
                ),
                "position_sec": round(position_sec, 3),
                "subtitle_available_sec": round(available, 3),
                "subtitle_required_sec": round(required, 3),
                "subtitle_ready_threshold_sec": round(threshold, 3),
                **capacity,
                "worker_active": self.current_subtitle_generation_active()
                or bool(self.background_subtitle_generation_jobs),
            }
        return {
            "overall": "green",
            "reason": "ready",
            "position_sec": round(position_sec, 3),
            "subtitle_available_sec": round(available, 3),
            "subtitle_required_sec": round(required, 3),
            "subtitle_ready_threshold_sec": round(threshold, 3),
            "speculative_speed": self.speculative_subtitle_playback_speed(),
            **(capacity or {}),
        }

    def start_actual_playback(self, source: str) -> None:
        matrix = self.playback_readiness_matrix()
        self.refresh_playback_readiness_gate(matrix)
        if matrix["overall"] != "green":
            self.pending_autoplay_after_preheat = True
            self.playback_readiness_gate_enabled = True
            self.start_initial_playback_subtitle_preheat()
            self.status_label.setText(f"🟡 等待全綠燈：{matrix['reason']}")
            self.log_runtime_status("playback_start_blocked_by_gate", source=source, **matrix)
            return
        self.apply_playback_speed()
        self.playback_core.play()
        self.set_player_playing(True)
        self.mpv_idle_started_at = time.time()
        self.update_state_lights(mpv="yellow", detail="mpv starting")
        self.log_runtime_status(
            "playback_started",
            record_key=self.current_record.stable_key if self.current_record else None,
            source=source,
        )

    def maybe_autoplay_after_preheat(self, source: str) -> None:
        if not self.pending_autoplay_after_preheat or self.player_is_playing:
            return
        matrix = self.playback_readiness_matrix()
        self.log_runtime_status("playback_preheat_recheck", source=source, **matrix)
        if matrix["overall"] != "green":
            self.refresh_playback_readiness_gate(matrix)
            return
        self.refresh_playback_readiness_gate(matrix)
        self.pending_autoplay_after_preheat = False
        self.playback_readiness_gate_enabled = False
        self.status_label.setText("🟢 預熱完成，開始播放")
        self.apply_speed_warmup_indicator()
        self.start_actual_playback(source)

    def refresh_playback_readiness_gate(self, matrix: dict[str, object] | None = None) -> None:
        matrix = matrix or self.playback_readiness_matrix()
        ready = matrix.get("overall") == "green"
        subtitle_light = "green" if ready else "yellow"
        if matrix.get("overall") == "red":
            subtitle_light = "red"
        self.update_state_lights(subtitle=subtitle_light, detail=str(matrix.get("reason", "waiting")))
        if not self.playback_readiness_gate_enabled:
            self.play_button.setEnabled(True)
            self.play_button.setToolTip("暫停" if self.player_is_playing else "播放")
            return
        self.play_button.setEnabled(ready)
        if ready:
            self.play_button.setToolTip("預熱完成，可以播放")
        else:
            self.play_button.setToolTip(f"預熱中：{matrix.get('reason', 'waiting')}")

    def enforce_playback_runtime_readiness(self, position_sec: float) -> bool:
        if not self.player_is_playing:
            return True
        matrix = self.playback_readiness_matrix()
        if matrix["overall"] == "green":
            self.refresh_playback_readiness_gate(matrix)
            return True
        self.playback_core.pause()
        self.set_player_playing(False)
        self.playback_readiness_gate_enabled = True
        self.refresh_playback_readiness_gate(matrix)
        if matrix["overall"] == "yellow":
            self.pending_autoplay_after_preheat = True
            self.start_background_subtitle_prefetch(
                position_sec,
                trigger="runtime_gate_background_prefetch",
            )
            if not self.current_subtitle_generation_active() and self.subtitle_prefetch_needed(position_sec):
                self.maybe_start_timeline_subtitle_generation(
                    trigger="runtime_gate_timeline",
                    position_sec=position_sec,
                    force=True,
                )
            reason = str(matrix.get("reason", "subtitle buffering"))
            self.status_label.setText(f"Subtitle buffering: {reason}")
            self.show_caption_overlay(f"Subtitle buffering\n{reason}")
            log_payload = dict(matrix)
            log_payload["gate_position_sec"] = round(float(position_sec), 3)
            self.log_runtime_status(
                "playback_paused_for_subtitle_preheat",
                record_key=self.current_record.stable_key if self.current_record else None,
                **log_payload,
            )
            return False
        self.pending_autoplay_after_preheat = False
        reason = str(matrix.get("reason", "playback blocked"))
        self.status_label.setText(f"Playback blocked: {reason}")
        self.show_caption_overlay(f"Playback blocked\n{reason}")
        log_payload = dict(matrix)
        log_payload["gate_position_sec"] = round(float(position_sec), 3)
        self.log_runtime_status(
            "playback_paused_for_readiness_block",
            record_key=self.current_record.stable_key if self.current_record else None,
            **log_payload,
        )
        return False

    def restart_current_video(self) -> None:
        if self.current_record is None:
            self.log_runtime_status("playback_restart_skipped", reason="no_current_record")
            return
        if not self.playback_core.available():
            self.log_runtime_status(
                "playback_restart_skipped",
                record_key=self.current_record.stable_key,
                reason="playback_core_unavailable",
            )
            return
        if self.current_playability is None or not self.current_playability.playable_url:
            self.log_runtime_status(
                "playback_restart_skipped",
                record_key=self.current_record.stable_key,
                reason="no_playable_url",
            )
            return
        try:
            self.log_runtime_status("playback_restart_requested", record_key=self.current_record.stable_key)
            self.pending_seek_sec = None
            self.pending_seek_key = None
            self.subtitle_controller.reset_for_video()
            self.reset_subtitles_for_fresh_generation(self.current_record)
            self.playback_core.load_at(self.current_playability.playable_url, 0.0)
            self.update_live_playback_position(0.0, self.current_record.duration_sec)
            self.maybe_start_timeline_subtitle_generation(
                trigger="restart_timeline",
                position_sec=0.0,
                force=True,
            )
            if not self.ensure_playback_ready_or_preheat("restart"):
                self.refresh_detail_box()
                self.log_runtime_status("playback_restart_waiting_for_preheat", record_key=self.current_record.stable_key)
                return
            self.start_actual_playback("restart")
            self.log_runtime_status("playback_restarted", record_key=self.current_record.stable_key)
            self.refresh_detail_box()
            self.status_label.setText("已從頭播放")
            self.playback_hint_label.setText("目前從 00:00 播放；時間軸可點擊或拖曳。")
        except Exception as exc:  # noqa: BLE001
            self.log_runtime_status(
                "playback_restart_failed",
                record_key=self.current_record.stable_key if self.current_record else None,
                error=str(exc),
            )
            self.log(f"restart playback failed: {exc}")

    def jump_relative(self, delta_sec: float) -> None:
        if self.current_record is None or not self.playback_core.available():
            return
        current = self.current_playback_position_for_subtitles()
        duration = self.current_record.duration_sec
        try:
            duration = self.playback_core.duration_sec() or duration
        except Exception:
            pass
        target = max(0.0, current + float(delta_sec))
        if duration and duration > 0:
            target = min(float(duration), target)
        self.seek_to_slider(int(target))

    def maybe_start_timeline_subtitle_generation(
        self,
        trigger: str = "playback_timeline",
        position_sec: float | None = None,
        force: bool = False,
    ) -> None:
        if self.current_record is None:
            return
        if self.current_playability is None or not self.current_playability.can_play:
            return
        if not self.current_playability.playable_url:
            return
        target_sec = self.current_playback_position_for_subtitles() if position_sec is None else max(0.0, float(position_sec))
        plan = self.subtitle_controller.timeline_plan(
            self.current_record.stable_key,
            self.cues,
            target_sec,
            trigger=trigger,
            force=force,
        )
        if plan is None:
            return
        request = plan.request
        self.log(
            "timeline subtitle generation requested: "
            f"id={request.generation_id} trigger={trigger} start={request.start_sec:g}s"
        )
        self.start_subtitle_generation(
            trigger=trigger,
            start_sec=request.start_sec,
            max_duration_sec=request.max_duration_sec,
            overwrite=True,
            plan=plan,
        )

    def start_initial_playback_subtitle_preheat(self) -> None:
        position_sec = self.current_playback_position_for_subtitles()
        self.maybe_start_timeline_subtitle_generation(
            trigger="initial_handshake_timeline",
            position_sec=position_sec,
            force=True,
        )
        self.start_background_subtitle_prefetch(
            position_sec,
            trigger="initial_handshake_background_prefetch",
        )
        self.log_runtime_status(
            "initial_subtitle_preheat_requested",
            record_key=self.current_record.stable_key if self.current_record else None,
            position_sec=position_sec,
            speculative_speed=self.speculative_subtitle_playback_speed(),
        )

    def maybe_prefetch_subtitles_for_position(self, position_sec: float) -> None:
        if not self.player_is_playing:
            return
        self.start_background_subtitle_prefetch(position_sec)
        if self.current_subtitle_generation_active():
            return
        if not self.subtitle_prefetch_needed(position_sec):
            return
        self.maybe_start_timeline_subtitle_generation(
            trigger="playback_prefetch_timeline",
            position_sec=position_sec,
            force=True,
        )

    def subtitle_prefetch_needed(self, position_sec: float) -> bool:
        if subtitle_cues_need_generation(self.cues):
            return True
        ahead_sec = self.subtitle_ahead_remaining_sec(position_sec)
        return ahead_sec < self.required_subtitle_ahead_sec()

    def subtitle_ahead_remaining_sec(self, position_sec: float) -> float:
        future_ends = [cue.end_sec for cue in self.cues if cue.end_sec >= position_sec]
        if not future_ends:
            return 0.0
        return max(0.0, max(future_ends) - position_sec)

    def cached_subtitle_covers_position(self, position_sec: float) -> bool:
        if self.current_record is None:
            return False
        if active_cue(self.cues, position_sec) is not None:
            return True
        return self.find_cached_subtitle_for_position(position_sec) is not None

    def find_cached_subtitle_for_position(self, position_sec: float) -> str | None:
        if self.current_record is None:
            return None
        for path in sorted(self.session_subtitle_paths, key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True):
            if not path.exists():
                continue
            if subtitle_file_covers_position(path, position_sec):
                return str(path)
        return None

    def load_cached_subtitle_for_position(self, position_sec: float) -> bool:
        if self.current_record is None:
            return False
        subtitle_path = self.find_cached_subtitle_for_position(position_sec)
        if not subtitle_path:
            return False
        self.current_record.subtitle_path = subtitle_path
        self.store.records[self.current_record.stable_key] = self.current_record
        self.store.save()
        self.load_subtitles(self.current_record)
        self.log_runtime_status(
            "cached_subtitle_loaded_for_position",
            record_key=self.current_record.stable_key,
            position_sec=position_sec,
            subtitle_path=subtitle_path,
        )
        return True

    def rolling_subtitle_path_for_current_record(self) -> Path | None:
        if self.current_record is None:
            return None
        stem = safe_filename_stem(self.current_record.stable_key.replace(":", "_"))
        return self.config.subtitle_dir / f"{stem}_rolling.srt"

    def generated_subtitle_window_paths_for_current_record(self) -> list[Path]:
        if self.current_record is None:
            return []
        paths = [
            path
            for path in self.session_subtitle_paths
            if path.exists() and path.suffix.lower() == ".srt" and not path.name.endswith("_rolling.srt")
        ]
        return sorted(paths, key=lambda item: item.name)

    def bake_background_subtitles_for_current_record(self) -> Path | None:
        rolling_path = self.rolling_subtitle_path_for_current_record()
        if rolling_path is None:
            return None
        input_paths = self.generated_subtitle_window_paths_for_current_record()
        if rolling_path.exists():
            input_paths.append(rolling_path)
        if not input_paths:
            return None
        merged = merge_subtitle_files(rolling_path, input_paths)
        self.log_runtime_status(
            "rolling_subtitle_baked",
            record_key=self.current_record.stable_key if self.current_record else None,
            subtitle_path=str(rolling_path),
            cue_count=len(merged),
            input_count=len(input_paths),
        )
        return rolling_path

    def required_subtitle_ahead_sec(self) -> float:
        result = self.last_subtitle_generation_result
        playback_speed = self.speculative_subtitle_playback_speed()
        if result and result.elapsed_sec and result.audio_duration_sec:
            observed_cost = max(float(result.elapsed_sec), 1.0)
            return max(45.0, min(float(result.audio_duration_sec), observed_cost * playback_speed * 1.5))
        return max(45.0, min(180.0, playback_speed * 20.0))

    def subtitle_ready_threshold_sec(self, required_sec: float) -> float:
        tolerance = min(12.0, max(5.0, float(required_sec) * 0.05))
        return max(0.0, float(required_sec) - tolerance)

    def subtitle_runtime_low_watermark_sec(self) -> float:
        speed = self.effective_subtitle_playback_speed()
        return min(90.0, max(30.0, float(speed) * 12.0))

    def subtitle_gate_threshold_sec(self, required_sec: float) -> float:
        if self.player_is_playing:
            return min(self.subtitle_ready_threshold_sec(required_sec), self.subtitle_runtime_low_watermark_sec())
        return self.subtitle_ready_threshold_sec(required_sec)

    def subtitle_single_worker_capacity_ratio(self) -> float | None:
        result = self.last_subtitle_generation_result
        if result is None:
            return None
        if result.processing_capacity_ratio:
            return max(0.0, float(result.processing_capacity_ratio))
        if result.audio_duration_sec and result.elapsed_sec:
            return max(0.0, float(result.audio_duration_sec) / max(float(result.elapsed_sec), 1.0))
        return None

    def subtitle_capacity_readiness(self) -> dict[str, object] | None:
        single_capacity = self.subtitle_single_worker_capacity_ratio()
        if single_capacity is None:
            return None
        target_speed = self.speculative_subtitle_playback_speed()
        planned_workers = max(1, 1 + self.max_background_subtitle_workers())
        effective_capacity = single_capacity * planned_workers
        return {
            "ready": effective_capacity >= target_speed,
            "single_worker_capacity_ratio": round(single_capacity, 3),
            "planned_worker_count": planned_workers,
            "effective_capacity_ratio": round(effective_capacity, 3),
            "target_speed": round(target_speed, 3),
        }

    def current_playback_position_for_subtitles(self) -> float:
        if (
            self.current_record is not None
            and self.pending_seek_key == self.current_record.stable_key
            and self.pending_seek_sec is not None
        ):
            return max(0.0, float(self.pending_seek_sec))
        if self.playback_core.available():
            try:
                position_sec = self.playback_core.position_sec()
                if position_sec is not None:
                    return float(position_sec)
            except Exception as exc:  # noqa: BLE001
                self.log(f"subtitle position probe failed: {exc}")
        return max(0.0, float(self.live_playback_position_sec))

    def seek_to_slider(self, value: int) -> None:
        if not self.playback_core.available():
            return
        try:
            self.playback_core.seek(float(value))
            duration_sec = self.current_record.duration_sec if self.current_record is not None else None
            try:
                duration_sec = self.playback_core.duration_sec() or duration_sec
            except Exception:
                pass
            self.update_live_playback_position(float(value), duration_sec)
            self.refresh_detail_box()
            self.log_runtime_status(
                "playback_seek",
                record_key=self.current_record.stable_key if self.current_record else None,
                position_sec=float(value),
            )
            self.maybe_start_timeline_subtitle_generation(
                trigger="seek_timeline",
                position_sec=float(value),
            )
            self.start_urgent_seek_subtitle_worker(float(value))
        except Exception as exc:  # noqa: BLE001
            self.log(f"seek failed: {exc}")

    def seek_to_current_slider(self) -> None:
        self.seek_to_slider(self.position_slider.value())

    def seek_to_subtitle_item(self, item: QListWidgetItem) -> None:
        start_sec = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(start_sec, float):
            self.seek_to_slider(int(start_sec))

    def selected_playback_speed(self) -> float:
        value = self.speed_combo.currentData()
        try:
            return float(value)
        except (TypeError, ValueError):
            return 1.0

    def effective_subtitle_playback_speed(self) -> float:
        if self.pending_playback_speed is not None:
            return max(float(self.pending_playback_speed), self.active_playback_speed)
        return self.selected_playback_speed()

    def speculative_subtitle_playback_speed(self) -> float:
        try:
            speculative_speed = float(os.environ.get("M1_SPECULATIVE_SUBTITLE_SPEED", "4"))
        except (TypeError, ValueError):
            speculative_speed = 4.0
        speculative_speed = min(8.0, max(1.0, speculative_speed))
        return max(self.effective_subtitle_playback_speed(), speculative_speed)

    def change_playback_speed(self, *_args: object) -> None:
        if not self.playback_core.available():
            return
        requested_speed = self.selected_playback_speed()
        if self.player_is_playing and requested_speed > self.active_playback_speed:
            self.defer_playback_speed_increase(requested_speed)
            return
        self.pending_playback_speed = None
        self.pending_playback_speed_deadline = None
        self.speed_change_warmup_timer.stop()
        self.apply_playback_speed(requested_speed)
        capacity_summary = self.subtitle_capacity_summary_text(self.last_subtitle_generation_result)
        if capacity_summary:
            self.status_label.setText(f"播放速度 {requested_speed:g}x；{capacity_summary}")
        if self.player_is_playing:
            self.start_background_subtitle_prefetch(
                self.current_playback_position_for_subtitles(),
                trigger="speed_change_background_prefetch",
            )
            self.maybe_start_timeline_subtitle_generation(
                trigger="speed_change_timeline",
                position_sec=self.current_playback_position_for_subtitles(),
                force=True,
            )

    def defer_playback_speed_increase(self, requested_speed: float) -> None:
        self.pending_playback_speed = float(requested_speed)
        self.speed_warmup_status = "warming"
        warmup_ms = self.playback_speed_warmup_ms(requested_speed)
        self.pending_playback_speed_deadline = time.monotonic() + warmup_ms / 1000.0
        position_sec = self.current_playback_position_for_subtitles()
        self.start_background_subtitle_prefetch(
            position_sec,
            trigger="speed_change_background_prefetch",
        )
        self.maybe_start_timeline_subtitle_generation(
            trigger="speed_change_timeline",
            position_sec=position_sec,
            force=True,
        )
        self.speed_change_warmup_timer.start(1000)
        self.status_label.setText(
            f"🟡 字幕預熱中：準備切到 {requested_speed:g}x，暫時維持 {self.active_playback_speed:g}x"
        )
        self.apply_speed_warmup_indicator()
        self.log_runtime_status(
            "playback_speed_change_deferred",
            requested_speed=requested_speed,
            active_speed=self.active_playback_speed,
            warmup_ms=warmup_ms,
            position_sec=position_sec,
        )

    def playback_speed_warmup_ms(self, requested_speed: float) -> int:
        result = self.last_subtitle_generation_result
        if result and result.audio_duration_sec and result.elapsed_sec:
            window = max(float(result.audio_duration_sec), 1.0)
            throughput = window / max(float(result.elapsed_sec), 1.0)
            required_buffer = min(window, max(20.0, float(requested_speed) * 12.0))
            missing_ratio = max(0.0, float(requested_speed) - throughput) / max(float(requested_speed), 1.0)
            return int(max(2500, min(12000, required_buffer * missing_ratio * 1000.0)))
        return int(max(2500, min(9000, float(requested_speed) * 1200.0)))

    def maybe_apply_pending_playback_speed(self) -> None:
        if self.pending_playback_speed is None:
            return
        pending_speed = self.pending_playback_speed
        position_sec = self.current_playback_position_for_subtitles()
        ready = self.subtitle_preheat_ready_for_speed(position_sec, pending_speed)
        if not ready:
            required = self.subtitle_preheat_required_sec(pending_speed)
            available = self.subtitle_preheat_available_sec(position_sec)
            self.status_label.setText(
                f"🟡 字幕預熱中：{available:.0f}/{required:.0f}s，準備切到 {pending_speed:g}x"
            )
            self.apply_speed_warmup_indicator()
            self.speed_change_warmup_timer.start(1000)
            return
        self.pending_playback_speed = None
        self.pending_playback_speed_deadline = None
        self.speed_warmup_status = "ready"
        self.apply_playback_speed(pending_speed)
        self.status_label.setText(f"🟢 播放速度 {pending_speed:g}x")
        self.apply_speed_warmup_indicator()
        self.log_runtime_status("playback_speed_change_applied_after_warmup", speed=pending_speed)

    def apply_speed_warmup_indicator(self) -> None:
        if self.speed_warmup_status == "warming":
            self.status_label.setStyleSheet(
                "padding: 4px 8px; border-radius: 6px; background: #3d3000; color: #ffd75a;"
            )
            return
        self.status_label.setStyleSheet(
            "padding: 4px 8px; border-radius: 6px; background: #08351e; color: #78f2a3;"
        )

    def subtitle_preheat_required_sec(self, speed: float) -> float:
        return min(180.0, max(20.0, float(speed) * 12.0))

    def subtitle_preheat_ready_for_speed(self, position_sec: float, speed: float) -> bool:
        required = self.subtitle_preheat_required_sec(speed)
        return self.subtitle_preheat_available_sec(position_sec) >= self.subtitle_ready_threshold_sec(required)

    def subtitle_preheat_available_sec(self, position_sec: float) -> float:
        current = self.subtitle_ahead_remaining_sec(position_sec)
        cached = self.cached_subtitle_ahead_remaining_sec(position_sec)
        return max(current, cached)

    def cached_subtitle_ahead_remaining_sec(self, position_sec: float) -> float:
        if self.current_record is None:
            return 0.0
        best_end = 0.0
        for path in self.session_subtitle_paths:
            if not path.exists():
                continue
            best_end = max(best_end, subtitle_file_ahead_end_from_position(path, position_sec))
        return max(0.0, best_end - position_sec)

    def apply_playback_speed(self, speed: float | None = None) -> None:
        target_speed = self.selected_playback_speed() if speed is None else float(speed)
        try:
            self.playback_core.set_speed(target_speed)
            self.active_playback_speed = target_speed
        except Exception as exc:  # noqa: BLE001
            self.log(f"playback speed change failed: {exc}")

    def subtitle_capacity_plan_for_result(
        self,
        result: SubtitleGenerationResult | None,
    ) -> RollingPipelinePlan | None:
        if result is None:
            return None
        if result.audio_duration_sec is None or result.decode_elapsed_sec is None or result.inference_elapsed_sec is None:
            return None
        plan = plan_rolling_subtitle_pipeline(
            audio_window_sec=result.audio_duration_sec,
            decode_elapsed_sec=result.decode_elapsed_sec,
            inference_elapsed_sec=result.inference_elapsed_sec,
            playback_rate=self.effective_subtitle_playback_speed(),
            max_decode_workers=1,
        )
        self.log_runtime_status(
            "subtitle_capacity_plan",
            playback_rate=plan.playback_rate,
            audio_window_sec=plan.audio_window_sec,
            decode_elapsed_sec=plan.decode_elapsed_sec,
            inference_elapsed_sec=plan.inference_elapsed_sec,
            expected_pipeline_capacity_ratio=plan.expected_pipeline_capacity_ratio,
            recommended_decode_workers=plan.recommended_decode_workers,
            recommended_gpu_workers=plan.recommended_gpu_workers,
            can_keep_up=plan.can_keep_up,
            note=plan.note,
        )
        return plan

    def subtitle_capacity_summary_text(self, result: SubtitleGenerationResult | None) -> str:
        plan = self.subtitle_capacity_plan_for_result(result)
        if plan is None:
            return ""
        status = "可追上" if plan.can_keep_up else "會落後"
        return (
            f"產能 {plan.expected_pipeline_capacity_ratio:g}x / 播放 {plan.playback_rate:g}x，"
            f"{status}；建議 decode workers {plan.recommended_decode_workers}，GPU workers {plan.recommended_gpu_workers}"
        )

    def toggle_player_fullscreen(self) -> None:
        self.player_fullscreen = not self.player_fullscreen
        self.apply_player_fullscreen(self.player_fullscreen)

    def apply_player_fullscreen(self, enabled: bool) -> None:
        self.player_fullscreen = enabled
        for widget in (
            self.left_panel,
            self.detail_title,
            self.detail_box,
            self.writeback_summary_box,
            self.writeback_count_label,
            self.subtitle_title,
            self.active_subtitle_label,
            self.subtitle_box,
            self.playback_hint_label,
            self.complete_button,
            self.flush_writeback_button,
        ):
            widget.setHidden(enabled)
        self.fullscreen_button.setText("⛶")
        self.fullscreen_button.setToolTip("離開全螢幕" if enabled else "切換全螢幕")
        self.refresh_player_button_chrome()
        if enabled:
            self.showFullScreen()
        else:
            self.showNormal()
        self.player_click_overlay.setGeometry(self.player_label.rect())
        self.position_caption_overlay()
        self.player_click_overlay.raise_()

    def exit_player_fullscreen(self) -> None:
        if self.player_fullscreen:
            self.apply_player_fullscreen(False)

    def load_mpv_subtitle_track(self) -> None:
        if not self.current_mpv_subtitle_path or not self.playback_core.available():
            return
        try:
            self.playback_core.load_subtitle(self.current_mpv_subtitle_path)
            self.playback_core.set_subtitle_visible(self.caption_output_channel() == "native")
        except Exception as exc:  # noqa: BLE001
            self.log(f"mpv subtitle track load failed: {exc}")

    def set_caption_mode(self, mode: str) -> None:
        if mode not in self.caption_actions:
            mode = "auto"
        self.caption_mode = mode
        self.caption_actions[mode].setChecked(True)
        self.apply_caption_mode()

    def apply_caption_mode(self) -> None:
        enabled = self.caption_mode != "off"
        self.refresh_caption_button_chrome()
        if self.playback_core.available():
            try:
                self.playback_core.set_subtitle_visible(self.caption_output_channel() == "native")
            except Exception as exc:  # noqa: BLE001
                self.log(f"subtitle visibility change failed: {exc}")
        if enabled:
            self.highlight_subtitle(self.current_playback_position_for_subtitles())
        else:
            self.hide_caption_overlay()

    def refresh_caption_menu_state(self) -> None:
        has_cues = bool(self.cues)
        has_native = bool(self.current_mpv_subtitle_path)
        self.cc_button.setEnabled(has_cues)
        for mode, action in self.caption_actions.items():
            action.setEnabled(has_cues)
            if mode == "native":
                action.setEnabled(has_cues and has_native)
        if self.caption_mode == "native" and not has_native:
            self.set_caption_mode("auto")
        else:
            self.refresh_caption_button_chrome()

    def caption_output_channel(self) -> str | None:
        if self.caption_mode == "off":
            return None
        if self.caption_mode == "native":
            return "native" if self.current_mpv_subtitle_path else None
        if self.caption_mode == "osd":
            return "osd"
        if self.caption_mode == "qt":
            return "qt"
        if self.player_fullscreen:
            return "osd"
        return "qt"

    def refresh_caption_button_chrome(self) -> None:
        labels = {
            "auto": "自動",
            "native": "mpv 原生",
            "osd": "影片浮層",
            "qt": "下方字幕條",
            "off": "關閉",
        }
        self.cc_button.setText("💬")
        self.cc_button.setToolTip(f"字幕：{labels.get(self.caption_mode, '自動')}")

    def refresh_player_button_chrome(self) -> None:
        self.refresh_play_button_chrome()
        self.restart_button.setText("⏮ 00:00")
        self.rewind_button.setText("⏪")
        self.forward_button.setText("⏩")
        self.fullscreen_button.setText("⛶")
        self.refresh_caption_button_chrome()

    def set_player_playing(self, playing: bool) -> None:
        self.player_is_playing = bool(playing)
        self.refresh_play_button_chrome()

    def refresh_play_button_chrome(self) -> None:
        self.play_button.setText("⏸" if self.player_is_playing else "▶")
        self.play_button.setToolTip("暫停" if self.player_is_playing else "播放")

    def poll_playback_position(self) -> None:
        if self.current_record is None or not self.playback_core.available():
            return
        try:
            position_sec = self.playback_core.position_sec()
            duration_sec = self.playback_core.duration_sec()
        except Exception as exc:  # noqa: BLE001
            self.log(f"playback poll failed: {exc}")
            self.update_state_lights(mpv="red", detail="mpv poll failed")
            return
        if position_sec is None:
            snapshot = self.playback_core_snapshot()
            idle_active = bool(snapshot.get("idle_active")) or bool(snapshot.get("core_idle"))
            path_loaded = bool(snapshot.get("path_loaded"))
            if self.player_is_playing and idle_active and not path_loaded:
                if self.mpv_idle_started_at is None:
                    self.mpv_idle_started_at = time.time()
                idle_elapsed = time.time() - self.mpv_idle_started_at
                if idle_elapsed >= 3.0 and not self.mpv_idle_recovery_attempted:
                    self.mpv_idle_recovery_attempted = True
                    self.log_runtime_status(
                        "mpv_idle_recovery_attempt",
                        record_key=self.current_record.stable_key,
                        idle_elapsed_sec=round(idle_elapsed, 3),
                        idle_active=idle_active,
                        path_loaded=path_loaded,
                    )
                    if self.reload_current_stream_after_idle():
                        return
                light = "red" if self.mpv_idle_recovery_attempted else "yellow"
                self.update_state_lights(mpv=light, detail=f"mpv idle {idle_elapsed:.0f}s")
                self.status_label.setText("播放器尚未載入媒體，正在等待或修復串流")
            else:
                self.update_state_lights(mpv="yellow", detail="mpv probing media")
            if self.current_playability and self.current_playability.loading_hint:
                self.player_label.setText(
                    f"mpv 正在載入 Notion 串流\n"
                    f"{self.current_record.video_name}\n"
                    f"{self.current_playability.loading_hint}"
                )
            return
        self.mpv_idle_started_at = None
        self.mpv_idle_recovery_attempted = False
        self.update_state_lights(stream="green", mpv="green", detail="playing" if self.player_is_playing else "loaded")
        if self.current_playability and self.current_playability.loading_hint:
            self.player_label.setText(f"mpv 播放中\n{self.current_record.video_name}")
        if duration_sec and duration_sec > 0:
            self.position_slider.setRange(0, int(duration_sec))
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(int(position_sec))
        self.position_slider.blockSignals(False)
        self.update_live_playback_position(position_sec, duration_sec)
        self.highlight_subtitle(position_sec)
        runtime_ready = self.enforce_playback_runtime_readiness(position_sec)
        if self.playback_readiness_gate_enabled:
            self.refresh_playback_readiness_gate()
            self.maybe_autoplay_after_preheat("readiness_poll")
        self.maybe_prefetch_subtitles_for_position(position_sec)
        if not runtime_ready:
            self.refresh_detail_box()
            return
        if self.current_record.should_complete(position_sec, duration_sec, self.config.completion_threshold):
            self.mark_current_completed()
        self.refresh_detail_box()

    def update_position_label(self, position_sec: float | None, duration_sec: float | None) -> None:
        position_text = format_seconds(position_sec or 0.0)
        duration_text = format_seconds(duration_sec) if duration_sec and duration_sec > 0 else "--:--"
        self.position_time_label.setText(f"{position_text} / {duration_text}")

    def update_live_playback_position(self, position_sec: float | None, duration_sec: float | None) -> None:
        self.live_playback_position_sec = max(0.0, float(position_sec or 0.0))
        if duration_sec and duration_sec > 0:
            self.live_playback_duration_sec = float(duration_sec)
            if self.current_record is not None:
                self.current_record.duration_sec = float(duration_sec)
        elif self.current_record is not None and self.current_record.duration_sec:
            self.live_playback_duration_sec = float(self.current_record.duration_sec)
        else:
            self.live_playback_duration_sec = None
        self.update_position_label(self.live_playback_position_sec, self.live_playback_duration_sec)

    def live_playback_progress_percent(self) -> float:
        duration_sec = self.live_playback_duration_sec
        if not duration_sec or duration_sec <= 0:
            return 0.0
        return min(100.0, round(self.live_playback_position_sec / duration_sec * 100.0, 2))

    def highlight_subtitle(self, position_sec: float) -> None:
        cue = active_cue(self.cues, position_sec)
        if cue is None and self.load_cached_subtitle_for_position(position_sec):
            cue = active_cue(self.cues, position_sec)
        if cue is None:
            if not self.current_subtitle_generation_active():
                self.hide_caption_overlay()
            return
        self.active_subtitle_label.setText(cue.text)
        self.show_caption_overlay(cue.text)
        row = max(0, cue.index - 1)
        if row < self.subtitle_box.count() and self.subtitle_box.currentRow() != row:
            self.subtitle_box.setCurrentRow(row)

    def show_caption_overlay(self, text: str) -> None:
        channel = self.caption_output_channel()
        if channel is None:
            self.hide_caption_overlay()
            return
        value = text.strip()
        if not value:
            self.hide_caption_overlay()
            return
        if channel == "native":
            self.hide_caption_overlay()
            return
        if channel == "osd":
            self.hide_caption_overlay()
            self.show_mpv_caption(value)
            return
        self.caption_overlay.setText(value)
        self.caption_overlay.setHidden(False)
        self.player_click_overlay.raise_()

    def hide_caption_overlay(self) -> None:
        self.caption_overlay.setHidden(True)

    def show_mpv_caption(self, text: str) -> None:
        if not self.playback_core.available():
            return
        show_caption = getattr(self.playback_core, "show_caption", None)
        if not callable(show_caption):
            return
        try:
            show_caption(text, 1600)
        except Exception as exc:  # noqa: BLE001
            self.log(f"mpv caption overlay failed: {exc}")

    def position_caption_overlay(self) -> None:
        return

    def mark_current_completed(self) -> None:
        if self.current_record is None:
            return
        duration_sec = self.current_record.duration_sec
        if self.playback_core.available():
            try:
                duration_sec = self.playback_core.duration_sec() or duration_sec
            except Exception as exc:  # noqa: BLE001
                self.log(f"completion duration probe failed: {exc}")
        result = queue_completion_event(self.current_record, self.writeback, duration_sec)
        if duration_sec and duration_sec > 0:
            self.update_live_playback_position(duration_sec, duration_sec)
        self.store.save()
        self.refresh_writeback_count()
        self.log(f"completion {result.status}: {self.current_record.video_name} - {result.message}")
        self.refresh_current_item_text()
        self.refresh_detail_box()
        self.refresh_progress_overview()

    def refresh_detail_box(self) -> None:
        if (
            self.current_record is None
            or self.current_source is None
            or self.current_resolution is None
            or self.current_playability is None
        ):
            self.detail_box.setPlainText("尚未選取影片")
            return
        summary = build_video_detail_summary(
            record=self.current_record,
            source=self.current_source,
            resolution=self.current_resolution,
            playability=self.current_playability,
            subtitle_path=self.current_subtitle_path,
            cue_count=len(self.cues),
            display_position_sec=self.live_playback_position_sec,
            display_progress_percent=self.live_playback_progress_percent(),
        )
        self.detail_box.setPlainText(summary.to_text())

    def refresh_current_item_text(self) -> None:
        if self.current_record is None:
            return
        item = self.list_item_for_record_key(self.current_record.stable_key)
        if item is None:
            return
        item.setText(
            f"{self.current_record.course_date or 'no-date'}  "
            f"P{self.current_record.segment_index:02d}  "
            f"{self.current_record.status.value}  "
            f"{self.current_record.video_name}"
        )

    def list_item_for_record_key(self, record_key: str) -> QListWidgetItem | None:
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item and item.data(Qt.ItemDataRole.UserRole) == record_key:
                return item
        return None

    def closeEvent(self, event: object) -> None:  # noqa: N802 - Qt override name.
        self.playback_core.close()
        super().closeEvent(event)


def format_seconds(value: float) -> str:
    total = int(value)
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def state_light_icon(state: str) -> str:
    return {
        "green": "🟢",
        "yellow": "🟡",
        "red": "🔴",
        "idle": "⚪",
    }.get(state, "⚪")


def state_light_label(state: str) -> str:
    return {
        "green": "ready",
        "yellow": "working",
        "red": "blocked",
        "idle": "idle",
    }.get(state, str(state))


def playback_speed_options() -> tuple[tuple[str, float], ...]:
    return (
        ("0.5x", 0.5),
        ("0.75x", 0.75),
        ("1x", 1.0),
        ("1.25x", 1.25),
        ("1.5x", 1.5),
        ("2x", 2.0),
        ("3x", 3.0),
        ("4x", 4.0),
        ("6x", 6.0),
        ("8x", 8.0),
    )


def caption_mode_options() -> tuple[tuple[str, str], ...]:
    return (
        ("auto", "自動"),
        ("native", "mpv 原生字幕"),
        ("osd", "影片浮層字幕"),
        ("qt", "下方字幕條"),
        ("off", "關閉字幕"),
    )


def is_mpv_subtitle_path(path: object) -> bool:
    return str(Path(str(path)).suffix).lower() in {".srt", ".vtt", ".ass", ".ssa"}


def subtitle_file_covers_position(path: str | Path, position_sec: float) -> bool:
    try:
        return active_cue(load_subtitle(path), position_sec) is not None
    except Exception:
        return False


def subtitle_file_ahead_end_from_position(path: str | Path, position_sec: float) -> float:
    try:
        future_ends = [cue.end_sec for cue in load_subtitle(path) if cue.end_sec >= position_sec]
    except Exception:
        return 0.0
    if not future_ends:
        return 0.0
    return max(future_ends)


def writeback_status_text(result: FlushResult) -> str:
    if result.attempted == 0:
        return "沒有待送出的完成紀錄"
    if result.dry_run:
        return "完成紀錄尚未送出，請看事件欄"
    return f"完成紀錄送出：成功 {result.succeeded}，剩餘 {result.remaining}"


def writeback_count_text(count: int) -> str:
    return f"待送出完成紀錄：{count}"


def run_app(config: AppConfig | None = None) -> int:
    app = QApplication(sys.argv)
    window = M1MakeupPlayerWindow(config or load_app_config())
    window.show()
    return app.exec()

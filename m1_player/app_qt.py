from __future__ import annotations

import os
import sys

from dataclasses import replace
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
from .subtitle import SubtitleCue, active_cue
from .subtitle_generation import (
    DEFAULT_INITIAL_PROMPT,
    DEFAULT_TECHNICAL_HOTWORDS,
    SubtitleGenerationOptions,
    SubtitleGenerationResult,
    generate_subtitle_sidecar,
)
from .subtitle_manifest import write_missing_markdown_placeholders
from .subtitle_resolver import SubtitleResolver
from .sync_service import NotionScheduleSync, SyncResult
from .video_detail_summary import build_video_detail_summary
from .video_source import VideoSourceInfo, parse_video_source
from .writeback import WritebackOutbox
from .writeback_summary import collect_writeback_outbox_summary
from .writeback_sink import CompletionWritebackSink, FlushResult, flush_outbox


try:
    from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
    from PySide6.QtGui import QKeySequence, QShortcut
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


class PlayerSurface(QLabel):
    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event: object) -> None:  # noqa: N802 - Qt override name.
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event: object) -> None:  # noqa: N802 - Qt override name.
        super().resizeEvent(event)
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
    progress = Signal(str, int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        record: PlaybackRecord,
        media_ref: str,
        subtitle_dir: str,
        options: SubtitleGenerationOptions,
    ) -> None:
        super().__init__()
        self.record = record
        self.media_ref = media_ref
        self.subtitle_dir = subtitle_dir
        self.options = options

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(
                generate_subtitle_sidecar(
                    self.record,
                    self.media_ref,
                    self.subtitle_dir,
                    self.options,
                    progress_callback=self.emit_progress,
                )
            )
        except Exception as exc:  # noqa: BLE001 - UI boundary reports the failure.
            self.failed.emit(str(exc))

    def emit_progress(self, stage: str, percent: float | None, message: str) -> None:
        percent_value = -1 if percent is None else max(0, min(100, int(round(percent))))
        self.progress.emit(stage, percent_value, message)


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
        self.player_fullscreen = False
        self.pending_seek_sec: float | None = None
        self.pending_seek_key: str | None = None
        self.sync_thread: QThread | None = None
        self.sync_worker: SyncWorker | None = None
        self.writeback_thread: QThread | None = None
        self.writeback_worker: WritebackFlushWorker | None = None
        self.subtitle_generation_thread: QThread | None = None
        self.subtitle_generation_worker: SubtitleGenerationWorker | None = None

        self.setWindowTitle("m_1 Notion 補課播放器")
        self.resize(1280, 760)

        self.status_label = QLabel("啟動中")
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
        self.play_button = QPushButton("播放 / 暫停")
        self.fullscreen_button = QPushButton("全螢幕")
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
        self.cc_button = QPushButton("CC 開")
        self.cc_button.setCheckable(True)
        self.cc_button.setChecked(True)
        self.cc_button.setEnabled(False)
        self.position_slider = SeekSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_time_label = QLabel("00:00 / --:--")
        self.position_time_label.setMinimumWidth(110)
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
        self.player_label = PlayerSurface("播放器待命")
        self.player_label.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.player_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.player_label.setMinimumHeight(260)
        self.player_label.setStyleSheet("border: 1px solid #555; background: #111; color: #ddd;")
        self.player_click_overlay = PlayerDoubleClickOverlay(self.player_label)
        self.detail_box = QTextEdit()
        self.detail_box.setReadOnly(True)
        self.detail_box.setMaximumHeight(190)
        self.detail_box.setPlainText("尚未選取影片")

        self.position_timer = QTimer(self)
        self.position_timer.setInterval(1000)

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
        right_layout.addWidget(self.player_label, 3)
        self.detail_title = QLabel("目前影片狀態")
        right_layout.addWidget(self.detail_title)
        right_layout.addWidget(self.detail_box)
        position_row = QWidget()
        position_layout = QHBoxLayout(position_row)
        position_layout.setContentsMargins(0, 0, 0, 0)
        position_layout.addWidget(self.position_slider, 1)
        position_layout.addWidget(self.position_time_label)
        right_layout.addWidget(position_row)
        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.addWidget(self.play_button)
        controls_layout.addWidget(self.fullscreen_button)
        controls_layout.addWidget(QLabel("倍速"))
        controls_layout.addWidget(self.speed_combo)
        controls_layout.addWidget(self.cc_button)
        controls_layout.addWidget(self.complete_button)
        controls_layout.addWidget(self.subtitle_placeholder_button)
        controls_layout.addWidget(self.subtitle_generate_button)
        controls_layout.addWidget(self.flush_writeback_button)
        controls_layout.addWidget(self.writeback_count_label)
        right_layout.addWidget(controls)
        right_layout.addWidget(self.subtitle_progress_label)
        right_layout.addWidget(self.subtitle_progress_bar)
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
        self.player_click_overlay.raise_()

    def _connect_signals(self) -> None:
        self.sync_button.clicked.connect(self.start_sync)
        self.preflight_button.clicked.connect(self.run_local_preflight)
        self.api_settings_button.clicked.connect(self.open_api_settings_dialog)
        self.set_token_button.clicked.connect(self.prompt_notion_token)
        self.set_completion_source_button.clicked.connect(self.prompt_completion_data_source)
        self.set_schedule_view_button.clicked.connect(self.prompt_schedule_view_url)
        self.list_widget.itemSelectionChanged.connect(self.select_current_item)
        self.list_widget.itemDoubleClicked.connect(self.play_selected_item)
        self.play_button.clicked.connect(self.toggle_playback)
        self.fullscreen_button.clicked.connect(self.toggle_player_fullscreen)
        self.complete_button.clicked.connect(self.mark_current_completed)
        self.subtitle_placeholder_button.clicked.connect(self.create_current_subtitle_placeholder)
        self.subtitle_generate_button.clicked.connect(self.start_subtitle_generation)
        self.flush_writeback_button.clicked.connect(self.start_writeback_flush)
        self.speed_combo.currentIndexChanged.connect(self.change_playback_speed)
        self.cc_button.toggled.connect(self.change_subtitle_visibility)
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

    def log(self, message: str) -> None:
        self.log_box.append(message)

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

    def select_current_item(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        record = self.store.records.get(str(key))
        if record is None:
            return
        self.current_record = record
        self.pending_seek_sec = None
        self.pending_seek_key = None
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
        if playability.can_play and playability.playable_url:
            self.playback_core.load(playability.playable_url)
            self.apply_playback_speed()
            if record.last_position_sec > 0 and record.status != LessonStatus.COMPLETED:
                self.pending_seek_sec = record.last_position_sec
                self.pending_seek_key = record.stable_key
                self.log(f"resume queued: {format_seconds(record.last_position_sec)}")
            self.position_timer.start()
        else:
            self.position_timer.stop()
        self.player_label.setText(playability.player_label)
        for log_line in playability.log_lines:
            self.log(log_line)
        self.load_subtitles(record)
        self.update_position_label(record.last_position_sec, record.duration_sec)
        self.refresh_detail_box()
        self.status_label.setText(f"已選取：{record.video_name}")
        self.log(f"loaded video ref: {record.video_name}")

    def load_subtitles(self, record: PlaybackRecord) -> None:
        path, self.cues = self.subtitle_resolver.load_for(record)
        self.current_subtitle_path = path
        self.current_mpv_subtitle_path = None
        self.cc_button.setEnabled(False)
        self.subtitle_box.clear()
        if not self.cues:
            self.active_subtitle_label.setText("尚未載入字幕")
            self.subtitle_box.addItem("沒有找到本地字幕檔")
            return
        if path:
            record.subtitle_path = str(path)
            if is_mpv_subtitle_path(path):
                self.current_mpv_subtitle_path = str(path)
                self.cc_button.setEnabled(True)
                self.load_mpv_subtitle_track()
        self.active_subtitle_label.setText("等待播放位置")
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
    ) -> None:
        if self.subtitle_generation_thread is not None:
            self.log("subtitle generation skipped: already running")
            return
        if self.current_record is None:
            self.log("subtitle generation skipped: no selected video")
            return
        if self.current_playability is None or not self.current_playability.can_play or not self.current_playability.playable_url:
            self.log("subtitle generation skipped: no playable stream URL")
            return
        options = subtitle_generation_options_from_env()
        if start_sec is not None or max_duration_sec is not None or overwrite is not None:
            options = replace(
                options,
                start_sec=max(0.0, float(start_sec or 0.0)),
                max_duration_sec=max_duration_sec,
                overwrite=options.overwrite if overwrite is None else bool(overwrite),
            )
        self.status_label.setText("正在生成字幕，完成後會自動載入")
        self.subtitle_generate_button.setEnabled(False)
        self.log(
            "subtitle generation started: "
            f"trigger={trigger} "
            f"start={options.start_sec:g}s max={options.max_duration_sec or 'full'}s "
            f"model={options.model_size} language={options.language or 'auto'} "
            f"device={options.device} batch={options.batch_size}"
        )
        self.subtitle_generation_thread = QThread()
        self.subtitle_generation_worker = SubtitleGenerationWorker(
            self.current_record,
            self.current_playability.playable_url,
            str(self.config.subtitle_dir),
            options,
        )
        self.subtitle_generation_worker.moveToThread(self.subtitle_generation_thread)
        self.subtitle_generation_thread.started.connect(self.subtitle_generation_worker.run)
        self.subtitle_generation_worker.progress.connect(self.on_subtitle_generation_progress)
        self.subtitle_generation_worker.finished.connect(self.on_subtitle_generation_finished)
        self.subtitle_generation_worker.failed.connect(self.on_subtitle_generation_failed)
        self.subtitle_generation_worker.finished.connect(self.subtitle_generation_thread.quit)
        self.subtitle_generation_worker.failed.connect(self.subtitle_generation_thread.quit)
        self.subtitle_generation_thread.finished.connect(self.cleanup_subtitle_generation_worker)
        self.subtitle_progress_label.setHidden(False)
        self.subtitle_progress_bar.setHidden(False)
        self.subtitle_progress_bar.setRange(0, 0)
        self.subtitle_progress_label.setText("字幕解析：準備中")
        self.subtitle_generation_thread.start()

    @Slot(str, int, str)
    def on_subtitle_generation_progress(self, stage: str, percent: int, message: str) -> None:
        self.subtitle_progress_label.setHidden(False)
        self.subtitle_progress_bar.setHidden(False)
        if percent < 0:
            self.subtitle_progress_bar.setRange(0, 0)
        else:
            self.subtitle_progress_bar.setRange(0, 100)
            self.subtitle_progress_bar.setValue(percent)
        self.subtitle_progress_label.setText(f"字幕解析：{message}")
        if stage != "inference_segment":
            self.status_label.setText(f"字幕解析：{message}")

    @Slot(object)
    def on_subtitle_generation_finished(self, result: SubtitleGenerationResult) -> None:
        self.log(
            "subtitle generation "
            f"{result.status}: cues={result.cue_count} elapsed={result.elapsed_sec}s "
            f"decode={result.decode_elapsed_sec}s inference={result.inference_elapsed_sec}s "
            f"device={result.device}/{result.compute_type} "
            f"path={result.subtitle_path or 'missing'}"
        )
        if self.current_record and self.current_record.stable_key == result.record_key:
            if result.subtitle_path:
                self.current_record.subtitle_path = result.subtitle_path
                self.store.records[self.current_record.stable_key] = self.current_record
                self.store.save()
            self.load_subtitles(self.current_record)
            self.refresh_detail_box()
            self.status_label.setText("字幕已生成並載入")
        else:
            self.status_label.setText("字幕已生成，重新選取影片後載入")
        self.subtitle_progress_label.setHidden(False)
        self.subtitle_progress_bar.setHidden(False)
        self.subtitle_progress_bar.setRange(0, 100)
        self.subtitle_progress_bar.setValue(100)
        self.subtitle_progress_label.setText(f"字幕解析：完成，{result.cue_count} cues")
        self.run_mvp_readiness()

    @Slot(str)
    def on_subtitle_generation_failed(self, message: str) -> None:
        self.status_label.setText("字幕生成失敗，請看事件欄")
        self.subtitle_progress_label.setHidden(False)
        self.subtitle_progress_bar.setHidden(False)
        self.subtitle_progress_bar.setRange(0, 100)
        self.subtitle_progress_bar.setValue(0)
        self.subtitle_progress_label.setText("字幕解析：失敗")
        self.log(f"subtitle generation failed: {message}")

    @Slot()
    def cleanup_subtitle_generation_worker(self) -> None:
        self.subtitle_generate_button.setEnabled(True)
        self.subtitle_generation_thread = None
        self.subtitle_generation_worker = None

    def toggle_playback(self) -> None:
        if self.current_record is None or not self.playback_core.available():
            return
        self.maybe_start_timeline_subtitle_generation()
        try:
            self.playback_core.toggle_pause()
        except Exception as exc:  # noqa: BLE001 - UI boundary reports playback failure.
            self.log(f"playback toggle failed: {exc}")

    def play_selected_item(self, item: QListWidgetItem) -> None:
        if item is not self.list_widget.currentItem():
            self.list_widget.setCurrentItem(item)
        if self.current_record is None or not self.playback_core.available():
            return
        self.maybe_start_timeline_subtitle_generation()
        try:
            self.apply_playback_speed()
            self.playback_core.play()
        except Exception as exc:  # noqa: BLE001 - UI boundary reports playback failure.
            self.log(f"play selected failed: {exc}")

    def maybe_start_timeline_subtitle_generation(self) -> None:
        if self.current_record is None:
            return
        if self.current_playability is None or not self.current_playability.can_play:
            return
        if not self.current_playability.playable_url:
            return
        if self.subtitle_generation_thread is not None:
            return
        position_sec = self.current_playback_position_for_subtitles()
        if not subtitle_cues_need_generation(self.cues) and subtitle_cues_cover_position(self.cues, position_sec):
            return
        self.log("timeline subtitle generation requested: playback started without usable subtitles")
        window_sec = timeline_subtitle_window_sec_from_env()
        start_sec = max(0.0, position_sec - 5.0)
        self.start_subtitle_generation(
            trigger="playback_timeline",
            start_sec=start_sec,
            max_duration_sec=window_sec,
            overwrite=True,
        )

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
        if self.current_record is not None:
            return max(0.0, float(self.current_record.last_position_sec or 0.0))
        return 0.0

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
            self.update_position_label(float(value), duration_sec)
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

    def change_playback_speed(self, *_args: object) -> None:
        if not self.playback_core.available():
            return
        self.apply_playback_speed()

    def apply_playback_speed(self) -> None:
        try:
            self.playback_core.set_speed(self.selected_playback_speed())
        except Exception as exc:  # noqa: BLE001
            self.log(f"playback speed change failed: {exc}")

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
        ):
            widget.setHidden(enabled)
        self.fullscreen_button.setText("離開全螢幕" if enabled else "全螢幕")
        if enabled:
            self.showFullScreen()
        else:
            self.showNormal()
        self.player_click_overlay.setGeometry(self.player_label.rect())
        self.player_click_overlay.raise_()

    def exit_player_fullscreen(self) -> None:
        if self.player_fullscreen:
            self.apply_player_fullscreen(False)

    def load_mpv_subtitle_track(self) -> None:
        if not self.current_mpv_subtitle_path or not self.playback_core.available():
            return
        try:
            self.playback_core.load_subtitle(self.current_mpv_subtitle_path)
            self.playback_core.set_subtitle_visible(self.cc_button.isChecked())
        except Exception as exc:  # noqa: BLE001
            self.log(f"mpv subtitle track load failed: {exc}")

    def change_subtitle_visibility(self, *_args: object) -> None:
        if not self.playback_core.available():
            return
        try:
            self.playback_core.set_subtitle_visible(self.cc_button.isChecked())
        except Exception as exc:  # noqa: BLE001
            self.log(f"subtitle visibility change failed: {exc}")

    def poll_playback_position(self) -> None:
        if self.current_record is None or not self.playback_core.available():
            return
        try:
            position_sec = self.playback_core.position_sec()
            duration_sec = self.playback_core.duration_sec()
        except Exception as exc:  # noqa: BLE001
            self.log(f"playback poll failed: {exc}")
            return
        if position_sec is None:
            if self.current_playability and self.current_playability.loading_hint:
                self.player_label.setText(
                    f"mpv 正在載入 Notion 串流\n"
                    f"{self.current_record.video_name}\n"
                    f"{self.current_playability.loading_hint}"
                )
            return
        if self.current_playability and self.current_playability.loading_hint:
            self.player_label.setText(f"mpv 播放中\n{self.current_record.video_name}")
        if self.pending_seek_sec is not None and self.pending_seek_key == self.current_record.stable_key:
            try:
                self.playback_core.seek(self.pending_seek_sec)
                position_sec = self.pending_seek_sec
                self.log(f"resumed at: {format_seconds(position_sec)}")
            except Exception as exc:  # noqa: BLE001
                self.log(f"resume seek failed: {exc}")
            finally:
                self.pending_seek_sec = None
                self.pending_seek_key = None
        if duration_sec and duration_sec > 0:
            self.position_slider.setRange(0, int(duration_sec))
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(int(position_sec))
        self.position_slider.blockSignals(False)
        self.update_position_label(position_sec, duration_sec)
        self.current_record.update_position(position_sec, duration_sec)
        self.highlight_subtitle(position_sec)
        if self.current_record.should_complete(position_sec, duration_sec, self.config.completion_threshold):
            self.mark_current_completed()
        self.store.save()
        self.refresh_current_item_text()
        self.refresh_detail_box()
        self.refresh_progress_overview()

    def update_position_label(self, position_sec: float | None, duration_sec: float | None) -> None:
        position_text = format_seconds(position_sec or 0.0)
        duration_text = format_seconds(duration_sec) if duration_sec and duration_sec > 0 else "--:--"
        self.position_time_label.setText(f"{position_text} / {duration_text}")

    def highlight_subtitle(self, position_sec: float) -> None:
        cue = active_cue(self.cues, position_sec)
        if cue is None:
            return
        self.active_subtitle_label.setText(cue.text)
        row = max(0, cue.index - 1)
        if row < self.subtitle_box.count() and self.subtitle_box.currentRow() != row:
            self.subtitle_box.setCurrentRow(row)

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
        )
        self.detail_box.setPlainText(summary.to_text())

    def refresh_current_item_text(self) -> None:
        item = self.list_widget.currentItem()
        if item is None or self.current_record is None:
            return
        item.setText(
            f"{self.current_record.course_date or 'no-date'}  "
            f"P{self.current_record.segment_index:02d}  "
            f"{self.current_record.status.value}  "
            f"{self.current_record.video_name}"
        )

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


def is_mpv_subtitle_path(path: object) -> bool:
    return str(Path(str(path)).suffix).lower() in {".srt", ".vtt", ".ass", ".ssa"}


def subtitle_cues_need_generation(cues: list[SubtitleCue]) -> bool:
    if not cues:
        return True
    return all(is_placeholder_subtitle_text(cue.text) for cue in cues)


def subtitle_cues_cover_position(cues: list[SubtitleCue], position_sec: float) -> bool:
    return active_cue(cues, position_sec) is not None


def is_placeholder_subtitle_text(value: str) -> bool:
    normalized = value.strip()
    return normalized == "待補字幕"


def writeback_status_text(result: FlushResult) -> str:
    if result.attempted == 0:
        return "沒有待送出的完成紀錄"
    if result.dry_run:
        return "完成紀錄尚未送出，請看事件欄"
    return f"完成紀錄送出：成功 {result.succeeded}，剩餘 {result.remaining}"


def writeback_count_text(count: int) -> str:
    return f"待送出完成紀錄：{count}"


def subtitle_generation_options_from_env() -> SubtitleGenerationOptions:
    batch_size = _positive_int(os.environ.get("M1_WHISPER_BATCH_SIZE"), 8)
    beam_size = _positive_int(os.environ.get("M1_WHISPER_BEAM_SIZE"), 5)
    language = os.environ.get("M1_WHISPER_LANGUAGE", "zh").strip() or None
    return SubtitleGenerationOptions(
        model_size=os.environ.get("M1_WHISPER_MODEL", "medium").strip() or "medium",
        language=language,
        device=os.environ.get("M1_WHISPER_DEVICE", "auto").strip() or "auto",
        compute_type=os.environ.get("M1_WHISPER_COMPUTE_TYPE", "auto").strip() or "auto",
        batch_size=batch_size,
        beam_size=beam_size,
        overwrite=os.environ.get("M1_WHISPER_OVERWRITE", "").strip().lower() in {"1", "true", "yes"},
        output_suffix=os.environ.get("M1_WHISPER_OUTPUT_FORMAT", ".srt").strip() or ".srt",
        initial_prompt=os.environ.get("M1_WHISPER_INITIAL_PROMPT", DEFAULT_INITIAL_PROMPT).strip() or None,
        hotwords=os.environ.get("M1_WHISPER_HOTWORDS", DEFAULT_TECHNICAL_HOTWORDS).strip() or None,
    )


def timeline_subtitle_window_sec_from_env() -> float:
    try:
        return max(30.0, float(os.environ.get("M1_TIMELINE_SUBTITLE_WINDOW_SEC", "180")))
    except (TypeError, ValueError):
        return 180.0


def _positive_int(value: str | None, default: int) -> int:
    try:
        return max(1, int(str(value)))
    except (TypeError, ValueError):
        return default


def run_app(config: AppConfig | None = None) -> int:
    app = QApplication(sys.argv)
    window = M1MakeupPlayerWindow(config or load_app_config())
    window.show()
    return app.exec()

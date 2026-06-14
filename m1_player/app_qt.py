from __future__ import annotations

import os
import sys

from dataclasses import replace

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
    from PySide6.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
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
            self.finished.emit(generate_subtitle_sidecar(self.record, self.media_ref, self.subtitle_dir, self.options))
        except Exception as exc:  # noqa: BLE001 - UI boundary reports the failure.
            self.failed.emit(str(exc))


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
        self.cues: list[SubtitleCue] = []
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
        self.set_token_button = QPushButton("設定 token")
        self.set_completion_source_button = QPushButton("設定完成庫")
        self.set_schedule_view_button = QPushButton("設定課表")
        self.play_button = QPushButton("播放 / 暫停")
        self.complete_button = QPushButton("標記完成")
        self.subtitle_placeholder_button = QPushButton("建立字幕佔位")
        self.subtitle_placeholder_button.setHidden(True)
        self.subtitle_generate_button = QPushButton("生成字幕")
        self.subtitle_generate_button.setHidden(True)
        self.flush_writeback_button = QPushButton("送出完成紀錄")
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
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
        self.player_label = QLabel("播放器待命")
        self.player_label.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.player_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.player_label.setMinimumHeight(260)
        self.player_label.setStyleSheet("border: 1px solid #555; background: #111; color: #ddd;")
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
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.status_label)
        left_controls = QWidget()
        left_controls_layout = QHBoxLayout(left_controls)
        left_controls_layout.setContentsMargins(0, 0, 0, 0)
        left_controls_layout.addWidget(self.sync_button)
        left_controls_layout.addWidget(self.preflight_button)
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
        right_layout.addWidget(QLabel("目前影片狀態"))
        right_layout.addWidget(self.detail_box)
        right_layout.addWidget(self.position_slider)
        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.addWidget(self.play_button)
        controls_layout.addWidget(self.complete_button)
        controls_layout.addWidget(self.subtitle_placeholder_button)
        controls_layout.addWidget(self.subtitle_generate_button)
        controls_layout.addWidget(self.flush_writeback_button)
        controls_layout.addWidget(self.writeback_count_label)
        right_layout.addWidget(controls)
        right_layout.addWidget(self.writeback_summary_box)
        right_layout.addWidget(QLabel("字幕提詞"))
        right_layout.addWidget(self.active_subtitle_label)
        right_layout.addWidget(self.subtitle_box, 2)
        right_layout.addWidget(QLabel("事件"))
        right_layout.addWidget(self.log_box, 1)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([360, 920])
        self.setCentralWidget(splitter)

    def _connect_signals(self) -> None:
        self.sync_button.clicked.connect(self.start_sync)
        self.preflight_button.clicked.connect(self.run_local_preflight)
        self.set_token_button.clicked.connect(self.prompt_notion_token)
        self.set_completion_source_button.clicked.connect(self.prompt_completion_data_source)
        self.set_schedule_view_button.clicked.connect(self.prompt_schedule_view_url)
        self.list_widget.itemSelectionChanged.connect(self.select_current_item)
        self.play_button.clicked.connect(self.toggle_playback)
        self.complete_button.clicked.connect(self.mark_current_completed)
        self.subtitle_placeholder_button.clicked.connect(self.create_current_subtitle_placeholder)
        self.subtitle_generate_button.clicked.connect(self.start_subtitle_generation)
        self.flush_writeback_button.clicked.connect(self.start_writeback_flush)
        self.position_slider.sliderMoved.connect(self.seek_to_slider)
        self.subtitle_box.itemDoubleClicked.connect(self.seek_to_subtitle_item)
        self.position_timer.timeout.connect(self.poll_playback_position)

    def log(self, message: str) -> None:
        self.log_box.append(message)

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
        self.refresh_detail_box()
        self.status_label.setText(f"已選取：{record.video_name}")
        self.log(f"loaded video ref: {record.video_name}")

    def load_subtitles(self, record: PlaybackRecord) -> None:
        path, self.cues = self.subtitle_resolver.load_for(record)
        self.current_subtitle_path = path
        self.subtitle_box.clear()
        if not self.cues:
            self.active_subtitle_label.setText("尚未載入字幕")
            self.subtitle_box.addItem("沒有找到本地字幕檔")
            return
        if path:
            record.subtitle_path = str(path)
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

    def start_subtitle_generation(self, trigger: str = "manual") -> None:
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
        self.status_label.setText("正在生成字幕，完成後會自動載入")
        self.subtitle_generate_button.setEnabled(False)
        self.log(
            "subtitle generation started: "
            f"trigger={trigger} "
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
        self.subtitle_generation_worker.finished.connect(self.on_subtitle_generation_finished)
        self.subtitle_generation_worker.failed.connect(self.on_subtitle_generation_failed)
        self.subtitle_generation_worker.finished.connect(self.subtitle_generation_thread.quit)
        self.subtitle_generation_worker.failed.connect(self.subtitle_generation_thread.quit)
        self.subtitle_generation_thread.finished.connect(self.cleanup_subtitle_generation_worker)
        self.subtitle_generation_thread.start()

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
        self.run_mvp_readiness()

    @Slot(str)
    def on_subtitle_generation_failed(self, message: str) -> None:
        self.status_label.setText("字幕生成失敗，請看事件欄")
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

    def maybe_start_timeline_subtitle_generation(self) -> None:
        if self.current_record is None:
            return
        if self.current_playability is None or not self.current_playability.can_play:
            return
        if not self.current_playability.playable_url:
            return
        if self.subtitle_generation_thread is not None:
            return
        if not subtitle_cues_need_generation(self.cues):
            return
        self.log("timeline subtitle generation requested: playback started without usable subtitles")
        self.start_subtitle_generation(trigger="playback_timeline")

    def seek_to_slider(self, value: int) -> None:
        if not self.playback_core.available():
            return
        try:
            self.playback_core.seek(float(value))
        except Exception as exc:  # noqa: BLE001
            self.log(f"seek failed: {exc}")

    def seek_to_subtitle_item(self, item: QListWidgetItem) -> None:
        start_sec = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(start_sec, float):
            self.seek_to_slider(int(start_sec))

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
        self.current_record.update_position(position_sec, duration_sec)
        self.highlight_subtitle(position_sec)
        if self.current_record.should_complete(position_sec, duration_sec, self.config.completion_threshold):
            self.mark_current_completed()
        self.store.save()
        self.refresh_current_item_text()
        self.refresh_detail_box()
        self.refresh_progress_overview()

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


def subtitle_cues_need_generation(cues: list[SubtitleCue]) -> bool:
    if not cues:
        return True
    return all(is_placeholder_subtitle_text(cue.text) for cue in cues)


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

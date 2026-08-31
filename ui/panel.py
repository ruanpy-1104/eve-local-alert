"""控制面板（UI 层）。

基于 PySide6 实现：
- 启动引导：选择目标程序 -> 在预览画面中框选监控区域 -> 颜色选择/监控；
- 区域选择在控制面板的预览画面内完成（不再在被监控程序界面遮罩框选）；
- 预览画面自动适应控件尺寸，无需手动缩放；
- 启动 / 停止预警、实时预览、颜色选择（色块 + 识别程度）。
"""
from __future__ import annotations

import threading
import time
import winsound
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QRect, QRectF, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QImage, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.alerter import Alerter
from core.capture import Capture, client_size
from core.colors import COLOR_ORDER, DEFAULT_ALERT_COLORS, EVE_COLORS
from core.config import ConfigManager
from core.detector import Detector
from core.logger import get_logger
from core.paths import assets_dir
from core.region_selector import ROI
from core.window_locator import WindowInfo, WindowLocator, is_minimized
from ui import theme
from ui.preview import PreviewWidget
from ui.window_picker import TargetPickerDialog

logger = get_logger(__name__)

REFRESH_GEOMETRY_EVERY = 30  # 每 N 帧重新定位窗口，跟随移动 / 缩放
SELECTION_INTERVAL_MS = 150  # 框选模式下全窗口预览刷新间隔

# 识别程度档位 -> 描述文案（0/25/50/75/100 各档有独立描述）
_STRICT_LEVELS = {0: "宽松", 25: "较宽松", 50: "适中", 75: "较严格", 100: "严格"}


class _StrictScale(QWidget):
    """识别程度滑块下方的数字刻度（0/25/50/75/100）。

    直接按滑轨分数位置绘制，避免布局错位：左右各留出手柄半宽内边距，
    使两端刻度与滑轨端点、中间刻度与滑轨 25%/50%/75% 分位精确对齐。
    """

    _VALUES = (0, 25, 50, 75, 100)
    _INSET = 8  # 滑块手柄半宽，刻度端点对齐到滑轨端点

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(20)
        self.setMinimumWidth(220)
        # 水平撑满所在列（与滑块同宽），垂直固定
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setPen(QColor(theme.MUTED))
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        width = self.width()
        track = width - 2 * self._INSET
        box = 28.0
        for v in self._VALUES:
            x = self._INSET + v / 100.0 * track
            if v == 0:
                rect = QRectF(0, 0, box, self.height())
                painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, str(v))
            elif v == 100:
                rect = QRectF(width - box, 0, box, self.height())
                painter.drawText(rect, Qt.AlignRight | Qt.AlignVCenter, str(v))
            else:
                rect = QRectF(x - box / 2, 0, box, self.height())
                painter.drawText(rect, Qt.AlignHCenter | Qt.AlignVCenter, str(v))


def bgr_to_qimage(frame: np.ndarray) -> QImage:
    """BGR ndarray 转 QImage（拷贝数据，保证跨线程安全）。"""
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, _ = rgb.shape
    return QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()


class MonitorWorker(QThread):
    """后台监控线程：定位窗口 -> 捕获 ROI -> 检测红名 -> 触发警报。"""

    detected = Signal()       # 命中并触发警报
    preview = Signal(object)  # BGR 预览帧（numpy ndarray）
    status = Signal(str)      # 状态文本
    error = Signal(str)       # 致命错误
    alert_paused = Signal(bool)  # 播报暂停状态：True=已暂停，False=红名离开自动恢复
    window_lost = Signal(str)    # 目标窗口已关闭：监控结束，面板退回未选择状态
    window_minimized = Signal()  # 目标窗口已最小化：提醒玩家恢复窗口

    def __init__(self, config: ConfigManager, root_dir: Path, parent=None):
        super().__init__(parent)
        self._config = config
        self._root = root_dir
        self._stop = threading.Event()
        self._paused = threading.Event()  # 播报暂停：命中不报警，红名离开自动恢复
        self._alerting = False  # 当前是否正在播放警报（供 UI 线程只读查询）
        self._window_lost = False  # 目标窗口已关闭（抑制 run() 的"已停止"状态覆盖）
        self._paused_miss_start: float | None = None  # 暂停态下连续未命中起始时刻（自动恢复防抖）

    def stop(self) -> None:
        self._stop.set()

    def set_paused(self, paused: bool) -> None:
        """设置播报暂停状态（UI 线程调用，工作线程下一帧生效）。"""
        if paused:
            self._paused.set()
        else:
            self._paused.clear()
        self._paused_miss_start = None  # 暂停状态切换时重置自动恢复计时

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def is_alerting(self) -> bool:
        """当前是否正在播放警报（UI 线程读取，布尔赋值在 GIL 下原子）。"""
        return self._alerting

    def _notify_window_lost(self) -> None:
        """目标窗口已关闭：置位标志并通知面板退回「未选择目标」状态。"""
        self._window_lost = True
        self.window_lost.emit("目标程序窗口已关闭，请重新选择目标程序")

    def run(self) -> None:
        try:
            self._loop()
            # 仅自然结束时覆盖状态；手动停止 / 目标窗口关闭由面板自行设置后续状态，
            # 避免竞态覆盖。
            if not self._stop.is_set() and not self._window_lost:
                self.status.emit("已停止")
        except Exception as exc:  # noqa: BLE001
            logger.error("监控线程异常：%s", exc, exc_info=True)
            self.error.emit(str(exc))

    def _loop(self) -> None:
        cfg = self._config.data
        locator = WindowLocator(
            title_keyword=cfg["window"].get("title_keyword"),
            process_name=cfg["window"].get("process_name"),
        )
        interval = 1.0 / float(cfg["loop"].get("fps", 12))

        sound_file = cfg["alert"].get("sound_file")
        if sound_file:
            sound_file = str((self._root / sound_file).resolve())
        alerter = Alerter(sound_file)

        capture = Capture()
        detector: Detector | None = None
        det_cfg_key: str | None = None
        hit_count = 0
        frame_count = 0
        last_status = time.perf_counter()
        status_frames = 0
        minimized_reported = False
        try:
            # 定位失败（目标尚未打开 / 已关闭）：结束监控，由面板引导重新选择
            try:
                win = locator.find()
            except RuntimeError:
                self._notify_window_lost()
                return
            while not self._stop.is_set():
                started = time.perf_counter()

                # 检测参数变化时重建 Detector（保持时序确认状态）
                key = repr(sorted(cfg["detection"].items()))
                if key != det_cfg_key:
                    detector = Detector(**cfg["detection"])
                    det_cfg_key = key

                # 目标窗口可能已被关闭（枚举不到 / 句柄失效抛异常）：统一在此重新定位，
                # 仍失败则视为目标窗口关闭，结束监控由面板引导重新选择。
                try:
                    if frame_count % REFRESH_GEOMETRY_EVERY == 0:
                        win = locator.find()  # 窗口移动 / 缩放后自动跟随
                    minimized = is_minimized(win.handle)
                    cw, ch = client_size(win.handle)
                except Exception:
                    try:
                        win = locator.find()
                        minimized = is_minimized(win.handle)
                        cw, ch = client_size(win.handle)
                    except Exception:
                        self._notify_window_lost()
                        break

                # 目标窗口最小化：暂停捕获与检测，等待恢复（不中断监控）
                if minimized:
                    if self._alerting:
                        self._alerting = False
                        alerter.stop()
                    if not minimized_reported:
                        minimized_reported = True
                        self.window_minimized.emit()  # 提示音 + 面板置顶，提醒玩家恢复窗口
                        self.status.emit("目标窗口已最小化，无法监控，请恢复窗口")
                        self.preview.emit(None)  # 面板显示"已最小化"占位提示
                    time.sleep(interval)
                    continue
                minimized_reported = False

                roi = ROI(**cfg["roi"])
                region = roi.to_capture_region(0, 0, cw, ch)
                frame = capture.grab_window(win.handle, region)
                if frame is None or frame.size == 0:
                    # 捕获失败（异常状态）：跳过本帧，不中断监控
                    time.sleep(interval)
                    continue

                # 连续警报：识别到即持续播放，未识别立即停止（无冷却）。
                # 播报暂停时：即使命中也不报警；目标持续未出现达到设置延时后才自动恢复
                # （进出站黑屏等短暂无检测不会误解除暂停）。
                hit = detector.detect(frame)
                if hit:
                    self._paused_miss_start = None  # 目标仍在视野：重置无目标计时
                    if not self._alerting and not self._paused.is_set():
                        self._alerting = True
                        alerter.start()
                        hit_count += 1
                        self.detected.emit()
                    elif self._alerting and self._paused.is_set():
                        # 用户暂停时警报仍在播放中 → 停止（当前周期自然放完）
                        self._alerting = False
                        alerter.stop()
                elif self._alerting or self._paused.is_set():
                    if self._alerting:
                        self._alerting = False
                        alerter.stop()
                    if self._paused.is_set():
                        resume_delay = float(cfg["alert"].get("resume_delay", 10) or 0)
                        now = time.perf_counter()
                        if resume_delay <= 0 or (
                            self._paused_miss_start is not None
                            and now - self._paused_miss_start >= resume_delay
                        ):
                            # 目标已持续离开：自动恢复预警，下次命中重新报警
                            self._paused_miss_start = None
                            self._paused.clear()
                            self.alert_paused.emit(False)
                        elif self._paused_miss_start is None:
                            self._paused_miss_start = now

                # 预览叠加检测框，实时反馈识别结果（绿框 = 检测到目标颜色）
                vis = frame
                if detector.last_boxes:
                    vis = frame.copy()
                    for x, y, w, h in detector.last_boxes:
                        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 0), 2)
                self.preview.emit(vis)

                frame_count += 1
                status_frames += 1
                elapsed = time.perf_counter() - started
                now = time.perf_counter()
                if now - last_status >= 1.0:
                    fps = status_frames / max(now - last_status, 1e-6)
                    status_frames = 0
                    last_status = now
                    self.status.emit(f"监控中 | {fps:.1f} FPS | 命中 {hit_count} 次")
                time.sleep(max(0.0, interval - elapsed))
        finally:
            alerter.stop()  # 确保退出监控时停止警报
            capture.close()


class ColorPickerDialog(QDialog):
    """颜色选择：色块勾选警报颜色 + 识别程度（宽松~严格）滑块。"""

    def __init__(self, config: ConfigManager, parent=None):
        super().__init__(parent)
        self.config = config

        self.setWindowTitle("颜色选择 · 警报颜色")
        self.setFixedSize(520, 430)
        self._build_ui()

    # ---- UI ----
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        self.status_label = QLabel("勾选需要警报的颜色\n误报时往严格调，漏报时往宽松调")
        self.status_label.setObjectName("descriptionLabel")
        root.addWidget(self.status_label)

        # ---- 警报颜色（色块，勾选显示对勾） ----
        section_color = QLabel("警报颜色")
        section_color.setObjectName("sectionLabel")
        root.addWidget(section_color)
        palette_grid = QGridLayout()
        palette_grid.setHorizontalSpacing(10)
        palette_grid.setVerticalSpacing(10)
        self._color_buttons: dict[str, QPushButton] = {}
        for i, name in enumerate(COLOR_ORDER):
            info = EVE_COLORS[name]
            r, g, b = info["rgb"]
            luminance = 0.299 * r + 0.587 * g + 0.114 * b
            text_color = "black" if luminance > 150 else "white"
            btn = QPushButton("")
            btn.setCheckable(True)
            btn.setFixedSize(60, 40)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(f"{info['label']}（{name}）")
            btn.setStyleSheet(
                f"QPushButton {{ background-color: rgb({r},{g},{b}); color: {text_color};"
                f" border: 2px solid {theme.BORDER}; border-radius: 6px; font-weight: bold; }}"
                f"QPushButton:checked {{ border: 3px solid {theme.SUCCESS}; }}"
            )
            btn.clicked.connect(lambda _checked, n=name: self._on_color_toggled(n))
            palette_grid.addWidget(btn, i // 6, i % 6)
            self._color_buttons[name] = btn
        palette_row = QHBoxLayout()
        palette_row.addStretch()
        palette_row.addLayout(palette_grid)
        palette_row.addStretch()
        root.addLayout(palette_row)
        self._sync_colors_from_config()
        self._refresh_swatch_marks()

        # ---- 识别程度（左宽松 ~ 右严格，刻度紧贴滑块下方） ----
        section_strict = QLabel("识别程度")
        section_strict.setObjectName("sectionLabel")
        root.addWidget(section_strict)
        # 网格布局：第一行 宽松/滑块/严格/数值 与滑块垂直对齐，第二行刻度只在滑块列下方
        strict_grid = QGridLayout()
        strict_grid.setHorizontalSpacing(10)
        strict_grid.setVerticalSpacing(3)
        loose_lbl = QLabel("宽松")
        strict_lbl = QLabel("严格")
        loose_lbl.setObjectName("subtitleLabel")
        strict_lbl.setObjectName("subtitleLabel")
        self.strictness_slider = QSlider(Qt.Horizontal)
        self.strictness_slider.setRange(0, 100)
        self.strictness_slider.setSingleStep(25)
        self.strictness_slider.setPageStep(25)
        self.strictness_slider.setValue(int(self.config.data["detection"].get("strictness", 50)))
        self.strictness_slider.valueChanged.connect(self._on_strictness_changed)
        self.strictness_value = QLabel()
        self.strictness_value.setMinimumWidth(72)
        self.strictness_value.setAlignment(Qt.AlignCenter)
        strict_grid.addWidget(loose_lbl, 0, 0)
        strict_grid.addWidget(self.strictness_slider, 0, 1)
        strict_grid.addWidget(strict_lbl, 0, 2)
        strict_grid.addWidget(self.strictness_value, 0, 3)
        strict_grid.setColumnStretch(1, 1)  # 滑块列占满剩余空间
        strict_grid.addWidget(_StrictScale(), 1, 1)  # 刻度与滑块同列，居中于滑轨下方
        root.addLayout(strict_grid)
        self._update_strictness_label()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.apply_btn = QPushButton("应用")
        self.apply_btn.setProperty("role", theme.ROLE_PRIMARY)
        self.close_btn = QPushButton("关闭")
        self.apply_btn.setMinimumHeight(36)
        self.close_btn.setMinimumHeight(36)
        self.apply_btn.clicked.connect(self._apply)
        self.close_btn.clicked.connect(self.close)
        btn_row.addStretch()
        btn_row.addWidget(self.apply_btn)
        btn_row.addWidget(self.close_btn)
        root.addLayout(btn_row)

    # ---- 事件 ----
    def _active_colors(self) -> list[str]:
        """当前勾选的警报颜色。"""
        return [name for name, btn in self._color_buttons.items() if btn.isChecked()]

    def _refresh_swatch_marks(self) -> None:
        """勾选状态以色块上的对勾标记显示（色块不显示颜色名称）。"""
        for name, btn in self._color_buttons.items():
            btn.setText("✓" if btn.isChecked() else "")

    def _sync_colors_from_config(self) -> None:
        """按配置初始化颜色勾选状态。"""
        active = self.config.data["detection"].get("colors", list(DEFAULT_ALERT_COLORS))
        for name, btn in self._color_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(name in active)
            btn.blockSignals(False)

    def _update_strictness_label(self) -> None:
        value = self.strictness_slider.value()
        level = _STRICT_LEVELS.get(value, "适中")
        self.strictness_value.setText(f"{value} · {level}")

    def _on_strictness_changed(self, value: int) -> None:
        # 固定档位 0/25/50/75/100：拖动时吸附到最近档位
        snapped = round(value / 25) * 25
        if snapped != value:
            self.strictness_slider.blockSignals(True)
            self.strictness_slider.setValue(snapped)
            self.strictness_slider.blockSignals(False)
            value = snapped
        self._update_strictness_label()

    def _on_color_toggled(self, _name: str) -> None:
        """勾选颜色变化：刷新对勾标记。"""
        self._refresh_swatch_marks()

    def _apply(self) -> None:
        det = dict(self.config.data["detection"])
        det["colors"] = self._active_colors()
        det["strictness"] = self.strictness_slider.value()
        self.config.update("detection", det)
        self.status_label.setText("已应用")


class PauseSettingsDialog(QDialog):
    """暂停设置：自动恢复延时（暂停后目标连续未出现多少秒才自动解除暂停）。

    进出空间站等操作会造成数秒黑屏（检测不到目标），若恢复无延时会在黑屏瞬间
    误解除暂停并再次报警；调大延时可避免。设为 0 秒表示不延时（黑屏即恢复）。
    """

    def __init__(self, config: ConfigManager, parent=None):
        super().__init__(parent)
        self.config = config

        self.setWindowTitle("暂停设置 · 自动恢复延时")
        self.setFixedSize(440, 220)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        desc = QLabel("暂停后目标连续未出现多少秒才自动恢复预警")
        desc.setObjectName("descriptionLabel")
        desc.setWordWrap(True)
        root.addWidget(desc)

        row = QHBoxLayout()
        row.setSpacing(10)
        lbl = QLabel("自动恢复延时")
        lbl.setObjectName("subtitleLabel")
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 120)
        self.delay_spin.setSuffix(" 秒")
        self.delay_spin.setValue(int(self.config.data["alert"].get("resume_delay", 10)))
        row.addWidget(lbl)
        row.addWidget(self.delay_spin)
        row.addStretch()
        root.addLayout(row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.apply_btn = QPushButton("应用")
        self.apply_btn.setProperty("role", theme.ROLE_PRIMARY)
        self.close_btn = QPushButton("关闭")
        for btn in (self.apply_btn, self.close_btn):
            btn.setMinimumHeight(36)
        self.apply_btn.clicked.connect(self._apply)
        self.close_btn.clicked.connect(self.close)
        btn_row.addStretch()
        btn_row.addWidget(self.apply_btn)
        btn_row.addWidget(self.close_btn)
        root.addLayout(btn_row)

    def _apply(self) -> None:
        self.config.update("alert", {"resume_delay": self.delay_spin.value()})
        self.close()


class PreviewDialog(QDialog):
    """独立预警预览窗口：显示框选监控区域画面与识别信息。

    与监控线程独立运行：实时捕获 ROI 区域，叠加识别框与命中状态，
    供用户单独查看识别效果（不占用主面板预览）。
    """

    def __init__(self, config: ConfigManager, root_dir: Path, parent=None):
        super().__init__(parent)
        self.config = config
        self._root = root_dir
        self._capture = Capture()
        self._capture_closed = False  # closeEvent 后置位，下次打开时重建捕获实例
        self._locator = WindowLocator(
            title_keyword=config.data["window"].get("title_keyword"),
            process_name=config.data["window"].get("process_name"),
        )
        self._detector = Detector(**config.data["detection"])
        self._det_cfg_key: str | None = None
        self._win_cfg_key: str | None = None
        self._win = None
        self._tick_count = 0
        self._last_status = time.perf_counter()
        self._frames = 0

        self.setWindowTitle("预警预览")
        self.setMinimumSize(480, 320)
        self._saved_geometry: QRect | None = None  # 临时保存上次打开的大小 / 位置
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(167)  # ~6 FPS 预览（低帧率优化性能）
        self._timer.timeout.connect(self._tick)

    # ---- UI ----
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        self.preview = PreviewWidget()
        self.preview.set_placeholder("等待画面（确保目标程序已打开）")
        root.addWidget(self.preview, stretch=1)

        self.status_label = QLabel("预览：绿色框为识别到的目标颜色")
        self.status_label.setObjectName("statusLabel")
        root.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        self.close_btn = QPushButton("关闭")
        self.close_btn.setMinimumHeight(34)
        self.close_btn.clicked.connect(self.close)
        btn_row.addStretch()
        btn_row.addWidget(self.close_btn)
        root.addLayout(btn_row)

    # ---- 事件 ----
    def _tick(self) -> None:
        try:
            # 检测参数变化（颜色 / 识别程度）时重建检测器，保持与配置一致
            key = repr(sorted(self.config.data["detection"].items()))
            if key != self._det_cfg_key:
                self._detector = Detector(**self.config.data["detection"])
                self._det_cfg_key = key

            # 目标程序变化（重新选择程序）时重建定位器，保持与配置一致
            win_key = repr(sorted(self.config.data["window"].items()))
            if win_key != self._win_cfg_key:
                self._win_cfg_key = win_key
                self._locator = WindowLocator(
                    title_keyword=self.config.data["window"].get("title_keyword"),
                    process_name=self.config.data["window"].get("process_name"),
                )
                self._win = None  # 强制下一帧重新定位到新程序

            if self._win is None or self._tick_count % REFRESH_GEOMETRY_EVERY == 0:
                self._win = self._locator.find()
            self._tick_count += 1

            if is_minimized(self._win.handle):
                self.preview.clear()
                self.preview.set_placeholder("目标窗口已最小化，请恢复窗口")
                return

            cw, ch = client_size(self._win.handle)
            roi = ROI(**self.config.data["roi"])
            region = roi.to_capture_region(0, 0, cw, ch)
            frame = self._capture.grab_window(self._win.handle, region)
            if frame is None or frame.size == 0:
                return

            hit = self._detector.detect(frame)
            vis = frame.copy()
            for x, y, w, h in self._detector.last_boxes:
                cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 0), 2)
            self.preview.set_image(bgr_to_qimage(vis))

            self._frames += 1
            now = time.perf_counter()
            if now - self._last_status >= 0.5:
                fps = self._frames / max(now - self._last_status, 1e-6)
                self._frames = 0
                self._last_status = now
                state = "检测到目标颜色" if hit else "未检测到目标"
                self.status_label.setText(f"识别：{state} | {fps:.1f} FPS")
        except RuntimeError:
            self._win = None  # 窗口可能已关闭，下一帧重新定位
            self.status_label.setText("无法定位目标程序窗口")

    def show(self) -> None:
        """打开时恢复上次关闭前的大小与位置（重新选择程序后由 reset_geometry 清除）。"""
        if self._saved_geometry is not None:
            self.setGeometry(self._saved_geometry)
        super().show()

    def reset_geometry(self) -> None:
        """重新选择目标程序后，预览窗口恢复默认大小并居中于主面板。"""
        self._saved_geometry = None
        self.resize(self.minimumSize())
        parent = self.parentWidget()
        if parent is not None:
            geo = parent.geometry()
            self.move(
                max(0, geo.x() + (geo.width() - self.width()) // 2),
                max(0, geo.y() + (geo.height() - self.height()) // 2),
            )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 关闭后重新打开：底层 mss 实例已在 closeEvent 中释放，必须重建，
        # 否则独占全屏（PrintWindow 失效）回退到 mss 抓屏时会操作已释放的句柄。
        if self._capture_closed:
            self._capture = Capture()
            self._capture_closed = False
        if not self._timer.isActive():
            self._timer.start()

    def closeEvent(self, event) -> None:
        self._saved_geometry = self.geometry()
        self._timer.stop()
        self._capture.close()
        self._capture_closed = True
        super().closeEvent(event)


class ControlPanel(QWidget):
    """主控制面板：目标选择、预览内框选区域、监控启停与颜色选择。"""

    def __init__(self, config_path: Path):
        super().__init__()
        self.config = ConfigManager(config_path)
        self._root = assets_dir()  # 资源根目录：开发期为项目根，打包后为 PyInstaller 解包目录
        self._worker: MonitorWorker | None = None
        self._preview_dialog: PreviewDialog | None = None

        # 框选会话状态（在预览画面内完成区域选择）
        self._sel_target: WindowInfo | None = None
        self._sel_capture: Capture | None = None
        self._sel_timer: QTimer | None = None
        self._sel_frame: np.ndarray | None = None
        self._sel_done = False
        self._sel_roi: ROI | None = None
        # 「预览」可用状态：需已选择目标
        self._has_target = False

        # 预览自动适应控件尺寸；监控程序客户区宽高比用于面板等比缩放
        self._prog_aspect = 0.0
        self._resizing = False          # 防止 resizeEvent 递归
        self._controls_cache = 170      # 控件总高（布局生效后测量校准）

        self.setWindowTitle("EVE Local Alert")
        self.setWindowIcon(self._app_icon())
        # 注意：面板不做置顶，不改变其他窗口的 Z 序或前台状态，避免影响其他应用操作。
        self.setMinimumSize(520, 460)
        self.resize(520, 520)
        self._build_ui()
        self._update_preview_button()

    def _app_icon(self) -> QIcon:
        # 窗口 / 任务栏图标用多分辨率 ICO（16–256px），比 PNG 在 Windows 上更稳
        icon = QIcon(str(self._root / "assets" / "logo.ico"))
        return icon if not icon.isNull() else QIcon()

    # ---- UI ----
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        # 布局不约束窗口尺寸，由 resizeEvent 按监控程序比例等比控制
        root.setSizeConstraint(QVBoxLayout.SetNoConstraint)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(12)

        self.status_label = QLabel("请选择要监控的目标程序")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.preview = PreviewWidget()
        self.preview.set_placeholder("实时预览：选择目标程序后自动进入框选模式")
        self.preview.setMinimumSize(420, 260)
        self.preview.selection_finished.connect(self._on_selection_finished)
        root.addWidget(self.preview, stretch=1)

        # ---- 主操作行：开始预警 / 暂停预警 / 选择程序（三个等宽大按钮） ----
        main_row = QHBoxLayout()
        main_row.setSpacing(8)
        self.start_btn = QPushButton("开始预警")
        self.start_btn.setProperty("role", theme.ROLE_PRIMARY)
        self.pause_btn = QPushButton("暂停预警")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setToolTip(
            "点击后即使命中也不报警；目标持续未出现达设定延时后自动恢复（延时可在「暂停设置」调整）"
        )
        self.target_btn = QPushButton("选择程序")
        # 主功能：更大字号 + 更高按钮，与次要功能拉开明显层次
        row_font = self.start_btn.font()
        row_font.setPointSize(row_font.pointSize() + 2)
        for btn in (self.start_btn, self.pause_btn, self.target_btn):
            btn.setFont(row_font)
            btn.setMinimumHeight(46)
            main_row.addWidget(btn, stretch=1)  # 等宽排布
        root.addLayout(main_row)

        # ---- 第二行：其他功能 + GitHub（末尾留白，便于后续扩展） ----
        tool_row = QHBoxLayout()
        tool_row.setSpacing(8)
        self.color_btn = QPushButton("颜色选择")
        self.preview_btn = QPushButton("预览")
        self.test_btn = QPushButton("测试警报音")
        self.pause_settings_btn = QPushButton("暂停设置")
        self.pause_settings_btn.setToolTip(
            "设置暂停预警后自动恢复的延时（目标连续未出现多少秒后自动解除暂停）"
        )
        # 次要功能统一固定宽度，左侧紧凑排布，避免按钮撑满整行
        for btn in (
            self.color_btn,
            self.preview_btn,
            self.test_btn,
            self.pause_settings_btn,
        ):
            btn.setMinimumHeight(38)
            btn.setFixedWidth(104)
            tool_row.addWidget(btn)
        # 与 GitHub 图标之间留出弹性空白（≥ 一个按钮宽度），方便后续追加新功能按钮
        tool_row.addStretch(1)
        self.github_btn = QPushButton()
        self.github_btn.setIcon(QIcon(str(self._root / "assets" / "github.svg")))
        self.github_btn.setIconSize(QSize(20, 20))
        self.github_btn.setFixedSize(32, 32)
        self.github_btn.setProperty("role", theme.ROLE_GHOST)
        self.github_btn.setToolTip("GitHub 仓库")
        self.github_btn.setCursor(Qt.PointingHandCursor)
        self.github_btn.clicked.connect(self._open_github)
        tool_row.addWidget(self.github_btn)
        root.addLayout(tool_row)

        self.start_btn.clicked.connect(self._toggle_monitor)
        self.pause_btn.toggled.connect(self._on_pause_toggled)
        self.target_btn.clicked.connect(self._select_target)
        self.color_btn.clicked.connect(self._open_color_picker)
        self.preview_btn.clicked.connect(self._open_preview)
        self.test_btn.clicked.connect(self._test_alert)
        self.pause_settings_btn.clicked.connect(self._open_pause_settings)

    def _proportional_height(self) -> int:
        """按监控程序宽高比计算目标高度：控件高 + 预览(窗口宽 - 边距)/程序比例。"""
        m = self.layout().contentsMargins()
        pw = max(self.preview.minimumWidth(), self.width() - m.left() - m.right())
        ph = int(pw / self._prog_aspect)
        avail = self.screen().availableGeometry() if self.screen() else None
        max_h = int(avail.height() * 0.7) if avail else 900
        ph = max(self.preview.minimumHeight(), min(ph, max_h))
        return max(self.minimumHeight(), self._controls_cache + ph)

    def _set_program_aspect(self, cw: int, ch: int) -> None:
        """记录监控程序客户区宽高比，并把面板尺寸设为与程序比例匹配的值。"""
        if cw <= 0 or ch <= 0:
            return
        aspect = cw / ch
        if self._prog_aspect and abs(self._prog_aspect - aspect) < 0.05:
            return
        self._prog_aspect = aspect
        avail = self.screen().availableGeometry() if self.screen() else None
        max_w = int(avail.width() * 0.5) if avail else 980
        w = max(self.minimumWidth(), min(max_w, 980))
        self._resizing = True
        try:
            # 布局生效后测量控件总高（缓存），再按程序比例设定面板尺寸
            self.resize(w, self.height())
            self.layout().activate()
            self._controls_cache = max(60, self.height() - self.preview.height())
            target = self._proportional_height()
            self.resize(w, min(target, int(avail.height() * 0.85) if avail else target))
        finally:
            self._resizing = False

    # ---- 目标程序 ----
    def guide_target_selection(self) -> None:
        """启动引导：选择目标程序 -> 在预览中框选监控区域。"""
        self._select_target()

    def _make_locator(self) -> WindowLocator:
        cfg = self.config.data
        return WindowLocator(
            title_keyword=cfg["window"].get("title_keyword"),
            process_name=cfg["window"].get("process_name"),
        )

    def _select_target(self) -> None:
        prefer = None
        try:
            prefer = self._make_locator().find().handle
        except RuntimeError:
            pass
        dialog = TargetPickerDialog(self, prefer_handle=prefer)
        if dialog.exec() != QDialog.Accepted or dialog.picked is None:
            self.status_label.setText("未选择程序，可稍后点击「选择程序」")
            return
        win = self._on_target_selected(dialog.picked)
        if win is not None:
            self._start_selection()  # 选择完成后进入预览框选

    def _on_target_selected(self, win: WindowInfo) -> WindowInfo | None:
        """保存目标选择并返回重新定位的最新窗口；失败返回 None。"""
        # 保存选择：进程名 + 窗口标题，供后续定位与监控使用
        update: dict = {"title_keyword": win.title}
        if win.process_name:
            update["process_name"] = win.process_name
        self.config.update("window", update)
        # 重新选择程序后，预览窗口恢复默认大小与位置
        if self._preview_dialog is not None:
            self._preview_dialog.reset_geometry()

        try:
            win = self._make_locator().find()
        except RuntimeError:
            self.status_label.setText("目标程序窗口已关闭，请重新选择")
            return None
        cw, ch = client_size(win.handle)
        self._set_program_aspect(cw, ch)
        self._has_target = True
        self._update_preview_button()
        self.status_label.setText(f"已选择目标：{win.title}，请在预览画面中框选监控区域")
        return win

    def _ensure_target(self) -> WindowInfo | None:
        """确保有匹配的目标窗口；没有则引导用户选择，返回 None 表示放弃。"""
        try:
            return self._make_locator().find()
        except RuntimeError:
            dialog = TargetPickerDialog(self)
            if dialog.exec() != QDialog.Accepted or dialog.picked is None:
                return None
            return self._on_target_selected(dialog.picked)

    # ---- 预览内框选区域 ----
    def _start_selection(self) -> None:
        """进入框选模式：预览显示目标窗口全画面，拖拽框选监控区域。"""
        if self._worker is not None and self._worker.isRunning():
            self._stop_monitor()  # 避免监控预览覆盖框选画面
        win = self._ensure_target()
        if win is None:
            return
        self._end_selection()  # 清残留状态
        self._sel_target = win
        self._sel_capture = Capture()
        self._sel_timer = QTimer(self)
        self._sel_timer.setInterval(SELECTION_INTERVAL_MS)
        self._sel_timer.timeout.connect(self._sel_tick)
        self._sel_timer.start()
        self.preview.enable_selection(True)
        self.preview.setFocus()
        self.status_label.setText("在预览画面中拖动鼠标框选监控区域")
        self._sel_tick()

    def _sel_tick(self) -> None:
        """刷新全窗口预览，并标注当前监控区域。"""
        if self._sel_target is None or self._sel_capture is None:
            return
        try:
            self._sel_target = self._make_locator().find()  # 跟随窗口移动 / 缩放
        except RuntimeError:
            self._end_selection("目标程序窗口已关闭")
            return
        # 窗口最小化时无法捕获内容：显示占位提示，恢复后自动继续框选预览
        if is_minimized(self._sel_target.handle):
            self.preview.clear()
            self.preview.set_placeholder("目标窗口已最小化，请恢复窗口")
            return
        cw, ch = client_size(self._sel_target.handle)
        self._set_program_aspect(cw, ch)
        region = {"left": 0, "top": 0, "width": cw, "height": ch}
        frame = self._sel_capture.grab_window(self._sel_target.handle, region)
        if frame is None or frame.size == 0:
            return  # 捕获失败：保持当前画面，等待下一帧
        self._sel_frame = frame

        fh, fw = frame.shape[:2]
        roi = self._sel_roi if self._sel_done else ROI(**self.config.data["roi"])
        sel = QRect(int(roi.x * fw), int(roi.y * fh), int(roi.width * fw), int(roi.height * fh))
        self.preview.set_selection(sel)
        self.preview.set_image(bgr_to_qimage(frame))

    def _on_selection_finished(self, rect_img: QRect) -> None:
        """用户在预览中框选完成：换算归一化 ROI 并保存。"""
        if self._sel_frame is None:
            return
        fh, fw = self._sel_frame.shape[:2]
        if fw <= 0 or fh <= 0:
            return
        roi = ROI(
            x=rect_img.left() / fw,
            y=rect_img.top() / fh,
            width=rect_img.width() / fw,
            height=rect_img.height() / fh,
        )
        # 最小支持 2x2 像素选区
        if roi.width < 2 / fw or roi.height < 2 / fh:
            self.status_label.setText("选区过小，请重新框选")
            return
        self.config.update(
            "roi",
            {"x": roi.x, "y": roi.y, "width": roi.width, "height": roi.height},
        )
        self._sel_done = True
        self._sel_roi = roi
        self.status_label.setText(
            f"监控区域已保存: ({roi.x:.2f}, {roi.y:.2f}, {roi.width:.2f}, {roi.height:.2f})"
        )

    def _end_selection(self, message: str = "") -> None:
        """结束框选会话，释放捕获资源并清除残留选区。"""
        if self._sel_timer is not None:
            self._sel_timer.stop()
            self._sel_timer = None
        if self._sel_capture is not None:
            self._sel_capture.close()
            self._sel_capture = None
        self._sel_target = None
        self._sel_frame = None
        self._sel_done = False
        self._sel_roi = None
        self.preview.enable_selection(False)
        self.preview.set_selection(None)  # 清除残留选区叠加层，避免污染监控预览
        if message:
            self.status_label.setText(message)

    # ---- 监控 ----
    def _toggle_monitor(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._stop_monitor()
            self._start_selection()  # 退出监控后自动进入框选模式
        else:
            self._start_monitor()

    def _start_monitor(self) -> None:
        self._end_selection()  # 先结束框选会话，避免预览冲突
        win = self._ensure_target()
        if win is None:
            return
        cw, ch = client_size(win.handle)
        self._set_program_aspect(cw, ch)
        # 通过窗口内容捕获（PrintWindow）监控所选窗口，不改变窗口 Z 序 / 前台状态。
        self._worker = MonitorWorker(self.config, self._root, self)
        self._worker.preview.connect(self._on_preview)
        self._worker.status.connect(self.status_label.setText)
        self._worker.detected.connect(self._on_detected)
        self._worker.error.connect(self._on_error)
        self._worker.alert_paused.connect(self._on_alert_paused)
        self._worker.window_lost.connect(self._on_window_lost)
        self._worker.window_minimized.connect(self._on_window_minimized)
        self._worker.start()
        self.start_btn.setText("停止预警")
        # 每次启动监控重置播报暂停状态
        self.pause_btn.blockSignals(True)
        self.pause_btn.setChecked(False)
        self.pause_btn.setText("暂停预警")
        self.pause_btn.blockSignals(False)
        self.pause_btn.setEnabled(True)
        self.status_label.setText("正在定位窗口...")

    def _stop_monitor(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker.wait(2000)
            self._worker = None
        self.start_btn.setText("开始预警")
        self.pause_btn.setEnabled(False)
        self.status_label.setText("已停止预警")

    def _on_preview(self, frame) -> None:
        if frame is None:
            self.preview.clear()
            self.preview.set_placeholder("目标窗口已最小化，请恢复窗口")
            return
        self.preview.set_image(bgr_to_qimage(frame))

    def _on_detected(self) -> None:
        self.status_label.setStyleSheet(
            f"color:{theme.DANGER};font-weight:bold;font-size:13px;"
        )
        self.status_label.setText("检测到目标颜色，正在警报！")
        QTimer.singleShot(1200, self._restore_status_style)

    def _on_error(self, message: str) -> None:
        logger.error("监控出错：%s", message)
        self.status_label.setStyleSheet(
            f"color:{theme.WARN};font-weight:bold;font-size:13px;"
        )
        self.status_label.setText(f"错误：{message}")
        self.start_btn.setText("开始预警")
        self.pause_btn.setEnabled(False)

    def _on_window_lost(self, message: str) -> None:
        """目标窗口已关闭：退回「未选择目标」界面，引导用户重新选择程序后开始预警。"""
        logger.warning("监控目标窗口已关闭：%s", message)
        self.status_label.setStyleSheet("")
        self.status_label.setText("目标程序窗口已关闭，请点击「选择程序」重新选择，再点击「开始预警」")
        self.preview.clear()
        self.preview.set_placeholder("实时预览：选择目标程序后自动进入框选模式")
        self.start_btn.setText("开始预警")
        self.pause_btn.setEnabled(False)
        self._has_target = False
        self._update_preview_button()

    def _on_window_minimized(self) -> None:
        """目标窗口最小化：播放提示音并置顶面板提醒玩家（不抢键盘焦点，不提示窗口关闭）。"""
        try:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
        self.raise_()

    # ---- 播报暂停 ----
    def _on_pause_toggled(self, checked: bool) -> None:
        """用户点击「暂停预警」：即使命中也不报警，红名离开后自动恢复。"""
        if self._worker is None or not self._worker.isRunning():
            # 未监控时忽略（按钮已禁用，双保险）
            self.pause_btn.blockSignals(True)
            self.pause_btn.setChecked(False)
            self.pause_btn.setText("暂停预警")
            self.pause_btn.blockSignals(False)
            return
        self._worker.set_paused(checked)
        self.pause_btn.setText("预警已暂停" if checked else "暂停预警")
        if checked:
            self.status_label.setText("预警已暂停：命中不再报警，目标持续未出现达设定延时后自动恢复")

    def _on_alert_paused(self, paused: bool) -> None:
        """工作线程同步暂停状态（红名离开自动恢复时取消按钮勾选）。"""
        if self._worker is None or not self._worker.isRunning():
            return
        self.pause_btn.blockSignals(True)
        self.pause_btn.setChecked(paused)
        self.pause_btn.setText("预警已暂停" if paused else "暂停预警")
        self.pause_btn.blockSignals(False)
        if not paused:
            self.status_label.setText("红名已离开，预警已自动恢复")

    def _restore_status_style(self) -> None:
        self.status_label.setStyleSheet("")

    # ---- 颜色选择 / 预览 ----
    def _open_color_picker(self) -> None:
        dialog = ColorPickerDialog(self.config, self)
        dialog.exec()

    def _open_pause_settings(self) -> None:
        """打开暂停设置：自动恢复延时（秒）。"""
        dialog = PauseSettingsDialog(self.config, self)
        dialog.exec()

    def _update_preview_button(self) -> None:
        """「预览」仅在已选择目标时可点击。"""
        self.preview_btn.setEnabled(self._has_target)

    def _open_preview(self) -> None:
        """打开独立预览窗口：显示框选监控区域画面与识别信息。"""
        if self._preview_dialog is None:
            self._preview_dialog = PreviewDialog(self.config, self._root, self)
        self._preview_dialog.show()
        self._preview_dialog.raise_()
        self._preview_dialog.activateWindow()

    def _open_github(self) -> None:
        """打开项目 GitHub 仓库。"""
        QDesktopServices.openUrl(QUrl("https://github.com/ruanpy-1104/eve-local-alert"))

    def _test_alert(self) -> None:
        """测试警报音：播放一个完整周期后停止。"""
        if (
            self._worker is not None
            and self._worker.isRunning()
            and self._worker.is_alerting()
        ):
            # 监控警报正在播放：PlaySound(SND_PURGE) 按进程停止所有声音，
            # 会误杀监控警报且其 _alerting 状态不会自动恢复，故此时禁止测试。
            self.status_label.setText("监控警报正在播放，请先「暂停预警」再测试")
            return
        sound_file = self.config.data["alert"].get("sound_file")
        path = str((self._root / sound_file).resolve()) if sound_file else None
        alerter = Alerter(path)
        alerter.start()
        self._test_alerter = alerter
        self.status_label.setText("正在播放警报音...")
        # 略早于周期结束触发 stop：使周期线程判定「已停止」而不续播，正好播放一个完整周期
        QTimer.singleShot(
            max(200, int(alerter.cycle_seconds * 1000) - 50),
            lambda: (alerter.stop(), self.status_label.setText("警报音测试完成")),
        )

    def closeEvent(self, event) -> None:
        self._end_selection()
        if self._preview_dialog is not None:
            self._preview_dialog.close()
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(2000)
        super().closeEvent(event)

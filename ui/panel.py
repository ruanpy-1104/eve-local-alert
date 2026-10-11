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
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
from core.notifier import (
    SERVERCHAN_REGISTER_URL,
    mark_enemy_gone,
    send_alert_async,
    send_serverchan,
)
from core.paths import assets_dir
from core.region_selector import ROI
from core.window_locator import WindowInfo, WindowLocator, is_minimized
from ui import theme
from ui.preview import PreviewWidget
from ui.window_picker import TargetPickerDialog

logger = get_logger(__name__)

REFRESH_GEOMETRY_EVERY = 30  # 每 N 帧重新定位窗口，跟随移动 / 缩放
SELECTION_INTERVAL_MS = 150  # 框选模式下全窗口预览刷新间隔

# 识别程度档位 -> 描述文案（三档 0/50/100）
_STRICT_LEVELS = {0: "宽松", 50: "适中", 100: "严格"}


class _StrictScale(QWidget):
    """识别程度滑块下方的数字刻度（0/50/100）。

    直接按滑轨分数位置绘制，避免布局错位：左右各留出手柄半宽内边距，
    使两端刻度与滑轨端点、中间刻度与滑轨 50% 分位精确对齐。
    水平撑满所在空间（与滑块同宽），保证刻度与滑轨对齐。
    """

    _VALUES = (0, 50, 100)
    _INSET = 8  # 滑块手柄半宽，刻度端点对齐到滑轨端点

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(20)
        # 水平撑满（与滑块同宽），垂直固定；不设最小宽，避免限制滑块扩展
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

    preview = Signal(object)  # BGR 预览帧（numpy ndarray）
    status = Signal(str)      # 状态文本（富文本：敌袭/安全带颜色）
    error = Signal(str)       # 致命错误
    alert_paused = Signal(bool)  # 播报暂停状态：True=已暂停，False=达最短时长且目标不在视野后自动恢复
    window_lost = Signal(str)    # 目标窗口已关闭：监控结束，面板退回未选择状态
    window_minimized = Signal()  # 目标窗口已最小化：提醒玩家恢复窗口

    def __init__(
        self,
        config: ConfigManager,
        root_dir: Path,
        parent=None,
        target_handle: int | None = None,
    ):
        super().__init__(parent)
        self._config = config
        self._root = root_dir
        # 用户点选的目标窗口句柄：只跟踪这个窗口，窗口关闭即结束监控（见 WindowLocator）
        self._target_handle = target_handle
        self._stop = threading.Event()
        self._paused = threading.Event()  # 播报暂停：命中不报警，达最短时长且目标不在视野后自动恢复
        self._alerting = False  # 当前是否正在播放警报（供 UI 线程只读查询）
        self._window_lost = False  # 目标窗口已关闭（抑制 run() 的"已停止"状态覆盖）
        self._paused_since: float | None = None  # 暂停开始时刻（最短持续时长计时起点）
        self._last_hit = False  # 最近一帧命中状态（供顶部状态栏即时刷新）
        self._last_fps = 0.0  # 最近一次状态栏 FPS（供顶部状态栏即时刷新）

    def _status_text(self, fps: float, hit: bool) -> str:
        """监控状态栏富文本。

        暂停中：状态标签为黄色加粗「已暂停」（不随命中闪烁）；
        未暂停：命中「敌袭」红色加粗增大，未命中「安全」绿色正常大小。
        前缀「监控中 | FPS」不随暂停 / 命中状态改变样式。
        """
        if self._paused.is_set():
            state = (
                f'<span style="color:{theme.YELLOW};font-weight:bold;font-size:15px;">已暂停</span>'
            )
        elif hit:
            state = (
                f'<span style="color:{theme.DANGER};font-weight:bold;font-size:15px;">敌袭</span>'
            )
        else:
            state = f'<span style="color:{theme.SUCCESS};">安全</span>'
        return f"监控中 | {fps:.1f} FPS | {state}"

    def refresh_status(self) -> None:
        """按当前暂停 / 命中状态立即补发一条顶部状态栏文本。

        状态切换（如面板点击暂停 / 恢复）时调用，不必等下一周期（最长 1 秒）
        才看到状态变化。
        """
        self.status.emit(self._status_text(self._last_fps, self._last_hit))

    def _approx_fps(self, status_frames: int, last_status: float) -> float:
        """当前刷新窗口内的近似 FPS（状态切换时立即刷新状态栏用）。"""
        return status_frames / max(time.perf_counter() - last_status, 1e-6)

    def stop(self) -> None:
        self._stop.set()

    def set_paused(self, paused: bool) -> None:
        """设置播报暂停状态（UI 线程调用，工作线程下一帧生效）。"""
        if paused:
            self._paused_since = time.perf_counter()  # 最短持续时长计时起点
            self._paused.set()
        else:
            self._paused.clear()
            self._paused_since = None

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
            prefer_handle=self._target_handle,
        )
        interval = 1.0 / float(cfg["loop"].get("fps", 12))
        self._last_fps = 1.0 / interval  # 首个周期前的 FPS 占位（供暂停即时刷新）

        sound_file = cfg["alert"].get("sound_file")
        if sound_file:
            sound_file = str((self._root / sound_file).resolve())
        alerter = Alerter(sound_file)

        capture = Capture()
        detector: Detector | None = None
        det_cfg_key: str | None = None
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
                # 播报暂停时：即使命中也不报警；暂停至少持续设定时长（resume_delay），
                # 时长满后目标不在视野才自动恢复——进出空间站等短暂无检测不会提前解除。
                hit = detector.detect(frame)
                self._last_hit = hit  # 供状态栏「敌袭/安全/已暂停」实时指示
                if hit:
                    if not self._alerting and not self._paused.is_set():
                        self._alerting = True
                        alerter.start()
                        # 命中：立即刷新状态栏「敌袭」（红色加粗增大），不等每秒刷新
                        self._last_fps = self._approx_fps(status_frames, last_status)
                        self.status.emit(self._status_text(self._last_fps, True))
                        # 远程预警（默认关闭）：仅在本轮警报开始时发一条微信提醒；
                        # 发送需同时满足内置 5 秒冷却与「敌方已消失」，独立线程异步发送
                        send_alert_async(cfg.get("remote_alert") or {})
                    elif self._alerting and self._paused.is_set():
                        # 用户暂停时警报仍在播放中 → 停止（当前周期自然放完）
                        self._alerting = False
                        alerter.stop()
                else:
                    # 敌方消失（本帧未命中）：上报给远程提醒模块，作为再次触发的前提条件
                    mark_enemy_gone()
                    if self._alerting:
                        self._alerting = False
                        alerter.stop()
                        # 未命中：立即刷新状态栏「安全」（绿色正常大小），不等每秒刷新
                        self._last_fps = self._approx_fps(status_frames, last_status)
                        self.status.emit(self._status_text(self._last_fps, False))
                    if self._paused.is_set():
                        resume_delay = float(cfg["alert"].get("resume_delay", 10) or 0)
                        # 暂停至少持续设定时长，且当前无目标时才自动恢复（目标在视野时永不恢复）
                        now = time.perf_counter()
                        if resume_delay <= 0 or (
                            self._paused_since is not None
                            and now - self._paused_since >= resume_delay
                        ):
                            self._paused_since = None
                            self._paused.clear()
                            self.alert_paused.emit(False)

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
                    self._last_fps = status_frames / max(now - last_status, 1e-6)
                    status_frames = 0
                    last_status = now
                    # 状态栏富文本：暂停「已暂停」黄粗 / 命中「敌袭」红粗增大 / 未命中「安全」绿正常；前缀不变
                    self.status.emit(self._status_text(self._last_fps, self._last_hit))
                time.sleep(max(0.0, interval - elapsed))
        finally:
            alerter.stop()  # 确保退出监控时停止警报
            capture.close()


class ColorPickerDialog(QDialog):
    """颜色选择：色块勾选警报颜色 + 识别程度（三档滑块）。

    布局：标题作为内容区块的纯文字标题（无边框、位于区块外侧上方）；
    4 个色块等宽平铺在浅色卡片容器内（卡片本身含边框，色块上显示颜色名，勾选时
    前缀 ✓）；识别程度为一行滑块（无卡片框，仅轨道有颜色，其余与背景一致）。
    """

    _CARD_QSS = (
        f"QFrame {{ background-color: {theme.SURFACE};"
        f" border: 1px solid {theme.BORDER}; border-radius: 10px; }}"
    )

    def __init__(self, config: ConfigManager, parent=None):
        super().__init__(parent)
        self.config = config

        self.setWindowTitle("颜色选择 · 警报颜色")
        self.setFixedSize(520, 360)
        self._build_ui()

    # ---- UI ----
    def _make_section_title(self, text: str) -> QLabel:
        """内容区块上方的纯文字标题（无边框）。"""
        label = QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(14)

        desc = QLabel("勾选需要警报的颜色，误报时往严格调，漏报时往宽松调")
        desc.setObjectName("descriptionLabel")
        desc.setWordWrap(True)
        root.addWidget(desc)

        # ---- 警报颜色：标题在卡片外，卡片内为一行等宽色块 ----
        root.addWidget(self._make_section_title("警报颜色"))
        color_group = QFrame()
        color_group.setStyleSheet(self._CARD_QSS)
        color_box = QVBoxLayout(color_group)
        color_box.setContentsMargins(14, 12, 14, 12)
        color_box.setSpacing(10)

        palette_grid = QGridLayout()
        palette_grid.setHorizontalSpacing(10)
        self._color_buttons: dict[str, QPushButton] = {}
        for i, name in enumerate(COLOR_ORDER):
            btn = self._make_swatch(name)
            # 单行平铺：色块均分卡片宽度（列数与颜色数一致，不靠魔法数列）
            palette_grid.addWidget(btn, 0, i)
            palette_grid.setColumnStretch(i, 1)
            self._color_buttons[name] = btn
        color_box.addLayout(palette_grid)
        root.addWidget(color_group)
        self._sync_colors_from_config()
        self._refresh_swatch_marks()

        # ---- 识别程度：标题为纯文字（无卡片），滑块行无边框、仅轨道有颜色 ----
        root.addWidget(self._make_section_title("识别程度"))

        loose_lbl = QLabel("宽松")
        strict_lbl = QLabel("严格")
        loose_lbl.setObjectName("subtitleLabel")
        strict_lbl.setObjectName("subtitleLabel")
        self.strictness_slider = QSlider(Qt.Horizontal)
        self.strictness_slider.setRange(0, 100)
        self.strictness_slider.setSingleStep(50)
        self.strictness_slider.setPageStep(50)
        self.strictness_slider.setMinimumHeight(24)
        self.strictness_slider.setValue(
            int(self.config.data["detection"].get("strictness", 50))
        )
        self.strictness_slider.valueChanged.connect(self._on_strictness_changed)
        self.strictness_value = QLabel()
        self.strictness_value.setMinimumWidth(76)
        self.strictness_value.setAlignment(Qt.AlignCenter)
        self.strictness_value.setContentsMargins(12, 0, 0, 0)  # 增大「严格」与「0·宽松」间距
        # 滑块长度减半：与下方刻度统一固定宽；「宽松 / 严格 / 数值」与滑块轨道同列对齐
        #（垂直居中于滑块行），刻度单独一行放在滑块正下方。
        self.strictness_slider.setFixedWidth(150)
        self._strict_scale = _StrictScale()
        self._strict_scale.setFixedWidth(150)
        strict_grid = QGridLayout()
        strict_grid.setHorizontalSpacing(10)
        strict_grid.setVerticalSpacing(4)
        strict_grid.addWidget(loose_lbl, 0, 0, Qt.AlignVCenter)
        strict_grid.addWidget(self.strictness_slider, 0, 1, Qt.AlignVCenter)
        strict_grid.addWidget(strict_lbl, 0, 2, Qt.AlignVCenter)
        strict_grid.addWidget(self.strictness_value, 0, 3, Qt.AlignVCenter)
        strict_grid.addWidget(self._strict_scale, 1, 1)  # 刻度在滑块列正下方
        # 整行左右对称居中
        strict_row = QHBoxLayout()
        strict_row.addStretch(1)
        strict_row.addLayout(strict_grid)
        strict_row.addStretch(1)
        root.addLayout(strict_row)
        self._update_strictness_label()

        # 底部少量弹性空白：内容紧凑，按钮行沉底，与识别程度卡片间隔留白自然
        root.addStretch(1)

        # ---- 底部：应用反馈（左） + 操作按钮（右） ----
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.apply_status = QLabel("")
        self.apply_status.setObjectName("statusLabel")
        btn_row.addWidget(self.apply_status)
        btn_row.addStretch()
        self.apply_btn = QPushButton("应用")
        self.apply_btn.setProperty("role", theme.ROLE_PRIMARY)
        self.close_btn = QPushButton("关闭")
        self.apply_btn.setMinimumHeight(36)
        self.close_btn.setMinimumHeight(36)
        self.apply_btn.clicked.connect(self._apply)
        self.close_btn.clicked.connect(self.close)
        btn_row.addWidget(self.apply_btn)
        btn_row.addWidget(self.close_btn)
        root.addLayout(btn_row)

    # ---- 事件 ----
    def _make_swatch(self, name: str) -> QPushButton:
        """构造单个颜色色块：底色为预设色，文字为颜色名，勾选时前缀 ✓。

        文字颜色按底色亮度取黑 / 白，保证任意预设色上都可读；宽度用 Expanding
        由布局均分，避免固定宽导致挤压重叠。
        """
        info = EVE_COLORS[name]
        r, g, b = info["rgb"]
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        text_color = "black" if luminance > 150 else "white"
        btn = QPushButton()
        btn.setCheckable(True)
        btn.setMinimumSize(96, 52)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(f"{info['label']}（{name}）")
        btn.setStyleSheet(
            f"QPushButton {{ background-color: rgb({r},{g},{b}); color: {text_color};"
            f" border: 2px solid {theme.BORDER}; border-radius: 8px;"
            f" font-size: 14px; font-weight: 600; }}"
            f"QPushButton:checked {{ border: 3px solid {theme.SUCCESS}; }}"
        )
        btn.clicked.connect(lambda _checked, n=name: self._on_color_toggled(n))
        return btn

    def _active_colors(self) -> list[str]:
        """当前勾选的警报颜色。"""
        return [name for name, btn in self._color_buttons.items() if btn.isChecked()]

    def _refresh_swatch_marks(self) -> None:
        """勾选状态以「✓ + 颜色名」显示，未勾选只显示颜色名。"""
        for name, btn in self._color_buttons.items():
            label = EVE_COLORS[name]["label"]
            btn.setText(f"✓ {label}" if btn.isChecked() else label)

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
        # 固定档位 0/50/100：拖动时吸附到最近档位
        snapped = round(value / 50) * 50
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
        colors = self._active_colors()
        if not colors:
            # 至少勾选一种颜色：全不勾会让检测掩码恒为空、警报永不触发
            self._flash_apply_status("请至少勾选一种颜色", theme.WARN)
            return
        det = dict(self.config.data["detection"])
        det["colors"] = colors
        det["strictness"] = self.strictness_slider.value()
        self.config.update("detection", det)
        # 底部左侧短暂显示绿色「已应用」反馈（1.5 秒后自动清除）
        self._flash_apply_status("已应用", theme.SUCCESS, 1500)

    def _flash_apply_status(self, text: str, color: str, delay_ms: int = 2200) -> None:
        """底部左侧短暂显示应用反馈（颜色区分成功 / 需要修正）。"""
        self.apply_status.setText(text)
        self.apply_status.setStyleSheet(f"color:{color};font-weight:600;")
        QTimer.singleShot(delay_ms, self._clear_apply_status)

    def _clear_apply_status(self) -> None:
        self.apply_status.clear()
        self.apply_status.setStyleSheet("")


class PauseSettingsDialog(QDialog):
    """暂停设置：暂停最短持续时长（暂停后至少持续多少秒，且目标不在视野时才自动解除暂停）。

    进出空间站等操作会造成数秒黑屏（检测不到目标），若时长为 0 会在黑屏瞬间误解除
    暂停并再次报警；调大时长可避免。设为 0 秒表示不延时（黑屏即恢复）。
    """

    def __init__(self, config: ConfigManager, parent=None):
        super().__init__(parent)
        self.config = config

        self.setWindowTitle("暂停设置 · 最短持续时长")
        self.setFixedSize(440, 220)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        desc = QLabel("暂停后至少持续多少秒，且目标不在视野时才自动恢复预警")
        desc.setObjectName("descriptionLabel")
        desc.setWordWrap(True)
        root.addWidget(desc)

        row = QHBoxLayout()
        row.setSpacing(10)
        lbl = QLabel("最短持续时长")
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


class _SendTestWorker(QThread):
    """异步发送 Server酱 测试通知，结果经信号回 UI 线程（不阻塞界面）。"""

    done = Signal(bool, str)

    def __init__(self, sendkey: str, parent=None):
        super().__init__(parent)
        self._sendkey = sendkey

    def run(self) -> None:
        ok, message = send_serverchan(
            self._sendkey,
            "EVE Local Alert 测试通知",
            "收到此条消息说明远程预警通道配置正常。",
        )
        self.done.emit(ok, message)


class RemoteAlertDialog(QDialog):
    """远程预警设置：Server酱 微信推送。

    默认关闭；开启后警报触发时向用户自己的微信推送一条提醒。
    首次使用（开启时尚未填写 SendKey）显示引导步骤与 Server酱 注册入口。
    """

    def __init__(self, config: ConfigManager, parent=None):
        super().__init__(parent)
        self.config = config
        self._test_worker: _SendTestWorker | None = None

        self.setWindowTitle("远程预警 · 微信推送（Server酱）")
        self.setFixedWidth(500)
        self._build_ui()
        self._sync_from_config()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        desc = QLabel(
            "开启后，警报触发时通过 Server酱 向你的微信推送一条提醒（需联网；"
            "发送失败只记日志，不影响本地警报音）"
        )
        desc.setObjectName("descriptionLabel")
        desc.setWordWrap(True)
        root.addWidget(desc)

        self.enable_check = QCheckBox("启用远程预警")
        self.enable_check.toggled.connect(self._update_state)
        root.addWidget(self.enable_check)

        # ---- 首次使用引导（开启且未填 SendKey 时显示） ----
        self.guide_label = QLabel(
            "首次使用：\n"
            "1. 登录 Server酱（微信扫码即可）\n"
            "2. 在「SendKey」页面复制你的 Key（SCT 开头）\n"
            "3. 粘贴到下方输入框，微信将收到一条关注绑定消息"
        )
        self.guide_label.setObjectName("descriptionLabel")
        self.guide_label.setWordWrap(True)
        root.addWidget(self.guide_label)

        register_row = QHBoxLayout()
        self.register_btn = QPushButton("打开 Server酱 注册页面")
        self.register_btn.setToolTip(SERVERCHAN_REGISTER_URL)
        self.register_btn.setCursor(Qt.PointingHandCursor)
        self.register_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(SERVERCHAN_REGISTER_URL))
        )
        register_row.addWidget(self.register_btn)
        register_row.addStretch()
        root.addLayout(register_row)

        # ---- SendKey ----
        key_row = QHBoxLayout()
        key_row.setSpacing(10)
        key_lbl = QLabel("SendKey")
        key_lbl.setObjectName("subtitleLabel")
        self.sendkey_edit = QLineEdit()
        self.sendkey_edit.setPlaceholderText("粘贴 SCT 开头的 SendKey")
        self.sendkey_edit.textChanged.connect(self._update_state)
        key_row.addWidget(key_lbl)
        key_row.addWidget(self.sendkey_edit, stretch=1)
        root.addLayout(key_row)

        # ---- 测试通知 ----
        test_row = QHBoxLayout()
        test_row.setSpacing(10)
        self.test_btn = QPushButton("发送测试通知")
        self.test_btn.setMinimumHeight(36)
        self.test_btn.clicked.connect(self._send_test)
        self.test_result = QLabel("")
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.test_result, stretch=1)
        root.addLayout(test_row)

        # ---- 冷却间隔（可选）：免费版每日 5 条额度，建议开启以免短时间重复推送 ----
        self.cooldown_check = QCheckBox("启用冷却间隔（免费用户请勾选）")
        self.cooldown_check.toggled.connect(self._update_state)
        root.addWidget(self.cooldown_check)
        cooldown_desc = QLabel(
            "勾选后，两次远程提醒之间至少间隔设定时间：目标短暂消失又出现时不再重复推送，"
            "把 Server酱 免费版每日 5 条的额度留给真正需要的警报"
        )
        cooldown_desc.setObjectName("descriptionLabel")
        cooldown_desc.setWordWrap(True)
        root.addWidget(cooldown_desc)
        cooldown_row = QHBoxLayout()
        cooldown_row.setSpacing(10)
        cooldown_lbl = QLabel("冷却时间")
        cooldown_lbl.setObjectName("subtitleLabel")
        self.cooldown_spin = QSpinBox()
        self.cooldown_spin.setRange(1, 120)
        self.cooldown_spin.setSuffix(" 分钟")
        cooldown_row.addWidget(cooldown_lbl)
        cooldown_row.addWidget(self.cooldown_spin)
        cooldown_row.addStretch()
        root.addLayout(cooldown_row)

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

    # ---- 状态 ----
    def _sendkey(self) -> str:
        return self.sendkey_edit.text().strip()

    def _sync_from_config(self) -> None:
        remote = self.config.data.get("remote_alert") or {}
        self.enable_check.setChecked(bool(remote.get("enabled")))
        self.sendkey_edit.setText(str(remote.get("sendkey") or ""))
        self.cooldown_check.setChecked(bool(remote.get("cooldown_enabled")))
        self.cooldown_spin.setValue(int(remote.get("cooldown_minutes", 5) or 5))
        self._update_state()

    def _update_state(self) -> None:
        """开关 / SendKey / 冷却变化时刷新引导显隐、测试与应用按钮可用性。"""
        enabled = self.enable_check.isChecked()
        has_key = bool(self._sendkey())
        # 首次使用引导：开启但还没填 SendKey
        self.guide_label.setVisible(enabled and not has_key)
        self.register_btn.setVisible(enabled and not has_key)
        self.test_btn.setEnabled(has_key)
        self.cooldown_spin.setEnabled(self.cooldown_check.isChecked())
        if not has_key:
            self.test_result.clear()
        # 已开启但没填 SendKey 时不允许应用，避免保存无效配置
        self.apply_btn.setEnabled(not enabled or has_key)

    def _send_test(self) -> None:
        sendkey = self._sendkey()
        if not sendkey or (
            self._test_worker is not None and self._test_worker.isRunning()
        ):
            return
        self.test_btn.setEnabled(False)
        self.test_result.setText("发送中...")
        self._test_worker = _SendTestWorker(sendkey, self)
        self._test_worker.done.connect(self._on_test_done)
        self._test_worker.start()

    def _on_test_done(self, ok: bool, message: str) -> None:
        self.test_btn.setEnabled(True)
        self.test_result.setText(
            "已发送，请查看微信" if ok else f"发送失败：{message}"
        )

    def _apply(self) -> None:
        self.config.update(
            "remote_alert",
            {
                "enabled": self.enable_check.isChecked(),
                "sendkey": self._sendkey(),
                "cooldown_enabled": self.cooldown_check.isChecked(),
                "cooldown_minutes": self.cooldown_spin.value(),
            },
        )
        self.close()


class PreviewDialog(QDialog):
    """独立预警预览窗口：显示框选监控区域画面与识别信息。

    与监控线程独立运行：实时捕获 ROI 区域，叠加识别框与命中状态，
    供用户单独查看识别效果（不占用主面板预览）。
    """

    def __init__(
        self,
        config: ConfigManager,
        root_dir: Path,
        parent=None,
        target_handle: int | None = None,
    ):
        super().__init__(parent)
        self.config = config
        self._root = root_dir
        self._capture = Capture()
        self._capture_closed = False  # closeEvent 后置位，下次打开时重建捕获实例
        self._target_handle = target_handle
        self._locator = self._make_locator()
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

    def _make_locator(self) -> WindowLocator:
        """按当前配置 + 目标句柄构造定位器（与主面板保持同一套定位语义）。"""
        return WindowLocator(
            title_keyword=self.config.data["window"].get("title_keyword"),
            process_name=self.config.data["window"].get("process_name"),
            prefer_handle=self._target_handle,
        )

    def set_target_handle(self, handle: int | None) -> None:
        """目标程序变化时更新跟踪句柄，并强制下一帧重新定位。"""
        self._target_handle = handle
        self._win = None

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
        """定时刷新：定位窗口 -> 读取尺寸 -> 捕获 ROI -> 检测 -> 叠加显示。"""
        self._tick_count += 1
        # 检测参数变化（颜色 / 识别程度）时重建检测器，保持与配置一致
        key = repr(sorted(self.config.data["detection"].items()))
        if key != self._det_cfg_key:
            self._detector = Detector(**self.config.data["detection"])
            self._det_cfg_key = key

        win = self._resolve_window()
        if win is None:
            return
        # 句柄可能在两次调用之间失效：win32 抛的是 pywintypes.error（非 RuntimeError），
        # 必须在此收敛，否则异常逃出 Qt 槽会被记成「未捕获异常（闪退）」。
        try:
            minimized = is_minimized(win.handle)
            cw, ch = client_size(win.handle)
        except Exception:
            logger.warning("预览：读取目标窗口状态失败，重新定位", exc_info=True)
            self._win = None
            self.status_label.setText("目标程序窗口不可用，正在重新定位")
            return

        if minimized:
            self.preview.clear()
            self.preview.set_placeholder("目标窗口已最小化，请恢复窗口")
            return

        roi = ROI(**self.config.data["roi"])
        region = roi.to_capture_region(0, 0, cw, ch)
        frame = self._capture.grab_window(win.handle, region)
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

    def _resolve_window(self) -> WindowInfo | None:
        """返回当前可用的目标窗口；窗口关闭 / 句柄失效时丢弃缓存并返回 None。

        「预览」是可选功能，这里把定位与句柄失效的全部异常收敛在窗口解析阶段，
        不牵连后面的捕获 / 检测代码（那些异常应正常暴露）。
        """
        try:
            # 目标程序变化（重新选择程序）时重建定位器，保持与配置一致
            win_key = repr(sorted(self.config.data["window"].items()))
            if win_key != self._win_cfg_key:
                self._win_cfg_key = win_key
                self._locator = self._make_locator()
                self._win = None  # 强制重新定位到新程序
            if self._win is None or self._tick_count % REFRESH_GEOMETRY_EVERY == 0:
                self._win = self._locator.find()
            return self._win
        except RuntimeError:
            self._win = None  # 窗口可能已关闭，下一帧重新定位
            self.status_label.setText("无法定位目标程序窗口")
            return None
        except Exception:
            if self._win is not None:
                logger.warning("预览：目标窗口不可用，重新定位", exc_info=True)
                self._win = None
            self.status_label.setText("目标程序窗口不可用，正在重新定位")
            return None

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
        # 目标窗口句柄（用户点选的那个窗口）：只跟踪它，关闭即判定目标已关闭
        self._target_handle: int | None = None
        # 「预览」可用状态：需已选择目标
        self._has_target = False

        # 预览自动适应控件尺寸；监控程序客户区宽高比用于面板等比缩放
        self._prog_aspect = 0.0
        self._resizing = False          # 防止 resizeEvent 递归
        self._controls_cache = 170      # 控件总高（布局生效后测量校准）

        self.setWindowTitle("EVE Local Alert")
        self.setWindowIcon(self._app_icon())
        # 注意：面板不做置顶，不改变其他窗口的 Z 序或前台状态，避免影响其他应用操作。
        # 最小宽度须容纳次要功能行（5 个 96px 按钮 + GitHub 图标 + 间距），否则布局挤压重叠
        self.setMinimumSize(600, 460)
        self.resize(600, 520)
        self._build_ui()
        self._update_preview_button()
        self._update_remote_button()

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
            "点击后即使命中也不报警；暂停至少持续设定时长，且目标不在视野时才自动恢复（时长可在「暂停设置」调整）"
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
        self.test_btn = QPushButton("测试声音")
        self.pause_settings_btn = QPushButton("暂停设置")
        self.pause_settings_btn.setToolTip(
            "设置暂停预警的最短持续时长（时长满后目标不在视野即自动解除暂停）"
        )
        self.remote_btn = QPushButton("远程预警")
        self.remote_btn.setToolTip(
            "开启后，警报触发时通过 Server酱 向你的微信推送提醒（默认关闭）"
        )
        # 次要功能统一固定宽度，左侧紧凑排布，避免按钮撑满整行
        for btn in (
            self.color_btn,
            self.preview_btn,
            self.test_btn,
            self.pause_settings_btn,
            self.remote_btn,
        ):
            btn.setMinimumHeight(38)
            btn.setFixedWidth(96)
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
        self.remote_btn.clicked.connect(self._open_remote_alert)

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
            prefer_handle=self._target_handle,
        )

    def _bind_target(self, win: WindowInfo) -> None:
        """记住目标窗口句柄：之后只跟踪这个窗口，避免同进程的其它窗口被当成目标。"""
        self._target_handle = win.handle
        if self._preview_dialog is not None:
            self._preview_dialog.set_target_handle(win.handle)

    def _select_target(self, cancelled_message: str = "") -> None:
        """选择目标程序；选择完成后自动进入框选。

        :param cancelled_message: 用户取消选择时的提示文案（默认通用文案）。
        """
        prefer = None
        try:
            prefer = self._make_locator().find().handle
        except RuntimeError:
            pass
        dialog = TargetPickerDialog(self, prefer_handle=prefer)
        if dialog.exec() != QDialog.Accepted or dialog.picked is None:
            self.status_label.setText(
                cancelled_message or "未选择程序，可稍后点击「选择程序」"
            )
            return
        win = self._on_target_selected(dialog.picked)
        if win is not None:
            self._start_selection()  # 选择完成后进入预览框选

    def _on_target_selected(self, win: WindowInfo) -> WindowInfo | None:
        """保存目标选择并返回重新定位的最新窗口；失败返回 None。"""
        # 保存选择：标题关键字 + 进程名，供后续定位与监控使用。进程名取不到时
        # （高权限进程 psutil 会失败）必须显式写成空串：否则旧进程名残留，而
        # WindowLocator 优先按进程名匹配，会锁回上一个目标窗口。
        self.config.update(
            "window",
            {"title_keyword": win.title, "process_name": win.process_name or ""},
        )
        # 记录用户点选的窗口句柄：后续只跟踪这个窗口（同名 / 同进程的其它窗口不会被误认）
        self._target_handle = win.handle
        # 重新选择程序后，预览窗口恢复默认大小与位置
        if self._preview_dialog is not None:
            self._preview_dialog.reset_geometry()

        try:
            win = self._make_locator().find()
        except RuntimeError:
            self.status_label.setText("目标程序窗口已关闭，请重新选择")
            return None
        self._bind_target(win)
        try:
            cw, ch = client_size(win.handle)
        except Exception:
            # 极窄的竞态：刚定位到窗口就被关闭。win32 抛的是 pywintypes.error
            logger.warning("目标窗口在定位后立即失效", exc_info=True)
            self.status_label.setText("目标程序窗口已关闭，请重新选择")
            return None
        self._set_program_aspect(cw, ch)
        self._has_target = True
        self._update_preview_button()
        self.status_label.setText(
            f"已选择目标：{win.title}，请在预览画面中框选监控区域，框选完成后点「开始预警」"
        )
        return win

    def _ensure_target(self) -> WindowInfo | None:
        """确保有匹配的目标窗口；没有则引导用户选择，返回 None 表示放弃。"""
        try:
            win = self._make_locator().find()
        except RuntimeError:
            dialog = TargetPickerDialog(self)
            if dialog.exec() != QDialog.Accepted or dialog.picked is None:
                return None
            return self._on_target_selected(dialog.picked)
        self._bind_target(win)
        return win

    # ---- 预览内框选区域 ----
    def _start_selection(self) -> None:
        """进入框选模式：预览显示目标窗口全画面，拖拽框选监控区域。"""
        if self._worker_running():
            self._stop_monitor()  # 避免监控预览覆盖框选画面
        if self._worker_running():
            # 线程未在超时内退出（采集卡住）：本轮不进入框选，避免两路画面互相覆盖
            self.status_label.setText("正在停止上一轮监控，请稍候再试")
            return
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
        self.status_label.setText("在预览画面中拖动鼠标框选监控区域，完成后点「开始预警」")
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
        try:
            minimized = is_minimized(self._sel_target.handle)
            cw, ch = client_size(self._sel_target.handle)
        except Exception:
            # 句柄在定位与读取之间失效（win32 抛 pywintypes.error）
            logger.warning("目标窗口在框选刷新期间失效", exc_info=True)
            self._end_selection("目标程序窗口已关闭")
            return
        # 窗口最小化时无法捕获内容：显示占位提示，恢复后自动继续框选预览
        if minimized:
            self.preview.clear()
            self.preview.set_placeholder("目标窗口已最小化，请恢复窗口")
            return
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
            "，点「开始预警」开始监控"
        )

    def _end_selection(self, message: str = "") -> None:
        """结束框选会话，释放捕获资源并清除残留选区。

        ``message`` 非空表示目标窗口已失效（异常结束）：除提示外还要清掉过期画面，
        并把面板退回「未选择目标」状态——否则下一步「开始预警」会拿着失效句柄再试。
        """
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
            # 异常结束（如目标窗口关闭）：清掉最后一帧画面，避免停在一张过期 / 黑屏画面上
            self.preview.clear()
            self.preview.set_placeholder("实时预览：选择目标程序后自动进入框选模式")
            self._has_target = False
            self._update_preview_button()
            self.status_label.setText(message)

    # ---- 监控 ----
    def _worker_running(self) -> bool:
        """监控线程是否仍在运行（含「已请求停止但尚未退出」的收尾阶段）。"""
        return self._worker is not None and self._worker.isRunning()

    def _toggle_monitor(self) -> None:
        if self._worker_running():
            self._stop_monitor()
            self._start_selection()  # 退出监控后自动进入框选模式
        else:
            self._start_monitor()

    def _start_monitor(self) -> None:
        """开始监控。

        未选择目标程序时，本次点击**等价于「选择程序」**：弹出选择框、选完进入框选，
        用户框好监控区域后再点一次「开始预警」才真正开始——避免「点开始预警 →
        弹窗选程序 → 选完立刻开跑」导致用户来不及框选区域。
        """
        # 上一轮线程可能仍在退出（采集卡住时 wait 会超时）：此时不新建，避免两个
        # 工作线程同时抓屏、同时写预览与状态栏
        if self._worker_running():
            self.status_label.setText("正在停止上一轮监控，请稍候再试")
            return
        if not self._has_target:
            self._select_target(
                "未选择目标程序，预警未开始：请选择窗口并在预览中框选监控区域，再点「开始预警」"
            )
            return
        self._end_selection()  # 先结束框选会话，避免预览冲突
        try:
            win = self._make_locator().find()
        except RuntimeError:
            # 目标窗口已关闭（句柄失效）：同样退回「选择程序」流程，由用户重新选择
            self._select_target(
                "目标程序窗口已关闭，预警未开始：请重新选择窗口并框选监控区域，再点「开始预警」"
            )
            return
        self._bind_target(win)
        try:
            cw, ch = client_size(win.handle)
        except Exception:
            logger.warning("目标窗口在启动监控前失效", exc_info=True)
            self.status_label.setText("目标程序窗口已关闭，请重新选择")
            return
        self._set_program_aspect(cw, ch)
        # 通过窗口内容捕获（PrintWindow）监控所选窗口，不改变窗口 Z 序 / 前台状态。
        self._worker = MonitorWorker(
            self.config, self._root, self, target_handle=self._target_handle
        )
        self._worker.preview.connect(self._on_preview)
        self._worker.status.connect(self.status_label.setText)
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
        """停止监控线程并复位按钮；线程未在超时内退出时保留引用等待其自行结束。"""
        worker = self._worker
        if worker is not None:
            worker.stop()
            if worker.wait(2000):
                self._worker = None
            else:
                # 采集被卡住（如 PrintWindow 阻塞）时线程仍在收尾：保留引用，由
                # _worker_running() 阻止新一轮监控 / 框选抢跑，避免双线程同抓屏。
                logger.warning("监控线程未在 2 秒内退出，保留引用等待其自行结束")
        self.start_btn.setText("开始预警")
        self.pause_btn.setEnabled(False)
        self.status_label.setText("已停止预警")

    def _on_preview(self, frame) -> None:
        if frame is None:
            self.preview.clear()
            self.preview.set_placeholder("目标窗口已最小化，请恢复窗口")
            return
        self.preview.set_image(bgr_to_qimage(frame))

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
        # 保留 _target_handle：下次「开始预警」会因句柄失效直接引导重选，
        # 而不是按进程名 / 标题悄悄锁到别的窗口

    def _on_window_minimized(self) -> None:
        """目标窗口最小化：播放提示音并置顶面板提醒玩家（不抢键盘焦点，不提示窗口关闭）。"""
        try:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
        self.raise_()

    # ---- 播报暂停 ----
    def _on_pause_toggled(self, checked: bool) -> None:
        """用户点击「暂停预警」：即使命中也不报警，达最短时长且目标不在视野后自动恢复。"""
        if not self._worker_running():
            # 未监控时忽略（按钮已禁用，双保险）
            self.pause_btn.blockSignals(True)
            self.pause_btn.setChecked(False)
            self.pause_btn.setText("暂停预警")
            self.pause_btn.blockSignals(False)
            return
        self._worker.set_paused(checked)
        self.pause_btn.setText("预警已暂停" if checked else "暂停预警")
        # 顶部状态信息立即切换为黄色「已暂停」（暂停）或恢复「敌袭/安全」常态
        self._worker.refresh_status()

    def _on_alert_paused(self, paused: bool) -> None:
        """工作线程同步暂停状态（自动恢复播报时取消按钮勾选）。"""
        if not self._worker_running():
            return
        self.pause_btn.blockSignals(True)
        self.pause_btn.setChecked(paused)
        self.pause_btn.setText("预警已暂停" if paused else "暂停预警")
        self.pause_btn.blockSignals(False)
        if not paused:
            self.status_label.setText("目标不在视野，预警已自动恢复")

    # ---- 颜色选择 / 预览 ----
    def _open_color_picker(self) -> None:
        dialog = ColorPickerDialog(self.config, self)
        dialog.exec()

    def _open_pause_settings(self) -> None:
        """打开暂停设置：暂停最短持续时长（秒）。"""
        dialog = PauseSettingsDialog(self.config, self)
        dialog.exec()

    def _open_remote_alert(self) -> None:
        """打开远程预警设置：Server酱 微信推送（默认关闭，首次使用有引导）。"""
        dialog = RemoteAlertDialog(self.config, self)
        dialog.exec()
        self._update_remote_button()  # 应用「启用 / 关闭」后同步按钮样式

    def _update_remote_button(self) -> None:
        """按远程预警开关同步「远程预警」按钮样式。

        开启时套用与「开始预警」相同的主行动样式（主题蓝底白字加粗）作为状态提示，
        关闭时恢复普通次要按钮样式。

        动态属性变更后全局 QSS 不会自动重新求值：只 ``setProperty`` 的话按钮外观不变
        （实测仍为 0 个主色像素）。刷新若在 Python 侧调用 ``style().unpolish/polish``，
        实测会在 Qt 测试会话里触发非确定性崩溃，故改用「按状态切换控件级样式表」——
        Qt 在 ``setStyleSheet`` 内部完成重新 polish，真实平台连续切换已验证稳定。
        """
        enabled = bool((self.config.data.get("remote_alert") or {}).get("enabled"))
        # 顺序要紧：先改动态属性，再重设控件样式表——setStyleSheet 内部会重新 polish，
        # 此刻读到的是新属性值；反过来会让关闭时仍按旧的 role=primary 重新求值。
        self.remote_btn.setProperty("role", theme.ROLE_PRIMARY if enabled else "")
        self.remote_btn.setStyleSheet(theme.primary_button_qss() if enabled else "")
        self.remote_btn.setToolTip(
            "远程预警已开启：警报触发时通过 Server酱 向你的微信推送提醒"
            if enabled
            else "开启后，警报触发时通过 Server酱 向你的微信推送提醒（默认关闭）"
        )

    def _update_preview_button(self) -> None:
        """「预览」仅在已选择目标时可点击。"""
        self.preview_btn.setEnabled(self._has_target)

    def _open_preview(self) -> None:
        """打开独立预览窗口：显示框选监控区域画面与识别信息。"""
        if self._preview_dialog is None:
            self._preview_dialog = PreviewDialog(
                self.config, self._root, self, target_handle=self._target_handle
            )
        else:
            self._preview_dialog.set_target_handle(self._target_handle)
        self._preview_dialog.show()
        self._preview_dialog.raise_()
        self._preview_dialog.activateWindow()

    def _open_github(self) -> None:
        """打开项目 GitHub 仓库。"""
        QDesktopServices.openUrl(QUrl("https://github.com/ruanpy-1104/eve-local-alert"))

    def _test_alert(self) -> None:
        """测试声音：播放一个完整周期后停止。"""
        if self._worker_running() and self._worker.is_alerting():
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
        if self._worker_running():
            self._worker.stop()
            self._worker.wait(2000)
        super().closeEvent(event)

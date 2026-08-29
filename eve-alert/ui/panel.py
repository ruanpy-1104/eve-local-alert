"""控制面板（UI 层）。

基于 PySide6 实现：
- 启动引导：选择目标程序 -> 在预览画面中框选监控区域 -> 校准/监控；
- 区域选择在控制面板的预览画面内完成（不再在被监控程序界面遮罩框选）；
- 预览画面自动适应控件尺寸，无需手动缩放；
- 启动 / 停止监控、实时预览、颜色选择（色块 + 识别程度）。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QRect, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.alerter import Alerter
from core.capture import Capture, client_size
from core.colors import COLOR_ORDER, DEFAULT_ALERT_COLORS, EVE_COLORS
from core.config import ConfigManager
from core.detector import Detector
from core.region_selector import ROI
from core.window_locator import WindowInfo, WindowLocator, is_minimized
from ui.preview import PreviewWidget
from ui.window_picker import TargetPickerDialog

REFRESH_GEOMETRY_EVERY = 30  # 每 N 帧重新定位窗口，跟随移动 / 缩放
SELECTION_INTERVAL_MS = 150  # 框选模式下全窗口预览刷新间隔


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

    def __init__(self, config: ConfigManager, root_dir: Path, parent=None):
        super().__init__(parent)
        self._config = config
        self._root = root_dir
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            self._loop()
            # 仅自然结束时覆盖状态；手动停止由面板自行设置后续状态，避免竞态覆盖
            if not self._stop.is_set():
                self.status.emit("已停止")
        except Exception as exc:  # noqa: BLE001
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
        alerting = False
        hit_count = 0
        frame_count = 0
        last_status = time.perf_counter()
        status_frames = 0
        minimized_reported = False
        try:
            win = locator.find()  # 未命中抛 RuntimeError，由 run() 上报
            while not self._stop.is_set():
                started = time.perf_counter()

                # 检测参数变化时重建 Detector（保持时序确认状态）
                key = repr(sorted(cfg["detection"].items()))
                if key != det_cfg_key:
                    detector = Detector(**cfg["detection"])
                    det_cfg_key = key

                if frame_count % REFRESH_GEOMETRY_EVERY == 0:
                    win = locator.find()  # 窗口移动 / 缩放后自动跟随

                # 目标窗口最小化：暂停捕获与检测，等待恢复（不中断监控）
                if is_minimized(win.handle):
                    if alerting:
                        alerting = False
                        alerter.stop()
                    if not minimized_reported:
                        minimized_reported = True
                        self.status.emit("目标窗口已最小化，无法监控，请恢复窗口")
                        self.preview.emit(None)  # 面板显示"已最小化"占位提示
                    time.sleep(interval)
                    continue
                minimized_reported = False

                cw, ch = client_size(win.handle)
                roi = ROI(**cfg["roi"])
                region = roi.to_capture_region(0, 0, cw, ch)
                frame = capture.grab_window(win.handle, region)
                if frame is None or frame.size == 0:
                    # 捕获失败（异常状态）：跳过本帧，不中断监控
                    time.sleep(interval)
                    continue

                # 连续警报：识别到即持续播放，未识别立即停止（无冷却）
                if detector.detect(frame):
                    if not alerting:
                        alerting = True
                        alerter.start()
                        hit_count += 1
                        self.detected.emit()
                elif alerting:
                    alerting = False
                    alerter.stop()

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
        self._capture = Capture()
        self._locator = WindowLocator(
            title_keyword=config.data["window"].get("title_keyword"),
            process_name=config.data["window"].get("process_name"),
        )
        # 以 downscale=1 预览，保证掩码 / 外接框与画面一一对应
        det_kwargs = dict(config.data["detection"])
        det_kwargs["downscale"] = 1
        self._detector = Detector(**det_kwargs)
        self._win = None
        self._frame: np.ndarray | None = None
        self._tick_count = 0

        self.setWindowTitle("颜色选择")
        self.setMinimumSize(560, 620)
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(100)  # 10 FPS 预览
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ---- UI ----
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self.preview = PreviewWidget()
        self.preview.set_placeholder("等待画面（确保目标程序已打开）")
        self.preview.setMinimumSize(480, 220)
        root.addWidget(self.preview, stretch=1)

        self.status_label = QLabel("勾选需要警报的颜色，识别程度越严格判定越苛刻、误报越少")
        root.addWidget(self.status_label)

        # ---- EVE 总览警报颜色选择（仅色块，勾选显示对勾） ----
        root.addWidget(QLabel("警报颜色（EVE 总览）：勾选需要警报的颜色"))
        palette_grid = QGridLayout()
        palette_grid.setSpacing(6)
        self._color_buttons: dict[str, QPushButton] = {}
        for i, name in enumerate(COLOR_ORDER):
            info = EVE_COLORS[name]
            r, g, b = info["rgb"]
            luminance = 0.299 * r + 0.587 * g + 0.114 * b
            text_color = "black" if luminance > 150 else "white"
            btn = QPushButton("")
            btn.setCheckable(True)
            btn.setFixedSize(58, 38)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(f"{info['label']}（{name}）")
            btn.setStyleSheet(
                f"QPushButton {{ background-color: rgb({r},{g},{b}); color: {text_color};"
                f" border: 2px solid #666; border-radius: 4px; font-weight: bold; }}"
                f"QPushButton:checked {{ border: 3px solid #4CAF50; }}"
            )
            btn.clicked.connect(lambda _checked, n=name: self._on_color_toggled(n))
            palette_grid.addWidget(btn, i // 6, i % 6)
            self._color_buttons[name] = btn
        root.addLayout(palette_grid)
        self._sync_colors_from_config()
        self._refresh_swatch_marks()

        # ---- 识别程度（左宽松 ~ 右严格） ----
        strict_row = QHBoxLayout()
        strict_row.addWidget(QLabel("宽松"))
        self.strictness_slider = QSlider(Qt.Horizontal)
        self.strictness_slider.setRange(0, 100)
        self.strictness_slider.setValue(int(self.config.data["detection"].get("strictness", 50)))
        self.strictness_slider.setTickPosition(QSlider.TicksBelow)
        self.strictness_slider.setTickInterval(25)
        self.strictness_slider.valueChanged.connect(self._on_strictness_changed)
        strict_row.addWidget(self.strictness_slider, stretch=1)
        strict_row.addWidget(QLabel("严格"))
        self.strictness_value = QLabel()
        self.strictness_value.setMinimumWidth(64)
        strict_row.addWidget(self.strictness_value)
        root.addLayout(strict_row)
        self._update_strictness_label()

        btn_row = QHBoxLayout()
        self.apply_btn = QPushButton("应用并保存")
        self.close_btn = QPushButton("关闭")
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
        if value <= 33:
            level = "宽松"
        elif value <= 66:
            level = "适中"
        else:
            level = "严格"
        self.strictness_value.setText(f"{value} · {level}")

    def _rebuild_detector(self) -> None:
        """按当前颜色与识别程度重建检测器并刷新预览。"""
        det = dict(self.config.data["detection"])
        det["colors"] = self._active_colors()
        det["strictness"] = self.strictness_slider.value()
        det["downscale"] = 1  # 预览与画面一一对应
        self._detector = Detector(**det)

    def _on_strictness_changed(self, _value: int) -> None:
        self._update_strictness_label()
        self._rebuild_detector()

    def _on_color_toggled(self, _name: str) -> None:
        """勾选颜色变化：刷新对勾并重建检测器。"""
        self._refresh_swatch_marks()
        self._rebuild_detector()

    def _apply(self) -> None:
        det = dict(self.config.data["detection"])
        det["colors"] = self._active_colors()
        det["strictness"] = self.strictness_slider.value()
        self.config.update("detection", det)
        active = "、".join(EVE_COLORS[c]["label"] for c in det["colors"])
        level = "宽松" if det["strictness"] <= 33 else ("适中" if det["strictness"] <= 66 else "严格")
        self.status_label.setText(f"已保存：警报颜色 {active}，识别程度 {level}（{det['strictness']}）")

    def _tick(self) -> None:
        try:
            if self._win is None or self._tick_count % REFRESH_GEOMETRY_EVERY == 0:
                self._win = self._locator.find()
            self._tick_count += 1
            cw, ch = client_size(self._win.handle)
            roi = ROI(**self.config.data["roi"])
            region = roi.to_capture_region(0, 0, cw, ch)
            frame = self._capture.grab_window(self._win.handle, region)
            self._frame = frame
            self._show_preview(frame)
        except RuntimeError as exc:
            self._win = None  # 窗口可能已关闭，下一帧重新定位
            self.status_label.setText(f"无法定位目标程序窗口：{exc}")

    def _show_preview(self, frame: np.ndarray) -> None:
        mask = self._detector.red_mask(frame)
        vis = frame.copy()
        if mask.shape[:2] == frame.shape[:2]:
            green = np.zeros_like(vis)
            green[:] = (60, 200, 60)
            hit = mask > 0
            vis[hit] = (vis[hit].astype(np.int16) + green[hit].astype(np.int16)) // 2
        for x, y, w, h in self._detector.red_boxes(frame):
            cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 0), 2)
        self.preview.set_image(bgr_to_qimage(vis))

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self._capture.close()
        super().closeEvent(event)


class ControlPanel(QWidget):
    """主控制面板：目标选择、预览内框选区域、监控启停与颜色选择。"""

    def __init__(self, config_path: Path):
        super().__init__()
        self.config = ConfigManager(config_path)
        self._root = config_path.parent
        self._worker: MonitorWorker | None = None

        # 框选会话状态（在预览画面内完成区域选择）
        self._sel_target: WindowInfo | None = None
        self._sel_capture: Capture | None = None
        self._sel_timer: QTimer | None = None
        self._sel_frame: np.ndarray | None = None
        self._sel_done = False
        self._sel_roi: ROI | None = None

        # 预览自动适应控件尺寸；监控程序客户区宽高比用于面板等比缩放
        self._prog_aspect = 0.0
        self._resizing = False          # 防止 resizeEvent 递归
        self._controls_cache = 170      # 控件总高（布局生效后测量校准）

        self.setWindowTitle("EVE Alert 控制面板")
        # 注意：面板不做置顶，不改变其他窗口的 Z 序或前台状态，避免影响其他应用操作。
        self.setMinimumSize(420, 460)
        self.resize(520, 520)
        self._build_ui()

    # ---- UI ----
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        # 布局不约束窗口尺寸，由 resizeEvent 按监控程序比例等比控制
        root.setSizeConstraint(QVBoxLayout.SetNoConstraint)

        self.status_label = QLabel("请选择要监控的目标程序")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.preview = PreviewWidget()
        self.preview.set_placeholder("实时预览：选择目标程序后自动进入框选模式")
        self.preview.setMinimumSize(420, 260)
        self.preview.selection_finished.connect(self._on_selection_finished)
        root.addWidget(self.preview, stretch=1)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("开始监控")
        self.target_btn = QPushButton("选择目标程序")
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.target_btn)
        root.addLayout(btn_row)

        opt_row = QHBoxLayout()
        self.color_btn = QPushButton("颜色选择")
        self.test_btn = QPushButton("测试警报音")
        opt_row.addWidget(self.color_btn)
        opt_row.addWidget(self.test_btn)
        opt_row.addStretch()
        root.addLayout(opt_row)

        self.start_btn.clicked.connect(self._toggle_monitor)
        self.target_btn.clicked.connect(self._select_target)
        self.color_btn.clicked.connect(self._open_color_picker)
        self.test_btn.clicked.connect(self._test_alert)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self._sel_timer is not None:
            self._end_selection("已取消框选")
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        """面板拉伸固定比例：高度 = 控件 + 预览(预览宽度 / 监控程序宽高比)。
        同步计算（无定时器），横 / 竖 / 斜向拉伸都保持与监控程序等比，不闪烁。"""
        super().resizeEvent(event)
        if self._resizing or self._prog_aspect <= 0:
            return
        self._resizing = True
        try:
            target = self._proportional_height()
            if abs(self.height() - target) > 2:
                self.resize(self.width(), target)
        finally:
            self._resizing = False

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
            self.status_label.setText("未选择目标程序，可稍后点击「选择目标程序」")
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

        try:
            win = self._make_locator().find()
        except RuntimeError:
            self.status_label.setText("目标程序窗口已关闭，请重新选择")
            return None
        cw, ch = client_size(win.handle)
        self._set_program_aspect(cw, ch)
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
        self.status_label.setText("在预览画面中拖动鼠标框选监控区域，Esc 取消")
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
        rect = self._sel_target.rect
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
        if roi.width < 0.01 or roi.height < 0.01:
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
        self._worker.start()
        self.start_btn.setText("停止监控")
        self.status_label.setText("正在定位窗口...")

    def _stop_monitor(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker.wait(2000)
            self._worker = None
        self.start_btn.setText("开始监控")
        self.status_label.setText("已停止监控")

    def _on_preview(self, frame) -> None:
        if frame is None:
            self.preview.clear()
            self.preview.set_placeholder("目标窗口已最小化，请恢复窗口")
            return
        self.preview.set_image(bgr_to_qimage(frame))

    def _on_detected(self) -> None:
        self.status_label.setStyleSheet("color:#e74c3c;font-weight:bold;")
        self.status_label.setText("检测到目标颜色，正在警报！")
        QTimer.singleShot(1200, self._restore_status_style)

    def _on_error(self, message: str) -> None:
        self.status_label.setStyleSheet("color:#e67e22;font-weight:bold;")
        self.status_label.setText(f"错误：{message}")
        self.start_btn.setText("开始监控")

    def _restore_status_style(self) -> None:
        self.status_label.setStyleSheet("")

    # ---- 颜色选择 ----
    def _open_color_picker(self) -> None:
        dialog = ColorPickerDialog(self.config, self)
        dialog.exec()

    def _test_alert(self) -> None:
        """测试警报音：循环播放 1.5 秒后停止。"""
        sound_file = self.config.data["alert"].get("sound_file")
        path = str((self._root / sound_file).resolve()) if sound_file else None
        alerter = Alerter(path)
        alerter.start()
        self._test_alerter = alerter
        self.status_label.setText("正在播放警报音（1.5 秒）...")
        QTimer.singleShot(
            1500,
            lambda: (alerter.stop(), self.status_label.setText("警报音测试完成")),
        )

    def closeEvent(self, event) -> None:
        self._end_selection()
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(2000)
        super().closeEvent(event)

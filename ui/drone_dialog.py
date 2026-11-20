"""无人机监控设置窗口。"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QRect, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from core.capture import Capture, client_size
from core.config import ConfigManager
from core.region_selector import ROI
from core.sounds import SoundPlayer
from core.window_locator import WindowLocator, is_minimized
from ui.preview import PreviewWidget


def _bgr_to_qimage(frame: np.ndarray) -> QImage:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, _ = rgb.shape
    return QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()


class DroneDialog(QDialog):
    """整窗预览 + 无人机独立 ROI 框选与可选监控设置。"""

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
        self._target_handle = target_handle
        self._capture = Capture()
        self._frame: np.ndarray | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._tick)

        drone = self.config.data.get("drone") or {}
        self.sound_player = SoundPlayer(
            root_dir,
            drone.get("idle_sound", "assets/drone_idle.wav"),
            drone.get("alert_sound", "assets/drone_alert.wav"),
        )
        self._roi = ROI(**drone.get("roi", {}))
        self.setWindowTitle("无人机监控设置")
        self.setMinimumSize(680, 500)
        self.resize(760, 560)
        self._build_ui()
        self._update_region_label()

    def _make_locator(self) -> WindowLocator:
        cfg = self.config.data
        return WindowLocator(
            title_keyword=cfg["window"].get("title_keyword"),
            process_name=cfg["window"].get("process_name"),
            prefer_handle=self._target_handle,
        )

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        help_label = QLabel(
            "先在整窗画面中拖动框选无人机列表区域；绿色文字与红色状态只在该区域内识别。"
        )
        help_label.setObjectName("descriptionLabel")
        help_label.setWordWrap(True)
        root.addWidget(help_label)

        self.preview = PreviewWidget()
        self.preview.set_placeholder("正在等待目标窗口画面")
        self.preview.setMinimumHeight(320)
        self.preview.enable_selection(True)
        self.preview.selection_finished.connect(self._on_selection_finished)
        root.addWidget(self.preview, stretch=1)

        options = QHBoxLayout()
        options.setSpacing(12)
        self.enable_check = QCheckBox("启用无人机监控")
        self.enable_check.setChecked(
            bool((self.config.data.get("drone") or {}).get("enabled", False))
        )
        options.addWidget(self.enable_check)

        delay_label = QLabel("空闲提示延时")
        options.addWidget(delay_label)
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(1, 120)
        self.delay_spin.setSuffix(" 秒")
        try:
            idle_delay = int(
                (self.config.data.get("drone") or {}).get("idle_delay", 30)
            )
        except (TypeError, ValueError, OverflowError):
            idle_delay = 30
        self.delay_spin.setValue(max(1, min(120, idle_delay)))
        options.addWidget(self.delay_spin)
        options.addStretch(1)

        self.idle_test_btn = QPushButton("试听提示音")
        self.alert_test_btn = QPushButton("试听报警音")
        self.idle_test_btn.clicked.connect(self.sound_player.play_idle)
        self.alert_test_btn.clicked.connect(self.sound_player.play_alert_once)
        options.addWidget(self.idle_test_btn)
        options.addWidget(self.alert_test_btn)
        root.addLayout(options)

        region_row = QHBoxLayout()
        self.region_label = QLabel()
        self.region_label.setObjectName("statusLabel")
        region_row.addWidget(self.region_label, stretch=1)
        self.apply_btn = QPushButton("应用")
        self.apply_btn.clicked.connect(self._apply)
        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.close)
        region_row.addWidget(self.apply_btn)
        region_row.addWidget(self.close_btn)
        root.addLayout(region_row)

    def _update_region_label(self) -> None:
        roi = self._roi
        if roi.width <= 0 or roi.height <= 0:
            self.region_label.setText("无人机监控区域：未框选")
            return
        self.region_label.setText(
            "无人机监控区域："
            f"({roi.x:.3f}, {roi.y:.3f}, {roi.width:.3f}, {roi.height:.3f})"
        )

    def _tick(self) -> None:
        if self._capture is None:
            return
        try:
            win = self._make_locator().find()
        except RuntimeError:
            self.preview.clear()
            self.preview.set_placeholder("目标程序窗口已关闭")
            return
        except Exception:
            self.preview.clear()
            self.preview.set_placeholder("目标程序窗口不可用")
            return
        try:
            minimized = is_minimized(win.handle)
            cw, ch = client_size(win.handle)
        except Exception:
            self.preview.clear()
            self.preview.set_placeholder("目标程序窗口不可用")
            return
        if minimized:
            self.preview.clear()
            self.preview.set_placeholder("目标窗口已最小化，请恢复窗口")
            return
        self._target_handle = win.handle
        frame = self._capture.grab_window(
            win.handle, {"left": 0, "top": 0, "width": cw, "height": ch}
        )
        if frame is None or frame.size == 0:
            return
        self._frame = frame
        fh, fw = frame.shape[:2]
        self.preview.set_selection(self._roi_rect(fw, fh))
        self.preview.set_image(_bgr_to_qimage(frame))

    def _roi_rect(self, frame_w: int, frame_h: int) -> QRect | None:
        roi = self._roi
        if roi.width <= 0 or roi.height <= 0:
            return None
        return QRect(
            int(roi.x * frame_w),
            int(roi.y * frame_h),
            max(1, int(roi.width * frame_w)),
            max(1, int(roi.height * frame_h)),
        )

    def _on_selection_finished(self, rect_img: QRect) -> None:
        """框选完成只写 drone.roi，绝不触碰敌对 roi。"""
        if self._frame is None:
            return
        fh, fw = self._frame.shape[:2]
        if fw <= 0 or fh <= 0:
            return
        roi = ROI(
            x=rect_img.left() / fw,
            y=rect_img.top() / fh,
            width=rect_img.width() / fw,
            height=rect_img.height() / fh,
        )
        if roi.width < 2 / fw or roi.height < 2 / fh:
            self.region_label.setText("选区过小，请重新框选无人机区域")
            return
        self._roi = roi
        self.config.update(
            "drone",
            {
                "roi": {
                    "x": roi.x,
                    "y": roi.y,
                    "width": roi.width,
                    "height": roi.height,
                }
            },
        )
        self._update_region_label()

    def _apply(self) -> None:
        self.config.update(
            "drone",
            {
                "enabled": self.enable_check.isChecked(),
                "idle_delay": self.delay_spin.value(),
                "roi": {
                    "x": self._roi.x,
                    "y": self._roi.y,
                    "width": self._roi.width,
                    "height": self._roi.height,
                },
            },
        )
        self.close()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._capture is None:
            self._capture = Capture()
        if not self._timer.isActive():
            self._timer.start()
            self._tick()

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self.sound_player.close()
        self._capture.close()
        self._capture = None
        super().closeEvent(event)

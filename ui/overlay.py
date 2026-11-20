"""可拖动、可锁定鼠标穿透的飞行状态悬浮窗。"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui import theme


class FlightStatusOverlay(QWidget):
    """无边框置顶小窗，只展示 MonitorWorker 提供的纯状态键。"""

    closed_by_user = Signal()
    lock_toggled = Signal(bool)
    position_changed = Signal(int, int)
    _BASE_HEIGHT = 54
    _DRONE_HEIGHT = 74

    _STATES = {
        "safe": ("安全飞行中", theme.SUCCESS, 13, False),
        "hostile": ("敌袭", theme.DANGER, 18, True),
        "paused": ("已暂停", theme.YELLOW, 13, True),
        "minimized": ("目标窗口已最小化", theme.YELLOW, 11, True),
        "lost": ("目标已关闭", theme.DANGER, 13, True),
        "monitoring": ("监控中", theme.MUTED, 12, False),
        "idle": ("待机中", theme.MUTED, 12, False),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "idle"
        self._drone_state = ""
        self._locked = False
        self._drag_origin: QPoint | None = None
        self._window_origin: QPoint | None = None
        self._suppress_close_signal = False

        flags = (
            Qt.WindowStaysOnTopHint
            | Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(200, self._BASE_HEIGHT)
        self.setObjectName("flightOverlay")
        self.setStyleSheet(
            "QWidget#flightOverlay { background: transparent; }"
            "QLabel { background: transparent; }"
            "QPushButton { background: rgba(255,255,255,28); border: none;"
            " border-radius: 7px; color: #e6eaf2; padding: 0; }"
            "QPushButton:hover { background: rgba(255,255,255,55); }"
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 8, 8, 8)
        root.setSpacing(4)
        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(1)
        text_column.addStretch(1)
        self.state_label = QLabel()
        self.state_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.state_label.setTextInteractionFlags(Qt.NoTextInteraction)
        self.state_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        text_column.addWidget(self.state_label)
        self.drone_label = QLabel()
        self.drone_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.drone_label.setTextInteractionFlags(Qt.NoTextInteraction)
        self.drone_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.drone_label.hide()
        text_column.addWidget(self.drone_label)
        text_column.addStretch(1)
        root.addLayout(text_column, stretch=1)

        self.close_btn = QPushButton("×")
        self.close_btn.setToolTip("关闭悬浮窗")
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setFocusPolicy(Qt.NoFocus)
        self.close_btn.clicked.connect(self._close_by_user)
        root.addWidget(self.close_btn)

        self.lock_btn = QPushButton("🔒")
        self.lock_btn.setToolTip("锁定位置（鼠标穿透）")
        self.lock_btn.setFixedSize(22, 22)
        self.lock_btn.setFocusPolicy(Qt.NoFocus)
        self.lock_btn.clicked.connect(lambda: self.set_locked(True))
        root.addWidget(self.lock_btn)

        self.set_state("idle")

    def set_state(self, key: str) -> None:
        """切换状态文案和颜色；未知状态安全回退待机。"""
        normalized = key if key in self._STATES else "idle"
        text, color, size, bold = self._STATES[normalized]
        self._state = normalized
        self.state_label.setText(text)
        self.state_label.setStyleSheet(f"color: {color}; background: transparent;")
        font = self.state_label.font()
        font.setPointSize(size)
        font.setBold(bold)
        self.state_label.setFont(font)
        self.setToolTip(text)

    def state_key(self) -> str:
        return self._state

    def set_drone_state(self, key: str) -> None:
        """显示无人机空闲 / 受损状态；空值隐藏并恢复悬浮窗原高度。"""
        normalized = key if key in ("idle", "damaged") else ""
        self._drone_state = normalized
        if normalized == "idle":
            self.drone_label.setText("无人机：空闲")
            color = theme.SUCCESS
            size = 11
            bold = False
        elif normalized == "damaged":
            self.drone_label.setText("无人机：受损")
            color = theme.DANGER
            size = 12
            bold = True
        else:
            self.drone_label.clear()
            self.drone_label.hide()
            self.setFixedSize(200, self._BASE_HEIGHT)
            self.move(self._clamped_origin(self.x(), self.y()))
            return

        self.drone_label.setStyleSheet(
            f"color: {color}; background: transparent;"
        )
        font = self.drone_label.font()
        font.setPointSize(size)
        font.setBold(bold)
        self.drone_label.setFont(font)
        self.drone_label.show()
        self.setFixedSize(200, self._DRONE_HEIGHT)
        self.move(self._clamped_origin(self.x(), self.y()))

    def drone_state_key(self) -> str:
        return self._drone_state

    def is_locked(self) -> bool:
        return self._locked

    def set_locked(self, locked: bool) -> None:
        """切换鼠标穿透；窗口可见性、位置与尺寸保持不变。"""
        locked = bool(locked)
        if locked == self._locked:
            self._update_action_buttons()
            return
        was_visible = self.isVisible()
        position = self.pos()
        self._locked = locked
        flags = self.windowFlags()
        if locked:
            flags |= Qt.WindowTransparentForInput
        else:
            flags &= ~Qt.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._update_action_buttons()
        if was_visible:
            self.show()
            self.move(position)
        self.lock_toggled.emit(locked)

    def _update_action_buttons(self) -> None:
        self.close_btn.setVisible(not self._locked)
        self.lock_btn.setVisible(not self._locked)

    def apply_position(self, x: int | None, y: int | None) -> None:
        """恢复位置并钳制到当前屏幕可用区域。"""
        current = self.pos()
        target_x = current.x() if not isinstance(x, int) else x
        target_y = current.y() if not isinstance(y, int) else y
        self.move(self._clamped_origin(target_x, target_y))

    def reset_position(self) -> None:
        """回到主屏右上角，距可用区域边缘 24 px。"""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        rect = screen.availableGeometry()
        x = rect.right() - self.width() - 24 + 1
        y = rect.top() + 24
        self.move(self._clamped_origin(x, y))

    def _clamped_origin(self, x: int, y: int) -> QPoint:
        screen = QApplication.screenAt(QPoint(x, y))
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return QPoint(x, y)
        rect = screen.availableGeometry()
        max_x = max(rect.left(), rect.right() - self.width() + 1)
        max_y = max(rect.top(), rect.bottom() - self.height() + 1)
        return QPoint(
            min(max(x, rect.left()), max_x),
            min(max(y, rect.top()), max_y),
        )

    def _close_by_user(self) -> None:
        self.hide()
        self.position_changed.emit(self.x(), self.y())
        self.closed_by_user.emit()

    def close_for_shutdown(self) -> None:
        """由主面板关闭时抑制「用户关闭」信号，避免覆盖持久化状态。"""
        self._suppress_close_signal = True
        self.close()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.move(self._clamped_origin(self.x(), self.y()))

    def closeEvent(self, event) -> None:
        if not self._suppress_close_signal:
            self.position_changed.emit(self.x(), self.y())
            self.closed_by_user.emit()
        super().closeEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and not self._locked:
            self._drag_origin = event.globalPosition().toPoint()
            self._window_origin = self.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._drag_origin is not None
            and self._window_origin is not None
            and not self._locked
        ):
            delta = event.globalPosition().toPoint() - self._drag_origin
            self.move(
                self._clamped_origin(
                    self._window_origin.x() + delta.x(),
                    self._window_origin.y() + delta.y(),
                )
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if (
            event.button() == Qt.LeftButton
            and self._drag_origin is not None
            and not self._locked
        ):
            self._drag_origin = None
            self._window_origin = None
            self.position_changed.emit(self.x(), self.y())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(QColor(12, 16, 25, 218))
        painter.setPen(QPen(QColor(255, 255, 255, 38), 1))
        painter.drawRoundedRect(rect, 13, 13)

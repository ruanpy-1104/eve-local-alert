"""飞行状态悬浮窗 offscreen 测试。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ui import theme
from ui.overlay import FlightStatusOverlay


def test_all_states_have_expected_text_and_color(app):
    overlay = FlightStatusOverlay()
    expected = {
        "safe": ("安全飞行中", theme.SUCCESS, False),
        "hostile": ("敌袭", theme.DANGER, True),
        "paused": ("已暂停", theme.YELLOW, True),
        "minimized": ("目标窗口已最小化", theme.YELLOW, True),
        "lost": ("目标已关闭", theme.DANGER, True),
        "monitoring": ("监控中", theme.MUTED, False),
        "idle": ("待机中", theme.MUTED, False),
    }
    for key, (text, color, bold) in expected.items():
        overlay.set_state(key)
        assert overlay.state_key() == key
        assert overlay.state_label.text() == text
        assert color.lower() in overlay.state_label.styleSheet().lower()
        assert overlay.state_label.font().bold() is bold
    overlay.close()


def test_lock_toggles_transparent_input_and_keeps_visibility(app):
    overlay = FlightStatusOverlay()
    overlay.move(40, 50)
    overlay.show()
    app.processEvents()
    position = overlay.pos()
    overlay.set_locked(True)
    app.processEvents()
    assert overlay.windowFlags() & Qt.WindowTransparentForInput
    assert overlay.isVisible()
    assert overlay.pos() == position
    assert not overlay.close_btn.isVisible()
    assert not overlay.lock_btn.isVisible()

    overlay.set_locked(False)
    app.processEvents()
    assert not (overlay.windowFlags() & Qt.WindowTransparentForInput)
    assert overlay.isVisible()
    assert overlay.close_btn.isVisible()
    overlay.close()


def test_apply_position_clamps_to_available_screen(app):
    overlay = FlightStatusOverlay()
    screen = QApplication.primaryScreen()
    rect = screen.availableGeometry()
    overlay.apply_position(rect.right() + 1000, rect.bottom() + 1000)
    assert overlay.x() >= rect.left()
    assert overlay.y() >= rect.top()
    assert overlay.x() + overlay.width() <= rect.right() + 1
    assert overlay.y() + overlay.height() <= rect.bottom() + 1
    overlay.close()


def test_reset_position_is_top_right_with_margin(app):
    overlay = FlightStatusOverlay()
    overlay.reset_position()
    rect = QApplication.primaryScreen().availableGeometry()
    assert overlay.x() == rect.right() - overlay.width() - 24 + 1
    assert overlay.y() == rect.top() + 24
    overlay.close()

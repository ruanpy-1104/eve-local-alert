"""颜色选择对话框测试（offscreen）：4 色色块排版、勾选标记、应用与兜底。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.colors import COLOR_ORDER, DEFAULT_ALERT_COLORS, EVE_COLORS
from core.config import ConfigManager
from ui.panel import ColorPickerDialog


def _dialog(tmp_path, colors=None) -> ColorPickerDialog:
    cfg = ConfigManager(tmp_path / "config.json")
    if colors is not None:
        cfg.update("detection", {"colors": list(colors)})
    return ColorPickerDialog(cfg)


def test_only_four_swatches(app, tmp_path):
    dialog = _dialog(tmp_path)
    assert list(dialog._color_buttons) == list(COLOR_ORDER)
    assert len(dialog._color_buttons) == 4


def test_swatch_labels_and_marks(app, tmp_path):
    dialog = _dialog(tmp_path, colors=["red", "gray_white"])
    for name, btn in dialog._color_buttons.items():
        label = EVE_COLORS[name]["label"]
        expected = f"✓ {label}" if name in ("red", "gray_white") else label
        assert btn.text() == expected
        assert btn.isChecked() is (name in ("red", "gray_white"))


def test_toggle_updates_mark(app, tmp_path):
    dialog = _dialog(tmp_path, colors=["red"])
    orange = dialog._color_buttons["orange"]
    orange.setChecked(True)  # 与鼠标点击等价的可勾选状态变化
    dialog._on_color_toggled("orange")
    assert orange.text() == f"✓ {EVE_COLORS['orange']['label']}"


def test_apply_persists_selection_and_strictness(app, tmp_path):
    dialog = _dialog(tmp_path, colors=["red"])
    dialog._color_buttons["yellow"].setChecked(True)
    dialog._on_color_toggled("yellow")
    dialog.strictness_slider.setValue(100)
    dialog._apply()
    saved = ConfigManager(tmp_path / "config.json").data["detection"]
    assert saved["colors"] == ["red", "yellow"]
    assert saved["strictness"] == 100


def test_apply_without_selection_is_rejected(app, tmp_path):
    # 一个都不勾会让掩码恒为空 → 拒绝应用并给出提示
    dialog = _dialog(tmp_path, colors=["red"])
    dialog._color_buttons["red"].setChecked(False)
    dialog._on_color_toggled("red")
    dialog._apply()
    assert ConfigManager(tmp_path / "config.json").data["detection"]["colors"] == ["red"]
    assert "至少" in dialog.apply_status.text()


def test_defaults_are_all_four_presets(app, tmp_path):
    dialog = _dialog(tmp_path)
    assert dialog._active_colors() == DEFAULT_ALERT_COLORS

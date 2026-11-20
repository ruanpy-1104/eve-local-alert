"""无人机与悬浮窗配置规整测试。"""
from __future__ import annotations

import json

from core.config import ConfigManager, DEFAULT_CONFIG


def _load_with(tmp_path, payload):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return ConfigManager(path)


def test_v12_defaults(tmp_path):
    cfg = ConfigManager(tmp_path / "missing.json")
    assert cfg.data["drone"] == {
        "enabled": False,
        "idle_delay": 10,
        "roi": {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0},
        "idle_sound": "assets/drone_idle.wav",
        "alert_sound": "assets/drone_alert.wav",
    }
    assert cfg.data["overlay"] == {
        "visible": False,
        "locked": False,
        "x": None,
        "y": None,
    }
    assert DEFAULT_CONFIG["drone"]["enabled"] is False


def test_old_config_is_deep_merged(tmp_path):
    cfg = _load_with(tmp_path, {"roi": {"x": 0.4}})
    assert cfg.data["roi"]["x"] == 0.4
    assert cfg.data["drone"]["idle_delay"] == 10
    assert "count" not in cfg.data["drone"]
    assert cfg.data["overlay"]["visible"] is False


def test_drone_idle_delay_is_clamped_and_invalid_falls_back(tmp_path):
    cases = (
        (0, 1),
        (1, 1),
        (45, 45),
        (120, 120),
        (121, 120),
        ("30", 30),
        ("bad", 10),
        (True, 10),
    )
    for index, (value, expected) in enumerate(cases):
        cfg = _load_with(
            tmp_path / str(index),
            {"drone": {"idle_delay": value}},
        )
        assert cfg.data["drone"]["idle_delay"] == expected


def test_old_drone_count_is_discarded(tmp_path):
    cfg = _load_with(tmp_path, {"drone": {"count": 2, "enabled": True}})
    assert "count" not in cfg.data["drone"]
    assert cfg.data["drone"]["idle_delay"] == 10


def test_invalid_drone_roi_becomes_unselected(tmp_path):
    empty = {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}
    payloads = (
        {"roi": "bad"},
        {"roi": {"x": "0.1", "y": 0.2, "width": 0.3, "height": 0.4}},
        {"roi": {"x": 0.8, "y": 0.2, "width": 0.3, "height": 0.1}},
        {"roi": {"x": 0.1, "y": 0.2, "width": 0.0, "height": 0.1}},
    )
    for index, drone in enumerate(payloads):
        cfg = _load_with(tmp_path / str(index), {"drone": drone})
        assert cfg.data["drone"]["roi"] == empty


def test_overlay_coordinates_must_be_integers(tmp_path):
    cfg = _load_with(
        tmp_path,
        {"overlay": {"visible": True, "locked": True, "x": "12", "y": 3.5}},
    )
    assert cfg.data["overlay"] == {
        "visible": True,
        "locked": True,
        "x": None,
        "y": None,
    }

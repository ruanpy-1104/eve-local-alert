"""无人机设置窗口与主面板入口 offscreen 测试。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QObject, QRect, Signal

from core.config import ConfigManager
from core.drone import DroneDetector, DroneState
from core.sounds import SoundPlayer
from core.window_locator import WindowInfo
from ui import panel as panel_module
from ui.drone_dialog import DroneDialog
from ui.panel import ControlPanel, MonitorWorker


class _ClosedCapture:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _StubSignal:
    def connect(self, *_args):
        pass


class _StubWorker(QObject):
    preview = _StubSignal()
    status = _StubSignal()
    error = _StubSignal()
    alert_paused = _StubSignal()
    window_lost = _StubSignal()
    window_minimized = _StubSignal()
    flight_state = _StubSignal()
    drone_idle = _StubSignal()
    drone_damaged = _StubSignal()
    drone_status = _StubSignal()

    def __init__(self, *_args, **kwargs):
        super().__init__()
        self.kwargs = kwargs

    def start(self):
        pass

    def isRunning(self):  # noqa: N802
        return False


class _StubLocator:
    def find(self):
        return WindowInfo(1, "Target", "target.exe", (0, 0, 200, 100))


def test_sound_player_is_safe_offscreen(tmp_path):
    player = SoundPlayer(tmp_path)
    player.play_idle()
    player.start_alert()
    player.stop_alert()
    player.play_alert_once()
    player.close()


def test_sound_player_pending_requests_play_once_when_ready(monkeypatch, tmp_path):
    class _FakeEffect:
        def stop(self):
            pass

    player = SoundPlayer(tmp_path)
    player._idle_effect = _FakeEffect()
    player._alert_effect = _FakeEffect()
    idle_plays: list[bool] = []
    alert_plays: list[bool] = []
    monkeypatch.setattr(player, "_is_ready", lambda _effect: False)
    monkeypatch.setattr(player, "_play_idle_now", lambda: idle_plays.append(True))
    monkeypatch.setattr(
        player, "_play_alert_now", lambda loop: alert_plays.append(loop)
    )

    player.play_idle()
    player.play_idle()
    player.start_alert()
    player.start_alert()
    assert idle_plays == []
    assert alert_plays == []
    assert player._pending_idle is True
    assert player._pending_alert_loop is True

    monkeypatch.setattr(player, "_is_ready", lambda _effect: True)
    player._on_idle_status_changed()
    player._on_idle_status_changed()
    player._on_alert_status_changed()
    player._on_alert_status_changed()
    assert idle_plays == [True]
    assert alert_plays == [True]

    player._pending_alert_loop = True
    player.stop_alert()
    player._on_alert_status_changed()
    assert alert_plays == [True]
    assert player._pending_alert_loop is False


def test_alert_loop_count_uses_plain_integers(monkeypatch, tmp_path):
    class _StrictEffect:
        def __init__(self):
            self.loop_counts: list[int] = []
            self.plays = 0
            self.stops = 0

        def setLoopCount(self, loop_count):
            if type(loop_count) is not int:
                raise TypeError("loopCount must be int")
            self.loop_counts.append(loop_count)

        def play(self):
            self.plays += 1

        def stop(self):
            self.stops += 1

    player = SoundPlayer(tmp_path)
    effect = _StrictEffect()
    player._alert_effect = effect
    monkeypatch.setattr(player, "_ensure_effects", lambda: None)
    monkeypatch.setattr(player, "_is_ready", lambda _effect: True)

    player.start_alert()
    assert effect.loop_counts == [-2]
    assert effect.plays == 1

    player.play_alert_once()
    assert effect.loop_counts == [-2, 1]
    assert effect.stops == 1
    assert effect.plays == 2


def test_monitor_worker_mutes_drone_chime_while_paused(app, tmp_path):
    config = ConfigManager(tmp_path / "config.json")
    worker = MonitorWorker(config, tmp_path)
    emitted: list[bool] = []
    worker.drone_idle.connect(lambda: emitted.append(True))
    detector = DroneDetector()
    state = DroneState(
        idle_count=5,
        combat=False,
        returning=False,
        damaged=False,
        all_idle=True,
        idle_chime=True,
    )

    worker._paused.set()
    worker._handle_drone_state(detector, state)
    app.processEvents()
    assert emitted == []

    worker._paused.clear()
    worker._handle_drone_state(detector, state)
    app.processEvents()
    assert emitted == [True]
    worker.deleteLater()


def test_monitor_worker_emits_drone_status_with_damage_priority(app, tmp_path):
    config = ConfigManager(tmp_path / "config.json")
    worker = MonitorWorker(config, tmp_path)
    emitted: list[str] = []
    worker.drone_status.connect(emitted.append)
    detector = DroneDetector()
    detector._alarm_streak = detector.damage_frames
    state = DroneState(
        idle_count=5,
        combat=True,
        returning=False,
        damaged=True,
        all_idle=True,
        idle_chime=False,
    )

    worker._handle_drone_state(detector, state)
    worker._handle_drone_state(detector, state)
    app.processEvents()
    assert emitted == ["damaged"]

    detector._alarm_streak = 0
    worker._handle_drone_state(
        detector,
        DroneState(5, False, False, False, True, False),
    )
    app.processEvents()
    assert emitted == ["damaged", ""]

    worker._handle_drone_state(
        detector,
        DroneState(5, False, False, False, True, True),
    )
    app.processEvents()
    assert emitted == ["damaged", "", "idle"]
    worker.deleteLater()


def test_monitor_worker_idle_status_requires_actual_chime(app, tmp_path):
    config = ConfigManager(tmp_path / "config.json")
    worker = MonitorWorker(config, tmp_path)
    idle_events: list[bool] = []
    statuses: list[str] = []
    worker.drone_idle.connect(lambda: idle_events.append(True))
    worker.drone_status.connect(statuses.append)
    detector = DroneDetector()

    worker._handle_drone_state(
        detector,
        DroneState(5, False, False, False, True, False),
    )
    app.processEvents()
    assert idle_events == []
    assert statuses == []

    worker._handle_drone_state(
        detector,
        DroneState(5, False, False, False, True, True),
    )
    app.processEvents()
    assert idle_events == [True]
    assert statuses == ["idle"]

    worker._handle_drone_state(
        detector,
        DroneState(4, False, False, False, False, False),
    )
    app.processEvents()
    assert statuses == ["idle", ""]

    worker._paused.set()
    worker._handle_drone_state(
        detector,
        DroneState(5, False, False, False, True, True),
    )
    app.processEvents()
    assert idle_events == [True]
    assert statuses == ["idle", ""]
    assert worker._drone_idle_alert_active is False
    worker.deleteLater()


def test_update_drone_detector_rebuilds_and_destroys(app, tmp_path):
    config = ConfigManager(tmp_path / "config.json")
    worker = MonitorWorker(config, tmp_path)

    _, detector = worker.update_drone_detector()
    assert detector is None

    config.update(
        "drone",
        {
            "enabled": True,
            "idle_delay": 10,
            "roi": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
        },
    )
    _, first = worker.update_drone_detector()
    assert first is not None
    assert first.idle_delay_seconds == 10.0
    _, unchanged = worker.update_drone_detector()
    assert unchanged is first

    worker._drone_idle_alert_active = True
    config.update(
        "drone",
        {
            "enabled": True,
            "idle_delay": 20,
            "roi": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
        },
    )
    _, delay_changed = worker.update_drone_detector()
    assert delay_changed is not first
    assert delay_changed.idle_delay_seconds == 20.0
    assert worker._drone_idle_alert_active is False

    worker._drone_idle_alert_active = True
    config.update(
        "drone",
        {
            "enabled": True,
            "idle_delay": 20,
            "roi": {"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.2},
        },
    )
    _, roi_changed = worker.update_drone_detector()
    assert roi_changed is not delay_changed
    assert worker._drone_idle_alert_active is False

    config.update(
        "drone",
        {
            "enabled": False,
            "idle_delay": 20,
            "roi": {"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.2},
        },
    )
    _, disabled = worker.update_drone_detector()
    assert disabled is None
    assert worker._drone_idle_alert_active is False
    assert worker._drone_status == ""
    worker.deleteLater()


def test_damaged_slot_ignores_stale_or_inactive_worker(app, tmp_path, monkeypatch):
    panel = ControlPanel(tmp_path / "config.json")
    starts: list[bool] = []
    monkeypatch.setattr(
        panel._drone_sound, "start_alert", lambda: starts.append(True)
    )
    try:
        panel._monitoring_active = False
        panel._on_drone_damaged(True)
        assert starts == []

        worker = MonitorWorker(panel.config, tmp_path, panel)
        panel._worker = worker
        panel._monitoring_active = True
        worker.drone_damaged.connect(panel._on_drone_damaged)
        worker.drone_damaged.emit(True)
        app.processEvents()
        assert starts == [True]
        worker.deleteLater()
    finally:
        panel.close()


def test_panel_drone_status_reaches_overlay_and_stop_clears_it(app, tmp_path):
    panel = ControlPanel(tmp_path / "config.json")
    try:
        panel.overlay_show_action.setChecked(True)
        app.processEvents()
        overlay = panel._overlay
        assert overlay is not None

        worker = MonitorWorker(panel.config, tmp_path, panel)
        panel._worker = worker
        panel._monitoring_active = True
        worker.drone_status.connect(panel._on_drone_status)
        worker.drone_status.emit("idle")
        app.processEvents()
        assert overlay.drone_state_key() == "idle"

        panel.overlay_show_action.setChecked(False)
        worker.drone_status.emit("damaged")
        app.processEvents()
        assert overlay.isVisible() is False
        assert panel._drone_state == "damaged"
        panel.overlay_show_action.setChecked(True)
        app.processEvents()
        assert overlay.drone_state_key() == "damaged"

        panel._stop_monitor()
        app.processEvents()
        assert panel._monitoring_active is False
        assert panel._drone_state == ""
        assert overlay.drone_state_key() == ""
        worker.deleteLater()
    finally:
        panel.close()


def test_drone_dialog_selection_only_writes_drone_roi(app, tmp_path, monkeypatch):
    monkeypatch.setattr("ui.drone_dialog.Capture", _ClosedCapture)
    config = ConfigManager(tmp_path / "config.json")
    old_roi = dict(config.data["roi"])
    dialog = DroneDialog(config, tmp_path, target_handle=1)
    dialog._frame = np.zeros((100, 200, 3), dtype=np.uint8)
    dialog._on_selection_finished(QRect(20, 10, 40, 20))
    assert config.data["roi"] == old_roi
    assert config.data["drone"]["roi"] == {
        "x": 0.1,
        "y": 0.1,
        "width": 0.2,
        "height": 0.2,
    }
    dialog.close()


def test_drone_dialog_applies_idle_delay_and_enabled(app, tmp_path, monkeypatch):
    monkeypatch.setattr("ui.drone_dialog.Capture", _ClosedCapture)
    config = ConfigManager(tmp_path / "config.json")
    dialog = DroneDialog(config, tmp_path, target_handle=1)
    assert not hasattr(dialog, "count_spin")
    assert dialog.delay_spin.minimum() == 1
    assert dialog.delay_spin.maximum() == 120
    assert dialog.delay_spin.suffix() == " 秒"
    assert dialog.delay_spin.value() == 10
    dialog.delay_spin.setValue(30)
    dialog.enable_check.setChecked(True)
    dialog._apply()
    assert config.data["drone"]["idle_delay"] == 30
    assert "count" not in config.data["drone"]
    assert config.data["drone"]["enabled"] is True


def test_open_drone_dialog_keeps_monitor_running(app, tmp_path, monkeypatch):
    panel = ControlPanel(tmp_path / "config.json")
    panel._has_target = True
    panel._target_handle = 1
    sentinel = object()
    panel._worker = sentinel
    stop_calls: list[bool] = []
    start_calls: list[bool] = []
    shown: list[bool] = []

    class _StubDialog(QObject):
        def __init__(self, *_args, **_kwargs):
            super().__init__()

        def show(self):
            shown.append(True)

        def raise_(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(panel, "_worker_running", lambda: True)
    monkeypatch.setattr(panel, "_stop_monitor", lambda: stop_calls.append(True))
    monkeypatch.setattr(panel, "_start_monitor", lambda: start_calls.append(True))
    monkeypatch.setattr(panel, "_ensure_target", lambda: _StubLocator().find())
    monkeypatch.setattr("ui.panel.DroneDialog", _StubDialog)
    try:
        panel._open_drone_dialog()
        assert stop_calls == []
        assert start_calls == []
        assert shown == [True]
        assert panel._worker is sentinel
        assert panel.status_label.text() == "无人机设置已打开，预警继续运行"
    finally:
        monkeypatch.setattr(panel, "_worker_running", lambda: False)
        panel._worker = None
        panel.close()


def test_panel_has_drone_overlay_controls_and_menu_sync(app, tmp_path):
    panel = ControlPanel(tmp_path / "config.json")
    try:
        assert panel.drone_btn.text() == "无人机"
        assert panel.overlay_btn.text() == "悬浮窗"
        assert [action.text() for action in panel.overlay_menu.actions()] == [
            "显示悬浮窗",
            "锁定位置（鼠标穿透）",
            "重置位置",
        ]
        panel.overlay_show_action.setChecked(True)
        app.processEvents()
        assert panel._overlay.isVisible()
        assert panel.overlay_show_action.isChecked()
        panel._overlay.set_locked(True)
        assert panel.overlay_lock_action.isChecked()
        panel.overlay_lock_action.setChecked(False)
        assert panel._overlay.is_locked() is False
        panel.overlay_show_action.setChecked(False)
        assert not panel._overlay.isVisible()
    finally:
        panel.close()


def test_overlay_is_created_lazily_until_shown(app, tmp_path):
    panel = ControlPanel(tmp_path / "config.json")
    try:
        assert panel._overlay is None
        panel.overlay_show_action.setChecked(True)
        app.processEvents()
        assert panel._overlay is not None
    finally:
        panel.close()


def test_panel_close_hides_overlay_without_destroying_it(app, tmp_path):
    panel = ControlPanel(tmp_path / "config.json")
    panel.overlay_show_action.setChecked(True)
    app.processEvents()
    overlay = panel._overlay
    assert overlay is not None
    assert overlay in panel_module._OVERLAY_KEEPALIVE

    panel.close()
    assert overlay.isVisible() is False
    assert panel._overlay is overlay
    assert overlay in panel_module._OVERLAY_KEEPALIVE


def test_three_panels_show_and_hide_overlays_without_crash(app, tmp_path):
    for index in range(3):
        panel = ControlPanel(tmp_path / str(index) / "config.json")
        panel.overlay_show_action.setChecked(True)
        app.processEvents()
        overlay = panel._overlay
        assert overlay is not None and overlay.isVisible()
        panel.overlay_show_action.setChecked(False)
        app.processEvents()
        assert overlay.isVisible() is False
        panel.close()
        assert panel._overlay is overlay
        assert overlay in panel_module._OVERLAY_KEEPALIVE


def test_disabled_drone_config_is_not_passed_to_worker(app, tmp_path, monkeypatch):
    panel = ControlPanel(tmp_path / "config.json")
    try:
        panel._has_target = True
        panel._target_handle = 1
        monkeypatch.setattr(panel, "_make_locator", lambda: _StubLocator())
        monkeypatch.setattr("ui.panel.client_size", lambda _hwnd: (200, 100))
        monkeypatch.setattr("ui.panel.MonitorWorker", _StubWorker)
        panel._start_monitor()
        assert isinstance(panel._worker, _StubWorker)
        assert "drone_config" not in panel._worker.kwargs
    finally:
        panel.close()


def test_enabled_drone_config_is_passed_to_worker(app, tmp_path, monkeypatch):
    panel = ControlPanel(tmp_path / "config.json")
    try:
        panel.config.update(
            "drone",
            {
                "enabled": True,
                "idle_delay": 30,
                "roi": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            },
        )
        panel._has_target = True
        panel._target_handle = 1
        monkeypatch.setattr(panel, "_make_locator", lambda: _StubLocator())
        monkeypatch.setattr("ui.panel.client_size", lambda _hwnd: (200, 100))
        monkeypatch.setattr("ui.panel.MonitorWorker", _StubWorker)
        panel._start_monitor()
        assert isinstance(panel._worker, _StubWorker)
        assert panel._worker.kwargs["drone_config"]["enabled"] is True
        assert panel._worker.kwargs["drone_config"]["idle_delay"] == 30
    finally:
        panel.close()

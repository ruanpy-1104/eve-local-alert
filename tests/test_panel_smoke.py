"""控制面板冒烟测试（offscreen）：界面可构建、监控启停护栏、目标选择写回。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor

from core.config import ConfigManager
from core.window_locator import WindowInfo
from ui import theme
from ui.panel import ControlPanel, PreviewDialog


@pytest.fixture
def panel(app, tmp_path):
    widget = ControlPanel(tmp_path / "config.json")
    yield widget
    widget.close()


def test_panel_builds_without_target(panel):
    assert panel._has_target is False
    assert panel._worker_running() is False
    assert panel.pause_btn.isEnabled() is False
    assert panel.preview_btn.isEnabled() is False  # 未选择目标时「预览」置灰


def test_start_monitor_without_target_opens_picker_only(panel, monkeypatch):
    # 未选择目标：本次点击只等价于「选择程序」，不得直接开始监控（否则来不及框选）
    calls: list[str] = []
    monkeypatch.setattr(panel, "_select_target", lambda *a, **k: calls.append("picker"))
    panel._start_monitor()
    assert calls == ["picker"]
    assert panel._worker is None
    assert panel._worker_running() is False


def test_start_monitor_when_target_closed_opens_picker(panel, monkeypatch):
    # 已选过目标但窗口已关闭：同样退回选择流程，不新建监控线程
    panel._has_target = True
    panel._target_handle = 999
    calls: list[str] = []
    monkeypatch.setattr(panel, "_make_locator", lambda: _DeadLocator())
    monkeypatch.setattr(panel, "_select_target", lambda *a, **k: calls.append("picker"))
    panel._start_monitor()
    assert calls == ["picker"]
    assert panel._worker is None


def test_stop_monitor_without_worker_is_safe(panel):
    panel._stop_monitor()
    assert panel._worker is None
    assert panel.start_btn.text() == "开始预警"
    assert panel.pause_btn.isEnabled() is False


def test_target_selection_clears_process_name_when_unknown(panel, monkeypatch, tmp_path):
    # 进程名取不到（高权限进程）时必须写空串，避免残留旧进程名锁回上一个窗口
    panel.config.update("window", {"process_name": "old_game.exe"})
    win = WindowInfo(handle=1, title="New Target", process_name="", rect=(0, 0, 10, 10))
    monkeypatch.setattr(panel, "_make_locator", lambda: _StubLocator())
    monkeypatch.setattr("ui.panel.client_size", lambda _h: (100, 50))
    assert panel._on_target_selected(win) is not None
    saved = ConfigManager(tmp_path / "config.json").data["window"]
    assert saved["title_keyword"] == "New Target"
    assert saved["process_name"] == ""


class _StubLocator:
    """返回固定窗口的定位器替身（避免测试依赖真实桌面窗口）。"""

    def __init__(self, handle: int = 1):
        self._handle = handle

    def find(self) -> WindowInfo:
        return WindowInfo(
            handle=self._handle, title="New Target", process_name="", rect=(0, 0, 10, 10)
        )


class _DeadLocator:
    """目标窗口已关闭的定位器替身。"""

    def find(self) -> WindowInfo:
        raise RuntimeError("目标窗口已关闭，请重新选择目标程序")


class _StubSignal:
    def connect(self, *_args) -> None:
        pass


class _StubWorker:
    """MonitorWorker 替身：记录 target_handle 便于断言句柄已透传。"""

    def __init__(self, config, root, parent=None, target_handle=None):
        self.target_handle = target_handle
        for name in (
            "preview",
            "status",
            "error",
            "alert_paused",
            "window_lost",
            "window_minimized",
        ):
            setattr(self, name, _StubSignal())

    def start(self) -> None:
        pass

    def isRunning(self) -> bool:  # noqa: N802  Qt 风格命名
        return False


class TestTargetHandleTracking:
    """只跟踪用户点选的那个窗口句柄，避免同进程的其它窗口被当成目标。"""

    def test_selection_binds_picked_handle(self, panel, monkeypatch, tmp_path):
        monkeypatch.setattr(panel, "_make_locator", lambda: _StubLocator(handle=555))
        monkeypatch.setattr("ui.panel.client_size", lambda _h: (100, 50))
        win = WindowInfo(handle=555, title="Game", process_name="game.exe", rect=(0, 0, 9, 9))
        assert panel._on_target_selected(win) is not None
        assert panel._target_handle == 555
        assert panel._make_locator() is not None  # 真实实现由下一条用例覆盖

    def test_locator_carries_bound_handle(self, panel):
        panel._target_handle = 777
        assert panel._make_locator().prefer_handle == 777

    def test_start_monitor_passes_handle_to_worker(self, panel, monkeypatch):
        panel._target_handle = 888
        panel._has_target = True
        monkeypatch.setattr(panel, "_make_locator", lambda: _StubLocator(handle=888))
        monkeypatch.setattr("ui.panel.client_size", lambda _h: (100, 50))
        monkeypatch.setattr("ui.panel.MonitorWorker", _StubWorker)
        panel._start_monitor()
        assert panel._worker.target_handle == 888

    def test_bind_propagates_to_preview_dialog(self, panel):
        dialog = PreviewDialog(panel.config, panel._root, panel)
        panel._preview_dialog = dialog
        panel._bind_target(
            WindowInfo(handle=321, title="G", process_name="g.exe", rect=(0, 0, 9, 9))
        )
        assert panel._target_handle == 321
        assert dialog._target_handle == 321
        assert dialog._make_locator().prefer_handle == 321
        dialog.close()


def _primary_pixels(widget) -> int:
    """统计控件渲染结果中接近主题主色 (#3d7aff) 的像素数。"""
    image = widget.grab().toImage()
    ref = QColor(theme.PRIMARY)
    hits = 0
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if (
                abs(color.red() - ref.red()) < 12
                and abs(color.green() - ref.green()) < 12
                and abs(color.blue() - ref.blue()) < 12
            ):
                hits += 1
    return hits


class TestRemoteAlertButtonStyle:
    """远程预警开启时「远程预警」按钮套用主行动样式，关闭后恢复原样。

    除动态属性外还做像素级校验：动态属性变了但样式没刷新（漏 repolish）是这条改动
    最容易踩的坑，只看属性值会漏掉。
    """

    def _prepare(self, panel, app):
        panel.resize(620, 520)
        panel.layout().activate()
        app.processEvents()
        assert panel.remote_btn.width() > 0
        return panel.remote_btn

    def test_default_is_secondary(self, panel, app):
        btn = self._prepare(panel, app)
        assert btn.property("role") == ""
        assert _primary_pixels(btn) == 0

    def test_primary_when_enabled(self, panel, app):
        btn = self._prepare(panel, app)
        panel.config.update("remote_alert", {"enabled": True})
        panel._update_remote_button()
        app.processEvents()
        assert btn.property("role") == theme.ROLE_PRIMARY
        assert _primary_pixels(btn) > 100  # 真的画成主色，而非仅属性变化

    def test_reverts_when_disabled(self, panel, app):
        btn = self._prepare(panel, app)
        panel.config.update("remote_alert", {"enabled": True})
        panel._update_remote_button()
        app.processEvents()
        panel.config.update("remote_alert", {"enabled": False})
        panel._update_remote_button()
        app.processEvents()
        assert btn.property("role") == ""
        assert _primary_pixels(btn) == 0

    def test_refreshed_after_remote_dialog_applied(self, panel, monkeypatch):
        # 对话框「应用」写入配置后，面板按钮样式随之刷新
        class _StubDialog:
            def __init__(self, *_args, **_kwargs):
                pass

            def exec(self):
                panel.config.update("remote_alert", {"enabled": True})
                return 1

        monkeypatch.setattr("ui.panel.RemoteAlertDialog", _StubDialog)
        panel._open_remote_alert()
        assert panel.remote_btn.property("role") == theme.ROLE_PRIMARY

"""远程预警对话框注册入口 offscreen 测试。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from core.config import ConfigManager
from core.notifier import SERVERCHAN_REGISTER_URL
from ui.panel import RemoteAlertDialog


def _dialog(tmp_path, **remote) -> RemoteAlertDialog:
    config = ConfigManager(tmp_path / "config.json")
    if remote:
        config.update("remote_alert", remote)
    return RemoteAlertDialog(config)


def test_register_url_is_exact():
    assert SERVERCHAN_REGISTER_URL == "https://sct.ftqq.com/r/25928"


def test_register_button_visible_without_enable_or_key(app, tmp_path):
    dialog = _dialog(tmp_path)
    dialog.show()
    app.processEvents()
    assert dialog.register_btn.isVisible()
    assert dialog.register_btn.text() == "获取 SendKey"
    dialog.close()


def test_register_button_opens_register_url(app, tmp_path, monkeypatch):
    captured: list[QUrl] = []

    def fake_open_url(url: QUrl) -> bool:
        captured.append(url)
        return True

    monkeypatch.setattr(QDesktopServices, "openUrl", fake_open_url)
    dialog = _dialog(tmp_path)
    dialog.show()
    app.processEvents()
    dialog.register_btn.click()
    assert captured == [QUrl(SERVERCHAN_REGISTER_URL)]
    dialog.close()


def test_register_button_stays_visible_with_key(app, tmp_path):
    dialog = _dialog(tmp_path)
    dialog.show()
    app.processEvents()
    dialog.sendkey_edit.setText("SCT_test_key")
    app.processEvents()
    assert dialog.register_btn.isVisible()
    dialog.close()

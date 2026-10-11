"""主题 QSS 测试（offscreen）：按钮获得焦点后不残留蓝色外框（像素级校验）。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QPushButton

from ui import theme


def _apply_stylesheet(app) -> None:
    """只装全局 QSS。

    这里刻意不调用 ``theme.apply()``：它会 ``app.setStyle("Fusion")``，而在测试进程里
    已有其它模块创建的 Qt 控件时切换样式会让 Qt 崩溃（exit 0xC0000409）；应用本身是在
    创建任何控件之前调用 ``apply()``，不受影响。焦点外框来自 QSS 规则，与样式无关，
    因此只装样式表即可覆盖本条回归。
    """
    app.setStyleSheet(theme.STYLE_SHEET)


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


def _button(app, text: str, primary: bool = False) -> QPushButton:
    btn = QPushButton(text)
    if primary:
        btn.setProperty("role", theme.ROLE_PRIMARY)
    btn.resize(140, 40)
    # 刻意不 show()：与既有 test_preview.py 一致，用隐藏控件 + grab() 渲染。
    # offscreen 平台下 show() 会让控件进入可见状态，在大批量 Qt 测试会话中曾出现
    # 非确定性崩溃；隐藏控件 grab() 同样会完整渲染，够用于像素校验。
    app.processEvents()
    return btn


def test_secondary_button_has_no_focus_outline(app):
    # 点击后的按钮会保留键盘焦点：不得因此画出一圈蓝框
    _apply_stylesheet(app)
    btn = _button(app, "颜色选择")
    try:
        btn.clearFocus()
        app.processEvents()
        assert _primary_pixels(btn) == 0
        btn.setFocus()
        app.processEvents()
        assert _primary_pixels(btn) == 0
    finally:
        btn.close()


def test_primary_button_keeps_its_own_blue(app):
    # 取消焦点环不影响「开始预警」这类主行动按钮本身的蓝底样式
    _apply_stylesheet(app)
    btn = _button(app, "开始预警", primary=True)
    try:
        btn.setFocus()
        app.processEvents()
        assert _primary_pixels(btn) > 100
    finally:
        btn.close()

"""PreviewWidget 测试：自动适应绘制、坐标映射、预览内框选（offscreen 渲染）。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ui.preview import PreviewWidget


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance()
    return instance or QApplication([])


def _make_image(w: int, h: int) -> QImage:
    img = QImage(w, h, QImage.Format_RGB888)
    img.fill(QColor(255, 0, 0))
    return img


def _render(pv: PreviewWidget) -> None:
    target = QImage(pv.size(), QImage.Format_ARGB32)
    pv.render(target)  # 触发 paintEvent


class TestAutoFit:
    def test_small_image_scales_to_fit(self, app):
        """画面小于控件：等比放大填满控件，内容完整且无大面积黑边。"""
        pv = PreviewWidget()
        pv.resize(400, 300)
        pv.set_image(_make_image(100, 50))
        _render(pv)
        rect = pv._rect
        assert rect.width() == 400  # 至少一个维度填满控件
        assert abs(rect.width() / rect.height() - 2.0) < 1e-6  # 宽高比不变

    def test_large_image_fits_no_truncation(self, app):
        """画面大于控件：等比缩小适配，内容完整、宽高比不变。"""
        pv = PreviewWidget()
        pv.resize(300, 200)
        pv.set_image(_make_image(1200, 600))
        _render(pv)
        rect = pv._rect
        assert rect.width() <= 300 and rect.height() <= 200
        assert (rect.width() == 300 or rect.height() == 200)  # 至少一个维度填满
        assert abs(rect.width() / rect.height() - 2.0) < 1e-6

    def test_widget_resize_refits(self, app):
        """控件尺寸变化后自动重新适配。"""
        pv = PreviewWidget()
        pv.resize(400, 300)
        pv.set_image(_make_image(800, 200))
        _render(pv)
        first = (pv._rect.width(), pv._rect.height())
        pv.resize(800, 600)
        _render(pv)
        second = (pv._rect.width(), pv._rect.height())
        assert second[0] > first[0]  # 控件变大，画面随之放大（不超控件）
        assert second[0] <= 800 and second[1] <= 600


class TestMapping:
    def test_corners_roundtrip(self, app):
        pv = PreviewWidget()
        pv.resize(400, 300)
        pv.set_image(_make_image(200, 100))
        _render(pv)
        rect = pv._rect
        assert pv.map_to_image(rect.x(), rect.y()) == (0, 0)
        assert pv.map_to_image(rect.right(), rect.bottom()) == (199, 99)

    def test_outside_returns_none(self, app):
        pv = PreviewWidget()
        pv.resize(400, 300)
        pv.set_image(_make_image(100, 100))
        _render(pv)
        assert pv.map_to_image(-1, -1) is None

    def test_no_image_returns_none(self, app):
        pv = PreviewWidget()
        assert pv.map_to_image(10, 10) is None


class TestSelection:
    def test_enable_sets_cross_cursor(self, app):
        pv = PreviewWidget()
        pv.enable_selection(True)
        assert pv.cursor().shape() == Qt.CrossCursor
        pv.enable_selection(False)
        assert pv.cursor().shape() == Qt.ArrowCursor

    def test_drag_emits_image_rect(self, app):
        pv = PreviewWidget()
        pv.resize(400, 300)
        pv.set_image(_make_image(200, 100))
        pv.enable_selection(True)
        _render(pv)
        results = []
        pv.selection_finished.connect(results.append)
        p1, p2 = QPoint(110, 110), QPoint(190, 150)
        QTest.mousePress(pv, Qt.LeftButton, Qt.NoModifier, p1)
        QTest.mouseMove(pv, p2)
        QTest.mouseRelease(pv, Qt.LeftButton, Qt.NoModifier, p2)
        assert len(results) == 1
        # 发出的选区应与控件坐标映射到图像坐标的结果一致（图像坐标）
        a = pv.map_to_image(p1.x(), p1.y())
        b = pv.map_to_image(p2.x(), p2.y())
        from PySide6.QtCore import QRect

        expected = QRect(QPoint(*a), QPoint(*b)).normalized()
        assert results[0] == expected
        assert expected.width() > 0 and expected.height() > 0

    def test_tiny_drag_ignored(self, app):
        pv = PreviewWidget()
        pv.resize(400, 300)
        pv.set_image(_make_image(200, 100))
        pv.enable_selection(True)
        _render(pv)
        results = []
        pv.selection_finished.connect(results.append)
        QTest.mousePress(pv, Qt.LeftButton, Qt.NoModifier, QPoint(110, 110))
        QTest.mouseRelease(pv, Qt.LeftButton, Qt.NoModifier, QPoint(111, 110))
        assert results == []  # 1px 微拖不视为选区

    def test_drag_beyond_edge_clamps(self, app):
        """拖拽超出预览边界：选区吸附并对齐图像边缘。"""
        pv = PreviewWidget()
        pv.resize(400, 300)
        pv.set_image(_make_image(200, 100))
        pv.enable_selection(True)
        _render(pv)
        results = []
        pv.selection_finished.connect(results.append)
        QTest.mousePress(pv, Qt.LeftButton, Qt.NoModifier, QPoint(50, 100))
        QTest.mouseRelease(pv, Qt.LeftButton, Qt.NoModifier, QPoint(1000, 500))
        assert len(results) == 1
        rect = results[0]
        assert rect.right() == 199  # 对齐图像右边缘
        assert rect.bottom() == 99  # 对齐图像下边缘
        assert rect.left() == 25

    def test_drag_start_outside_clamps(self, app):
        """从预览外部开始拖拽：起点吸附到图像边缘。"""
        pv = PreviewWidget()
        pv.resize(400, 300)
        pv.set_image(_make_image(200, 100))
        pv.enable_selection(True)
        _render(pv)
        results = []
        pv.selection_finished.connect(results.append)
        QTest.mousePress(pv, Qt.LeftButton, Qt.NoModifier, QPoint(-50, -20))
        QTest.mouseRelease(pv, Qt.LeftButton, Qt.NoModifier, QPoint(100, 100))
        assert len(results) == 1
        rect = results[0]
        assert rect.left() == 0  # 起点吸附到图像左上角
        assert rect.top() == 0

    def test_click_emits_when_selection_disabled(self, app):
        pv = PreviewWidget()
        pv.resize(400, 300)
        pv.set_image(_make_image(200, 100))
        clicks = []
        pv.clicked.connect(lambda x, y: clicks.append((x, y)))
        QTest.mousePress(pv, Qt.LeftButton, Qt.NoModifier, QPoint(50, 60))
        QTest.mouseRelease(pv, Qt.LeftButton, Qt.NoModifier, QPoint(50, 60))
        assert clicks == [(50, 60)]

    def test_set_selection_stored(self, app):
        pv = PreviewWidget()
        from PySide6.QtCore import QRect

        rect = QRect(10, 20, 100, 50)
        pv.set_selection(rect)
        assert pv.selection() == rect

"""自动适应预览控件。

预览画面按控件尺寸自动等比适配，无需手动缩放：
- 内容超出控件时平滑缩小适配（不截断、保持完整）；
- 内容小于控件时按原始像素显示（不放大、保持清晰）；
- 控件随窗口尺寸变化自动重排，画面始终完整可见。

支持在预览画面上拖拽框选区域（框选结果以图像坐标回传），
并支持非框选模式下点击取样（供颜色校准器使用）。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ui import theme

DIM_COLOR = QColor(0, 0, 0, 120)             # 选区外暗化
SEL_BORDER = QColor(theme.SUCCESS)           # 选区描边（主题成功绿）
BACKGROUND = QColor(theme.SCREEN)            # 预览画面底（深空屏）
PLACEHOLDER_COLOR = QColor(theme.MUTED)      # 占位提示文字

MIN_SELECT = 2          # 最小有效选区边长（图像坐标，2x2 像素，避免误选单像素）
CLICK_TOLERANCE = 3     # 控件坐标：按下 / 抬起移动小于该距离视为点击，不触发框选


class PreviewWidget(QWidget):
    """自动适应预览控件，支持预览内拖拽框选与点击取样。"""

    clicked = Signal(int, int)           # 控件坐标（非框选模式）
    selection_finished = Signal(object)  # QRect（图像坐标）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image: QImage | None = None
        self._rect: QRect | None = None            # 当前绘制区域（控件坐标）
        self._placeholder = "暂无画面"
        self._selection: QRect | None = None       # 持久选区（图像坐标）
        self._sel_enabled = False
        self._drag_start: QPoint | None = None
        self._drag_current: QPoint | None = None

    # ---- 对外接口 ----
    def set_image(self, image: QImage) -> None:
        self._image = image
        self.update()

    def clear(self) -> None:
        self._image = None
        self._rect = None
        self.update()

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        self.update()

    def enable_selection(self, enabled: bool) -> None:
        """启用 / 停用框选模式（启用时拖拽框选，停用时点击取样）。"""
        self._sel_enabled = enabled
        self.setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)
        if not enabled:
            self._drag_start = None
            self._drag_current = None
        self.update()

    def set_selection(self, rect: QRect | None) -> None:
        """设置持久选区（图像坐标），用于标注已保存的 ROI。"""
        self._selection = rect
        self.update()

    def selection(self) -> QRect | None:
        return self._selection

    def map_to_image(self, x: int, y: int) -> tuple[int, int] | None:
        """把控件坐标映射到图像坐标；落在绘制区域外返回 None。"""
        if self._image is None or self._rect is None:
            return None
        if not self._rect.contains(x, y):
            return None
        ix = (x - self._rect.x()) * self._image.width() // self._rect.width()
        iy = (y - self._rect.y()) * self._image.height() // self._rect.height()
        return ix, iy

    def _clamp_to_image(self, p: QPoint) -> QPoint:
        """把控件坐标吸附到图像绘制区域内，越界自动对齐图像边缘。"""
        if self._rect is None:
            return p
        return QPoint(
            max(self._rect.left(), min(self._rect.right(), p.x())),
            max(self._rect.top(), min(self._rect.bottom(), p.y())),
        )

    # ---- 鼠标事件 ----
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._sel_enabled:
            self._drag_start = self._clamp_to_image(event.position().toPoint())
            self._drag_current = self._drag_start
            self.update()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_start is not None:
            self._drag_current = self._clamp_to_image(event.position().toPoint())
            self.update()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._sel_enabled and self._drag_start is not None and event.button() == Qt.LeftButton:
            end = self._clamp_to_image(event.position().toPoint())
            start = self._drag_start
            self._drag_start = None
            self._drag_current = None
            # 按下 / 抬起几乎未移动（控件坐标）视为误点击，不生成选区
            if (end - start).manhattanLength() < CLICK_TOLERANCE:
                self.update()
                return
            a = self.map_to_image(start.x(), start.y())
            b = self.map_to_image(end.x(), end.y())
            if a is not None and b is not None:
                rect = QRect(QPoint(*a), QPoint(*b)).normalized()
                if rect.width() >= MIN_SELECT and rect.height() >= MIN_SELECT:  # 最小支持 2x2 像素
                    self._selection = rect
                    self.selection_finished.emit(rect)
            self.update()
            return
        if event.button() == Qt.LeftButton:
            self.clicked.emit(int(event.position().x()), int(event.position().y()))
        super().mouseReleaseEvent(event)

    # ---- 绘制 ----
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), BACKGROUND)
        if self._image is None or self._image.isNull():
            painter.setPen(PLACEHOLDER_COLOR)
            painter.drawText(self.rect(), Qt.AlignCenter, self._placeholder)
            return
        self._draw_image(painter)
        sel = self._drag_rect() or self._selection
        if sel is not None:
            self._draw_selection(painter, sel)

    def _draw_image(self, painter: QPainter) -> None:
        """自动适应绘制：始终等比缩放填满控件（不截断、保持完整），消除大面积黑边。"""
        img = self._image
        scale = min(self.width() / img.width(), self.height() / img.height())
        target = QSize(
            max(1, int(img.width() * scale)),
            max(1, int(img.height() * scale)),
        )
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = QRect(0, 0, target.width(), target.height())
        rect.moveCenter(self.rect().center())
        self._rect = rect
        painter.drawImage(rect, img)

    def _drag_rect(self) -> QRect | None:
        """当前拖拽中的选区（图像坐标）；未在拖拽时返回 None。"""
        if self._drag_start is None or self._drag_current is None:
            return None
        a = self.map_to_image(self._drag_start.x(), self._drag_start.y())
        b = self.map_to_image(self._drag_current.x(), self._drag_current.y())
        if a is None or b is None:
            return None
        return QRect(QPoint(*a), QPoint(*b)).normalized()

    def _draw_selection(self, painter: QPainter, sel_img: QRect) -> None:
        if self._rect is None or self._image is None:
            return
        scale_x = self._rect.width() / self._image.width()
        scale_y = self._rect.height() / self._image.height()
        sx = self._rect.x() + int(sel_img.x() * scale_x)
        sy = self._rect.y() + int(sel_img.y() * scale_y)
        sw = max(1, int(sel_img.width() * scale_x))
        sh = max(1, int(sel_img.height() * scale_y))
        sel = QRect(sx, sy, sw, sh)
        r = self._rect
        # 选区外四边暗化，突出选中区域
        painter.fillRect(QRect(r.x(), r.y(), r.width(), sel.y() - r.y()), DIM_COLOR)
        painter.fillRect(QRect(r.x(), sel.y(), sel.x() - r.x(), sel.height()), DIM_COLOR)
        painter.fillRect(
            QRect(sel.x() + sel.width(), sel.y(), r.right() - (sel.x() + sel.width()) + 1, sel.height()),
            DIM_COLOR,
        )
        painter.fillRect(
            QRect(r.x(), sel.y() + sel.height(), r.width(), r.bottom() - (sel.y() + sel.height()) + 1),
            DIM_COLOR,
        )
        painter.setPen(QPen(SEL_BORDER, 2))
        painter.drawRect(sel)

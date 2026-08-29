"""区域选择模块。

负责 ROI 坐标在「窗口像素坐标」与「归一化坐标」之间的换算。
归一化坐标以窗口客户区为基准，范围 0~1，使监控区域在不同分辨率 / 窗口尺寸下保持稳定。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ROI:
    """归一化 ROI：x、y 为左上角，width、height 为宽高，均为 0~1 比例。"""

    x: float
    y: float
    width: float
    height: float

    @classmethod
    def from_pixels(
        cls,
        left: int,
        top: int,
        right: int,
        bottom: int,
        win_left: int,
        win_top: int,
        win_right: int,
        win_bottom: int,
    ) -> "ROI":
        """由窗口内像素矩形构造归一化 ROI。"""
        w = win_right - win_left
        h = win_bottom - win_top
        return cls(
            x=(left - win_left) / w,
            y=(top - win_top) / h,
            width=(right - left) / w,
            height=(bottom - top) / h,
        )

    def to_pixels(
        self, win_left: int, win_top: int, win_right: int, win_bottom: int
    ) -> tuple[int, int, int, int]:
        """换算为窗口内像素矩形 (left, top, right, bottom)。"""
        w = win_right - win_left
        h = win_bottom - win_top
        left = win_left + int(self.x * w)
        top = win_top + int(self.y * h)
        right = win_left + int((self.x + self.width) * w)
        bottom = win_top + int((self.y + self.height) * h)
        return left, top, right, bottom

    def to_capture_region(
        self, win_left: int, win_top: int, win_right: int, win_bottom: int
    ) -> dict[str, int]:
        """换算为 mss 需要的 {'left','top','width','height'} 字典。"""
        left, top, right, bottom = self.to_pixels(win_left, win_top, win_right, win_bottom)
        return {"left": left, "top": top, "width": right - left, "height": bottom - top}

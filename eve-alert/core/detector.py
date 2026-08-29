"""目标识别模块。

采用 HSV 阈值分割：按启用的警报颜色（EVE 总览预设色）与识别程度（严格度）
计算各颜色的 HSV 阈值区间并取并集；随后形态学去噪、轮廓筛选，
并以连续 N 帧时序确认抑制闪烁误报。
"""
from __future__ import annotations

import cv2
import numpy as np

from core.colors import DEFAULT_ALERT_COLORS, DEFAULT_STRICTNESS, color_ranges


class Detector:
    """基于颜色分割的目标检测器。"""

    def __init__(
        self,
        min_area: int = 80,
        min_aspect_ratio: float = 1.5,
        confirm_frames: int = 3,
        downscale: int = 2,
        colors=None,
        strictness: int = DEFAULT_STRICTNESS,
    ):
        self.min_area = min_area
        self.min_aspect_ratio = min_aspect_ratio
        self.confirm_frames = confirm_frames
        self.downscale = max(1, downscale)
        if colors is None:
            colors = DEFAULT_ALERT_COLORS
        elif isinstance(colors, str):
            colors = (colors,)
        self.colors = tuple(colors)
        self.strictness = max(0, min(100, int(strictness)))
        self._confirm_count = 0
        self.last_boxes: list[tuple[int, int, int, int]] = []  # 最近一次检测的框（原分辨率）

    @property
    def _h_dilate(self) -> int:
        """水平膨胀核宽度：把分散的文字笔画合并为横向长条，随下采样比例缩放。"""
        return max(5, 15 // max(1, self.downscale))

    def reset(self) -> None:
        """清空时序确认计数。"""
        self._confirm_count = 0

    def _color_ranges(self) -> list[tuple[np.ndarray, np.ndarray]]:
        """返回当前启用颜色在当前严格度下的 HSV 阈值区间。"""
        t = self.strictness / 100.0
        ranges: list[tuple[np.ndarray, np.ndarray]] = []
        for name in self.colors:
            for low, high in color_ranges(name, t):
                ranges.append((np.array(low, dtype=np.uint8), np.array(high, dtype=np.uint8)))
        return ranges

    def red_mask(self, frame_bgr: np.ndarray) -> np.ndarray:
        """返回形态学处理后的目标颜色掩码（下采样空间），供校准器可视化使用。"""
        if self.downscale > 1:
            frame_bgr = cv2.resize(
                frame_bgr,
                None,
                fx=1 / self.downscale,
                fy=1 / self.downscale,
                interpolation=cv2.INTER_AREA,
            )

        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
        for low, high in self._color_ranges():
            mask = cv2.bitwise_or(mask, cv2.inRange(hsv, low, high))

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)  # 去孤立噪点
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)  # 填小空洞
        # 水平膨胀：将名称文字离散笔画合并为横向长条，使其能通过面积 / 宽高比过滤
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (self._h_dilate, 1))
        mask = cv2.dilate(mask, h_kernel)
        return mask

    def red_boxes(self, frame_bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
        """返回全部红名候选区域的外接矩形 (x, y, w, h)（下采样空间）。"""
        mask = self.red_mask(frame_bgr)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes: list[tuple[int, int, int, int]] = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < self.min_area:
                continue
            x, y, w, h = cv2.boundingRect(cnt)
            aspect = w / h if h > 0 else 0.0
            if aspect < self.min_aspect_ratio:
                continue
            boxes.append((x, y, w, h))
        return boxes

    def has_red(self, frame_bgr: np.ndarray) -> bool:
        """单帧判定：是否包含红名候选区域（不做时序确认）。"""
        return bool(self.red_boxes(frame_bgr))

    def detect(self, frame_bgr: np.ndarray) -> bool:
        """带时序确认的判定：连续 confirm_frames 帧命中才返回 True。

        同时记录本次候选框（原分辨率）到 last_boxes，供预览叠加显示。
        """
        boxes = self.red_boxes(frame_bgr)
        scale = self.downscale
        self.last_boxes = [
            (x * scale, y * scale, w * scale, h * scale) for x, y, w, h in boxes
        ]
        if boxes:
            self._confirm_count += 1
        else:
            self._confirm_count = 0
        return self._confirm_count >= self.confirm_frames

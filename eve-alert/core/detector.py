"""目标识别模块。

EVE 总览中，每个玩家名字**前方**都有一个实心小图标（约 13–15×13–15 像素，近正方形）
用于区分友军 / 敌对 / 中立，图标颜色可在总览设置中更改（对应「警报颜色」）；
而名字文字本身始终是白色 / 灰白，不随阵营变化。

旧算法按「文字条形」检测（水平膨胀 + 宽高比 ≥ 1.5），会把灰白名字文字误判为
「灰白」目标、把背景红色恒星等 UI 元素误判为「红色」目标。改为按「图标」检测：
- 阈值分割得到颜色掩码；
- 用连通域筛出**紧凑小方块**（面积自适应、近正方形、实心填充率高）；
- 校验图标右侧存在明亮的名字文字（白光 V 高），以剔除背景中颜色相近、但
  并非「图标 + 名字」结构的干扰元素（如红色恒星）。

随后以连续 N 帧时序确认抑制闪烁误报。
"""
from __future__ import annotations

import cv2
import numpy as np

from core.colors import DEFAULT_ALERT_COLORS, DEFAULT_STRICTNESS, color_ranges


class Detector:
    """基于「颜色图标 + 名字验证」的目标检测器。"""

    def __init__(
        self,
        confirm_frames: int = 3,
        downscale: int = 2,
        colors=None,
        strictness: int = DEFAULT_STRICTNESS,
        min_area: int | None = None,
        max_area: int | None = None,
        min_aspect: float = 0.7,
        max_aspect: float = 1.6,
        min_fill: float = 0.70,
        need_name: bool = True,
        name_v: int = 90,
    ):
        self.confirm_frames = confirm_frames
        self.downscale = max(1, downscale)
        if colors is None:
            colors = DEFAULT_ALERT_COLORS
        elif isinstance(colors, str):
            colors = (colors,)
        self.colors = tuple(colors)
        self.strictness = max(0, min(100, int(strictness)))
        # 图标几何约束（面积 / 宽高比 / 填充率）。min_area、max_area 为 None 时按帧面积自适应。
        self.min_area = min_area
        self.max_area = max_area
        self.min_aspect = min_aspect
        self.max_aspect = max_aspect
        self.min_fill = min_fill
        # need_name：优先以「图标右侧对齐的明亮名字文字」剔除背景干扰；
        # 但当整帧都没有名字（纯图标 ROI）时自动退化为仅凭几何判定，保证无名字也能识别。
        self.need_name = need_name
        self.name_v = name_v
        self._confirm_count = 0
        self.last_boxes: list[tuple[int, int, int, int]] = []  # 最近一次检测的框（原分辨率）

    def reset(self) -> None:
        """清空时序确认计数。"""
        self._confirm_count = 0

    def _area_bounds(self, frame_h: int, frame_w: int) -> tuple[int, int]:
        """图标面积阈值（下采样空间）。

        游戏内可自定义名字字体 / 图标大小，且识别窗口可大可小，因此**不能用固定像素**
        判定图标大小。改为：
        - 下限：仅剔除「噪点级」小色块——取固定的极小面积与帧面积的 0.05% 二者较小值，
          大帧用固定下限、小帧用帧比例下限，不会把真实小图标丢掉；
        - 上限：按**帧面积的比例**留出余量，仅剔除铺满多数区域的整块底色（如红色背景），
          中间任意的「小方块」都保留，交给几何与名字对齐校验判定。
        """
        ds = max(1, self.downscale)
        area = max(1, frame_h * frame_w)
        lo = self.min_area if self.min_area is not None else max(
            2, min(int(60 / (ds * ds)), area // 2000)
        )
        hi = self.max_area if self.max_area is not None else max(lo + 1, area // 2)
        return max(1, int(lo)), max(int(lo) + 1, int(hi))

    def _has_aligned_name(
        self, v_chan: np.ndarray, x: int, y: int, w: int, h: int, img_h: int, w_max: int
    ) -> bool:
        """判断图标右侧是否出现**与其对齐**的明亮名字文字。

        无论字体 / 图标多大，色块都与名字**对齐**：名字与图标处于同一行（垂直居中）。
        故在图标右侧、垂直居中的带内查找明亮像素（V ≥ name_v）。带宽 / 带高均以
        图标宽高为比例，可自适应任意字体大小，而非固定像素。
        """
        rx = x + w
        rw = max(int(w * 3), 5 // max(1, self.downscale))
        y0 = max(0, y - int(h * 0.4))
        y1 = min(img_h, y + int(h * 1.4))
        band = v_chan[y0:y1, rx : min(w_max, rx + rw)]
        return bool(band.size) and band.max() >= self.name_v

    def _color_ranges(self) -> list[tuple[np.ndarray, np.ndarray]]:
        """返回当前启用颜色在当前严格度下的 HSV 阈值区间。"""
        t = self.strictness / 100.0
        ranges: list[tuple[np.ndarray, np.ndarray]] = []
        for name in self.colors:
            for low, high in color_ranges(name, t):
                ranges.append((np.array(low, dtype=np.uint8), np.array(high, dtype=np.uint8)))
        return ranges

    def _analyze(self, frame_bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """返回 (颜色掩码, 明度 V 通道)——均在（下采样后的）检测空间，供图标判定复用。"""
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

        # 开运算去孤立噪点；仅在 1 倍下采样（核 3）时叠加，2 倍以上靠面积阈值过滤即可
        open_ksize = max(1, 3 // max(1, self.downscale))
        if open_ksize > 1:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_ksize, open_ksize))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        return mask, hsv[..., 2]

    def red_mask(self, frame_bgr: np.ndarray) -> np.ndarray:
        """返回目标颜色掩码（下采样空间），供预览可视化使用。"""
        mask, _ = self._analyze(frame_bgr)
        return mask

    def red_boxes(self, frame_bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
        """返回图标候选区域的外接矩形 (x, y, w, h)（下采样空间）。

        名字验证采取**两级自适应**：先把通过几何筛选（近方形 + 高填充 + 面积自适应）的
        色块全部收集，再检查各自右侧是否有对齐的明亮名字文字。
        - 若帧内**存在**某图标带名字：说明这是「图标 + 名字」的正常总览，则只保留带名字
          的图标，从而剔除背景中颜色相近、但并非玩家条目的干扰（如红色恒星）；
        - 若整帧**都没有**名字（纯图标 ROI）：判定为「仅图标」布局，退化为仅凭几何判定，
          在无名字作为参考时也能识别图标。
        """
        mask, v_chan = self._analyze(frame_bgr)
        lo, hi = self._area_bounds(mask.shape[0], mask.shape[1])
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        img_h, w_max = mask.shape
        candidates: list[tuple[int, int, int, int]] = []
        named: list[tuple[int, int, int, int]] = []
        for i in range(1, n):
            x, y, w, h, a = stats[i]
            if a < lo or a > hi:
                continue
            aspect = w / max(1, h)
            if aspect < self.min_aspect or aspect > self.max_aspect:
                continue
            if a / (w * h) < self.min_fill:
                continue
            candidates.append((x, y, w, h))
            if self._has_aligned_name(v_chan, x, y, w, h, img_h, w_max):
                named.append((x, y, w, h))
        if not self.need_name:
            return candidates
        return named if named else candidates

    def has_red(self, frame_bgr: np.ndarray) -> bool:
        """单帧判定：是否包含图标候选区域（不做时序确认）。"""
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

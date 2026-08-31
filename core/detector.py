"""目标识别模块。

EVE 总览中，每个玩家名字**前方**都有一个实心小图标（近正方形，尺寸随名字字体可调）
用于区分友军 / 敌对 / 中立，图标颜色可在总览设置中更改（对应「警报颜色」）；
而名字文字本身始终是白色 / 灰白，不随阵营变化。

旧算法按「文字条形」检测（水平膨胀 + 宽高比 ≥ 1.5），会把灰白名字文字误判为
「灰白」目标、把背景红色恒星等 UI 元素误判为「红色」目标。改为按「图标」检测：
- 阈值分割得到颜色掩码；
- 用连通域筛出**紧凑小方块**（面积自适应、近正方形、实心填充率高）；
- 校验图标右侧存在明亮的名字文字（白光 V 高），以剔除背景中颜色相近、但
  并非「图标 + 名字」结构的干扰元素（如红色恒星）。

关键点：名字文字是白色/灰白，只会污染「灰白」掩码（彩色掩码不含白色文字）。
而彩色图标下采样后填充率会下降（约 0.7–0.8），因此**把彩色与灰白分开检测**：
- 彩色用常规填充率（避免下采样后漏掉图标）；
- 灰白用更高填充率（下采样后仍是实心方块 fill≥0.9），从而把碎笔画般的名字文字剔除。

随后以「同一位置连续 N 帧」时序确认抑制闪烁 / 位置漂移误报：目标在总览中位置固定，
位置频繁跳变（移动的目标、不同位置轮流出现的噪声）不会被累计为命中。
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
        achromatic_fill: float = 0.82,
        gray_v_max: float = 155.0,
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
        # 彩色用常规填充率；灰白掩码会被名字文字污染，故用更高填充率剔除文字。
        self.min_fill = min_fill
        self.achromatic_fill = achromatic_fill
        # 灰白还要求目标整体是「灰」而非「白」（平均明度上限）：白色名字文字 / 图标里的
        # 白色高光平均 V 很高（约 180+），而中立灰图标平均 V 偏低（约 129），据此剔除白色。
        self.gray_v_max = gray_v_max
        # need_name：优先以「图标右侧对齐的明亮名字文字」剔除背景干扰；
        # 但当整帧都没有名字（纯图标 ROI）时自动退化为仅凭几何判定，保证无名字也能识别。
        self.need_name = need_name
        self.name_v = name_v
        self._confirm_count = 0
        # 上一帧的候选框（下采样空间），用于「同一位置」时序确认。
        self._prev_boxes: list[tuple[int, int, int, int]] = []
        self.last_boxes: list[tuple[int, int, int, int]] = []  # 最近一次检测的框（原分辨率）

    def reset(self) -> None:
        """清空时序确认状态（计数与上一帧位置）。"""
        self._confirm_count = 0
        self._prev_boxes = []

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

    def _downscaled_dims(self, h: int, w: int) -> tuple[int, int]:
        """下采样后的帧尺寸。极小帧（如 2×2 选区）无法再缩小，直接返回原尺寸。"""
        ds = max(1, self.downscale)
        return max(1, h // ds), max(1, w // ds)

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

    def _mask_for(
        self, frame_bgr: np.ndarray, names: tuple[str, ...]
    ) -> tuple[np.ndarray, np.ndarray]:
        """为**指定颜色子集**构建联合掩码与明度 V 通道（均在（下采样后的）检测空间）。

        拆分彩色 / 灰白单独处理，使两者可用不同的填充率阈值去筛色块。
        """
        # 下采样：极小帧（如 2×2 选区）无法再缩小，跳过 resize 避免目标尺寸为 0 报错
        if self.downscale > 1:
            h, w = frame_bgr.shape[:2]
            th, tw = self._downscaled_dims(h, w)
            if (th, tw) != (h, w):
                frame_bgr = cv2.resize(
                    frame_bgr,
                    (tw, th),
                    interpolation=cv2.INTER_AREA,
                )
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        t = self.strictness / 100.0
        mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
        for name in names:
            for low, high in color_ranges(name, t):
                lo = np.array(low, dtype=np.uint8)
                hi = np.array(high, dtype=np.uint8)
                mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lo, hi))

        # 开运算去孤立噪点；仅在 1 倍下采样（核 3）时叠加，2 倍以上靠面积阈值过滤即可
        open_ksize = max(1, 3 // max(1, self.downscale))
        if open_ksize > 1:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_ksize, open_ksize))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        return mask, hsv[..., 2]

    def _has_left_content(self, mask: np.ndarray, x: int, y: int, w: int, h: int) -> bool:
        """判断该色块**同行左侧**是否已有同色内容。

        名字文字是一串：每个字左侧必有前一个字或图标；而真正的阵营图标位于行首，
        左侧是空白。因此白色/灰白检测时，左侧有内容的色块应判为「名字文字」而非图标。
        """
        img_h, img_w = mask.shape
        x0 = max(0, x - max(6, int(3 * w)))
        y0 = max(0, y - h // 2)
        y1 = min(img_h, y + h + h // 2)
        if x0 >= x or y0 >= y1:
            return False
        return bool(mask[y0:y1, x0:x].any())

    def _candidate_boxes(
        self, mask: np.ndarray, v_chan: np.ndarray, min_fill: float,
        gray_v_max: float | None = None, left_clear: bool = False,
    ) -> list[tuple[int, int, int, int]]:
        """在单个颜色掩码上做连通域 + 几何筛选 + 两级名字验证。

        :param min_fill: 本次检测使用的实心填充率下限（彩色与灰白不同）。
        :param gray_v_max: 灰白专用：连通域**平均明度**上限，超过则判为白色（名字文字 /
            图标中的白色高光）而剔除；其它颜色为 None 不启用。
        :param left_clear: 灰白专用：色块**同行左侧若已有同色内容**则判为名字文字而剔除，
            用于抑制极小字体名字形成的紧凑色块。
        名字验证采取**两级自适应**：先收集通过几何筛选的色块，再检查各自右侧是否有
        对齐的明亮名字文字。
        - 若帧内**存在**某图标带名字：说明是「图标 + 名字」的正常总览，只保留带名字
          的图标，剔除背景中颜色相近、但并非玩家条目的干扰（如红色恒星）；
        - 若整帧**都没有**名字（纯图标 ROI）：退化为仅凭几何判定，无名字也能识别。
        """
        lo, hi = self._area_bounds(mask.shape[0], mask.shape[1])
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
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
            if a / (w * h) < min_fill:
                continue
            # 灰白：目标须是「灰」而非「白」，用平均明度剔除白色文字 / 高光
            if gray_v_max is not None and v_chan[labels == i].mean() > gray_v_max:
                continue
            # 灰白：同行左侧已有同色内容 → 是名字文字而非行首图标
            if left_clear and self._has_left_content(mask, x, y, w, h):
                continue
            candidates.append((x, y, w, h))
            if self._has_aligned_name(v_chan, x, y, w, h, img_h, w_max):
                named.append((x, y, w, h))
        if not self.need_name:
            return candidates
        return named if named else candidates

    def red_mask(self, frame_bgr: np.ndarray) -> np.ndarray:
        """返回目标颜色掩码（下采样空间，所有启用颜色并集），供预览可视化使用。"""
        mask, _ = self._mask_for(frame_bgr, self.colors)
        return mask

    def red_boxes(self, frame_bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
        """返回图标候选区域的外接矩形 (x, y, w, h)（下采样空间）。

        名字文字是白色/灰白，会进入「灰白」掩码（彩色掩码不含白色文字）；而彩色图标
        下采样后填充率降至 0.7–0.8。因此：
        - 彩色（含黑）用常规填充率 `min_fill`，避免下采样后漏掉图标；
        - 灰白用更高填充率 `achromatic_fill` 检测，碎笔画般的名字文字被填充率剔除。
        两组掩码按颜色互斥（彩色 S 高、灰白 S 低），检测结果不相交，直接拼接。
        """
        gray = tuple(c for c in self.colors if c == "gray_white")
        colored = tuple(c for c in self.colors if c != "gray_white")
        boxes: list[tuple[int, int, int, int]] = []
        if colored:
            m, v = self._mask_for(frame_bgr, colored)
            boxes += self._candidate_boxes(m, v, self.min_fill)
        if gray:
            m, v = self._mask_for(frame_bgr, gray)
            boxes += self._candidate_boxes(
                m, v, self.achromatic_fill, gray_v_max=self.gray_v_max, left_clear=True
            )
        return boxes

    def has_red(self, frame_bgr: np.ndarray) -> bool:
        """单帧判定：是否包含图标候选区域（不做时序确认）。"""
        return bool(self.red_boxes(frame_bgr))

    def _overlaps_previous(
        self,
        boxes: list[tuple[int, int, int, int]],
        prev: list[tuple[int, int, int, int]],
    ) -> bool:
        """判断当前候选框中是否有与上一帧**同一位置**延续的框。

        总览里的玩家条目位置固定，图标在相邻帧几乎重合；下采样会带来 ±1~2 像素抖动，
        故以「中心偏移不超过框尺寸的 75%」作为同一位置的容差。只要任一候选框在上一帧
        相同位置持续出现，即视为延续；位置整体跳变（如总览滚动、不同位置轮流出现）则
        视为新位置，重新累计时序确认。
        """
        for bx, by, bw, bh in boxes:
            bcx, bcy = bx + bw / 2.0, by + bh / 2.0
            for px, py, pw, ph in prev:
                pcx, pcy = px + pw / 2.0, py + ph / 2.0
                tol = max(bw, bh, pw, ph) * 0.75
                if abs(bcx - pcx) <= tol and abs(bcy - pcy) <= tol:
                    return True
        return False

    def detect(self, frame_bgr: np.ndarray) -> bool:
        """带时序确认的判定：**同一位置**连续 confirm_frames 帧命中才返回 True。

        相较旧版「连续命中即可」，这里额外要求命中位置稳定：目标在总览里静止，位置
        漂移（移动的目标、不同位置轮流出现的噪声、总览滚动）会被视为新目标而重新累计。
        同时记录本次候选框（原分辨率）到 last_boxes，供预览叠加显示。
        """
        boxes = self.red_boxes(frame_bgr)
        # 实际缩放比例：极小帧跳过 resize 时框已位于原分辨率，不可再乘缩放因子
        h, w = frame_bgr.shape[:2]
        scale = self.downscale if self._downscaled_dims(h, w) != (h, w) else 1
        self.last_boxes = [
            (x * scale, y * scale, w * scale, h * scale) for x, y, w, h in boxes
        ]
        if not boxes:
            self._confirm_count = 0
            self._prev_boxes = []
            return False
        if self._prev_boxes and not self._overlaps_previous(boxes, self._prev_boxes):
            # 命中位置发生变化：作为新位置的首帧重新累计
            self._confirm_count = 1
        else:
            # 首帧命中，或与上一帧同一位置延续
            self._confirm_count += 1
        self._prev_boxes = boxes
        return self._confirm_count >= self.confirm_frames

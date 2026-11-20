"""EVE 总览阵营方格检测。

检测流程与名字、中心图形均无关：

1. 在 ROI 原分辨率上寻找与局部背景有色差/亮度差的方格轮廓；
2. 用外框填充率、四角填充率、非中心面颜色一致性和背景对比度筛选；
3. 只采样外环与四角颜色，将方格分为敌对、友军或 unknown；
4. 从同一 x 列的两行以上方格估计固定行距网格，丢弃网格外干扰；
5. 对当前帧敌对方格建立按位置/颜色区分的连续帧计数。

友军蓝/绿/紫方格参与网格建立，但不会进入 ``last_boxes`` 或触发警报。
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from core.colors import (
    CLASSIFIER_COLORS,
    COLOR_ALIASES,
    DEFAULT_ALERT_COLORS,
    DEFAULT_STRICTNESS,
)

_HOSTILE_COLORS = frozenset(("red", "orange", "yellow", "gray_white"))
_FRIENDLY_COLORS = frozenset(("blue", "green", "purple"))
_KNOWN_COLORS = _HOSTILE_COLORS | _FRIENDLY_COLORS


@dataclass
class _Square:
    x: int
    y: int
    w: int
    h: int
    cx: float
    cy: float
    size: float
    score: float
    corner_fill: float
    face_fill: float
    contrast: float
    med_h: int
    med_s: int
    med_v: int
    color: str = "unknown"
    is_hostile: bool = False

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h

    @property
    def hostile(self) -> bool:
        return self.is_hostile


@dataclass
class _Track:
    count: int
    cx: float
    cy: float
    size: float
    color: str


class Detector:
    """检测总览图标列中的敌对方格。

    ``downscale`` 与旧版几何参数仅为兼容旧配置而接受，实际检测始终使用
    输入 ROI 的 1× 分辨率。
    """

    def __init__(
        self,
        confirm_frames: int = 3,
        downscale: int = 2,
        colors=None,
        strictness: int = DEFAULT_STRICTNESS,
        **legacy,
    ):
        del legacy
        try:
            self.confirm_frames = max(1, int(confirm_frames))
        except (TypeError, ValueError, OverflowError):
            self.confirm_frames = 3
        # 保留旧字段供调用方观察，但不再缩放图像。
        try:
            self.downscale = max(1, int(downscale))
        except (TypeError, ValueError, OverflowError):
            self.downscale = 1

        if colors is None:
            colors = DEFAULT_ALERT_COLORS
        elif isinstance(colors, str):
            colors = (colors,)
        self.colors = tuple(colors)
        self._enabled_hostile = frozenset(
            canonical
            for name in self.colors
            if isinstance(name, str)
            for canonical in (COLOR_ALIASES.get(name, name),)
            if canonical in _HOSTILE_COLORS
        )
        try:
            self.strictness = max(0, min(100, int(strictness)))
        except (TypeError, ValueError, OverflowError):
            self.strictness = DEFAULT_STRICTNESS

        self.last_boxes: list[tuple[int, int, int, int]] = []
        self.last_all_boxes: list[tuple[int, int, int, int]] = []
        self.last_grid: dict | None = None

        self._tracks: dict[tuple[int, int, str], _Track] = {}
        self._grid: dict | None = None
        self._grid_misses = 0
        self._grid_loss_limit = max(2, self.confirm_frames)

    def reset(self) -> None:
        """清空网格沿用状态与全部连续帧轨迹。"""
        self._tracks.clear()
        self._grid = None
        self._grid_misses = 0
        self.last_boxes = []
        self.last_all_boxes = []
        self.last_grid = None

    @property
    def _t(self) -> float:
        return self.strictness / 100.0

    @property
    def _contrast_floor(self) -> float:
        # 默认 10；白图标 V185 对 V205 背景仍可稳定越过。
        return 4.5 + 8.0 * self._t

    @property
    def _single_contrast_floor(self) -> float:
        return self._contrast_floor + 2.0 + 3.0 * self._t

    def _as_bgr(self, frame_bgr: np.ndarray) -> np.ndarray | None:
        if frame_bgr is None:
            return None
        image = np.asarray(frame_bgr)
        if image.size == 0:
            return None
        if image.dtype != np.uint8:
            image = np.clip(image, 0, 255).astype(np.uint8)
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        elif image.ndim == 3 and image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
        if image.ndim != 3 or image.shape[2] != 3:
            return None
        return image

    def _foreground_masks(
        self, frame_bgr: np.ndarray
    ) -> tuple[
        np.ndarray,
        np.ndarray,
        np.ndarray,
        np.ndarray,
        np.ndarray,
        np.ndarray,
        np.ndarray,
    ]:
        """返回局部灰差、色差、边缘和低对比暗面种子以及 Lab/HSV。"""
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        lab = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB)
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)

        ksize = 9
        gray_bg = cv2.medianBlur(gray, ksize)
        lab_bg = cv2.medianBlur(lab, ksize)
        gray_delta = cv2.absdiff(gray, gray_bg)
        lab_delta = cv2.absdiff(lab, lab_bg).max(axis=2)
        floor = self._contrast_floor
        contrast_mask = ((gray_delta >= floor) | (lab_delta >= floor)).astype(np.uint8) * 255
        chroma = np.maximum(cv2.absdiff(lab[..., 1], 128), cv2.absdiff(lab[..., 2], 128))
        chroma_floor = int(round(18 + 14 * self._t))
        chroma_mask = (
            (chroma >= chroma_floor) & (hsv[..., 2] >= max(20, int(25 + 15 * self._t)))
        ).astype(np.uint8) * 255
        edge_low = int(round(7 + 8 * self._t))
        edge_high = int(round(18 + 14 * self._t))
        edges = cv2.Canny(gray, edge_low, edge_high, L2gradient=True)

        # 大尺度块估计独立于某个固定颜色的局部背景。对“略暗于亮墙”的
        # 纯色面，自适应阈值能得到完整方块；水平闭运算只保留确实更暗的像素，
        # 避免平滑渐变自身被当成前景。
        max_side = min(48, max(15, int(round(min(gray.shape) * 0.45))))
        block = max_side + 3
        block = min(51, block + 1 - block % 2)
        margin = int(round(2 + 3 * self._t))
        dark_adaptive = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            block,
            margin,
        )
        horizontal_bg = cv2.morphologyEx(
            gray,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (block, 1)),
            borderType=cv2.BORDER_REPLICATE,
        )
        dark_delta = cv2.subtract(horizontal_bg, gray)
        darker_than_bg = (dark_delta >= max(1, int(round(1 + 2 * self._t)))).astype(
            np.uint8
        ) * 255
        dark_surface = cv2.bitwise_and(dark_adaptive, darker_than_bg)

        foreground = cv2.bitwise_or(cv2.bitwise_or(contrast_mask, chroma_mask), edges)
        foreground = cv2.bitwise_or(foreground, dark_surface)
        return (
            foreground,
            lab,
            hsv,
            chroma_mask,
            contrast_mask,
            edges,
            dark_surface,
        )

    def _surface_stats(
        self,
        image_lab: np.ndarray,
        image_hsv: np.ndarray,
        x: int,
        y: int,
        w: int,
        h: int,
    ) -> tuple[float, float, float, int, int, int]:
        side = max(1, min(w, h))
        inset = max(1, int(round(side * 0.10)))
        corner = max(1, int(round(side * 0.12)))

        surface_mask = np.zeros((h, w), dtype=bool)
        inner_h = max(inset + 1, h - inset)
        inner_w = max(inset + 1, w - inset)
        surface_mask[inset, inset:inner_w] = True
        surface_mask[inner_h - 1, inset:inner_w] = True
        surface_mask[inset:inner_h, inset] = True
        surface_mask[inset:inner_h, inner_w - 1] = True

        lab_roi = image_lab[y : y + h, x : x + w]
        hsv_roi = image_hsv[y : y + h, x : x + w]
        surface_pixels = lab_roi[surface_mask]
        if surface_pixels.size == 0:
            return 0.0, 0.0, 0.0, 0, 0, 0
        reference = np.median(surface_pixels, axis=0)

        corner_mask = np.zeros((h, w), dtype=bool)
        corner_start = 1 if h > 3 and w > 3 else 0
        for yy in (
            slice(corner_start, min(inner_h, corner_start + corner)),
            slice(max(corner_start, inner_h - corner), inner_h),
        ):
            for xx in (
                slice(corner_start, min(inner_w, corner_start + corner)),
                slice(max(corner_start, inner_w - corner), inner_w),
            ):
                corner_mask[yy, xx] = True

        face_mask = np.zeros((h, w), dtype=bool)
        face_mask[inset:inner_h, inset:inner_w] = True
        face_mask[
            int(h * 0.25) : max(int(h * 0.25) + 1, int(round(h * 0.75))),
            int(w * 0.25) : max(int(w * 0.25) + 1, int(round(w * 0.75))),
        ] = False
        distance = np.abs(
            lab_roi.astype(np.int16) - reference.astype(np.int16)
        ).max(axis=2)
        color_tolerance = int(round(30 - 5 * self._t))
        face_fill = float((distance[face_mask] <= color_tolerance).mean())
        corner_fill = float((distance[corner_mask] <= color_tolerance).mean())

        pad = max(2, int(round(side * 0.30)))
        y0, y1 = max(0, y - pad), min(image_lab.shape[0], y + h + pad)
        x0, x1 = max(0, x - pad), min(image_lab.shape[1], x + w + pad)
        outer_lab = image_lab[y0:y1, x0:x1]
        outer_mask = np.ones(outer_lab.shape[:2], dtype=bool)
        outer_mask[y - y0 : y - y0 + h, x - x0 : x - x0 + w] = False
        background_pixels = outer_lab[outer_mask]
        if background_pixels.size:
            background = np.median(background_pixels, axis=0)
            contrast = float(
                np.abs(reference.astype(np.int16) - background.astype(np.int16)).max()
            )
        else:
            contrast = 0.0

        hsv_pixels = hsv_roi[surface_mask]
        med_h, med_s, med_v = np.median(hsv_pixels, axis=0)
        return (
            corner_fill,
            face_fill,
            contrast,
            int(round(float(med_h))),
            int(round(float(med_s))),
            int(round(float(med_v))),
        )

    @staticmethod
    def _axis_aligned(angle: float) -> bool:
        normalized = abs(float(angle)) % 90.0
        return min(normalized, 90.0 - normalized) <= 8.0

    @staticmethod
    def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        left, top = max(ax, bx), max(ay, by)
        right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
        intersection = max(0, right - left) * max(0, bottom - top)
        if intersection == 0:
            return 0.0
        union = aw * ah + bw * bh - intersection
        return intersection / max(1, union)

    def _extract_candidates(
        self, frame_bgr: np.ndarray
    ) -> tuple[list[_Square], np.ndarray, np.ndarray]:
        (
            foreground,
            lab,
            hsv,
            chroma_mask,
            contrast_mask,
            edges,
            dark_surface,
        ) = self._foreground_masks(frame_bgr)
        contours: list[tuple[np.ndarray, bool]] = []
        for candidate_mask in (chroma_mask, contrast_mask, edges, dark_surface):
            found, _ = cv2.findContours(
                candidate_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            contours.extend((contour, candidate_mask is dark_surface) for contour in found)
        shape_foreground = cv2.bitwise_or(
            cv2.bitwise_or(chroma_mask, contrast_mask), edges
        )
        candidates: list[_Square] = []

        for contour, low_contrast_dark in contours:
            x, y, box_w, box_h = cv2.boundingRect(contour)
            side = min(box_w, box_h)
            split_dark = (
                low_contrast_dark
                and side >= 10
                and max(box_w, box_h) <= 50
                and max(box_w, box_h) / max(1, side) > 1.33
            )
            if split_dark:
                # 低对比暗面容易和同一行的文字连成宽轮廓；以轮廓起点为方块
                # 起始位置截取一个近方形窗口，再交由四角/面/网格校验。
                w = h = side
                contour_fill = 1.0
            else:
                (_, _), (rect_w, rect_h), angle = cv2.minAreaRect(contour)
                short_side = min(rect_w, rect_h)
                long_side = max(rect_w, rect_h)
                if short_side < (10.0 if low_contrast_dark else 5.0) or long_side > 48.0:
                    continue
                if long_side / max(1e-6, short_side) > 1.33:
                    continue
                if not self._axis_aligned(angle):
                    continue
                w, h = box_w, box_h
                if w < 5 or h < 5 or w > 50 or h > 50:
                    continue
                if min(w, h) / max(1, max(w, h)) < 0.75:
                    continue
                contour_fill = float(cv2.contourArea(contour)) / max(1, w * h)
            if contour_fill < 0.60 and not low_contrast_dark:
                continue

            corner_fill, face_fill, contrast, med_h, med_s, med_v = self._surface_stats(
                lab, hsv, x, y, w, h
            )
            if corner_fill < 0.60 or face_fill < 0.45 + 0.05 * self._t:
                continue
            if med_s < 70 and face_fill < 0.68:
                continue
            if low_contrast_dark and (med_s >= 70 or med_v < 175):
                continue
            contrast_floor = (
                max(1.5, 1.0 + 2.0 * self._t)
                if low_contrast_dark
                else self._contrast_floor
            )
            if contrast < contrast_floor:
                continue
            foreground_fill = float(
                shape_foreground[y : y + h, x : x + w].mean()
            ) / 255.0
            # 形状前景应连成面；中心颜色分类仍完全跳过中心 50%。
            if med_s >= 70 and foreground_fill < 0.88:
                continue

            size = float(min(w, h))
            score = (
                0.36 * min(1.0, face_fill)
                + 0.24 * min(1.0, corner_fill)
                + 0.20 * min(1.0, contour_fill)
                + 0.20 * min(1.0, contrast / 45.0)
            )
            candidates.append(
                _Square(
                    x=int(x),
                    y=int(y),
                    w=int(w),
                    h=int(h),
                    cx=x + w / 2.0,
                    cy=y + h / 2.0,
                    size=size,
                    score=float(score),
                    corner_fill=corner_fill,
                    face_fill=face_fill,
                    contrast=contrast,
                    med_h=med_h,
                    med_s=med_s,
                    med_v=med_v,
                )
            )

        candidates.sort(key=lambda c: c.score, reverse=True)
        kept: list[_Square] = []
        for candidate in candidates:
            duplicate = False
            for previous in kept:
                center_close = (
                    abs(candidate.cx - previous.cx) <= 0.38 * min(candidate.size, previous.size)
                    and abs(candidate.cy - previous.cy)
                    <= 0.38 * min(candidate.size, previous.size)
                )
                if center_close or self._iou(candidate.box, previous.box) >= 0.45:
                    duplicate = True
                    break
            if not duplicate:
                kept.append(candidate)

        for candidate in kept:
            candidate.color = self._classify(candidate)
            candidate.is_hostile = candidate.color in self._enabled_hostile
        known = [candidate for candidate in kept if candidate.color in _KNOWN_COLORS]
        known.sort(key=lambda c: (c.cy, c.cx))
        return known, lab, hsv

    @staticmethod
    def _hue_distance(a: int, b: int) -> int:
        delta = abs(int(a) - int(b)) % 180
        return min(delta, 180 - delta)

    def _classify(self, candidate: _Square) -> str:
        s_floor = int(round(70 + 30 * self._t))
        v_floor = int(round(40 + 35 * self._t))
        white_floor = int(round(75 + 30 * self._t))

        if candidate.med_s < s_floor:
            return "gray_white" if candidate.med_v >= white_floor else "unknown"
        if candidate.med_v < v_floor:
            return "unknown"

        nearest = "unknown"
        nearest_distance = 181
        for name, info in CLASSIFIER_COLORS.items():
            hue = info.get("hue")
            if hue is None:
                continue
            distance = self._hue_distance(candidate.med_h, hue)
            if distance < nearest_distance:
                nearest, nearest_distance = name, distance
        tolerance = int(round(13 + 5 * self._t))
        return nearest if nearest_distance <= tolerance else "unknown"

    def _column_cluster(self, candidates: list[_Square]) -> list[_Square]:
        if not candidates:
            return []
        ordered = sorted(candidates, key=lambda c: c.cx)
        groups: list[list[_Square]] = [[ordered[0]]]
        for candidate in ordered[1:]:
            previous = groups[-1][-1]
            tolerance = max(1.5, 0.30 * min(previous.size, candidate.size))
            if candidate.cx - previous.cx <= tolerance:
                groups[-1].append(candidate)
            else:
                groups.append([candidate])
        return max(groups, key=lambda group: (len(group), sum(c.score for c in group)))

    @staticmethod
    def _fit_grid(cluster: list[_Square]) -> dict | None:
        if len(cluster) < 2:
            return None
        ordered = sorted(cluster, key=lambda c: c.cy)
        size = float(np.median([c.size for c in ordered]))
        if size <= 0:
            return None
        low, high = 1.10 * size, 2.80 * size
        gaps = [
            ordered[index].cy - ordered[index - 1].cy
            for index in range(1, len(ordered))
        ]
        direct = [gap for gap in gaps if low <= gap <= high]
        if direct:
            # 少行列表可能包含 2×/3× 空槽 gap；最小的一致 gap 才是基础行距。
            smallest = min(direct)
            consistent = [gap for gap in direct if gap <= smallest * 1.20]
            pitch = float(np.median(consistent))
        else:
            divided = [
                gap / count
                for gap in gaps
                for count in range(1, 6)
                if low <= gap / count <= high
            ]
            if not divided:
                return None
            pitch = float(np.median(divided))
        if not low <= pitch <= high:
            return None

        y0 = ordered[0].cy
        residuals = [
            (candidate.cy - y0) - round((candidate.cy - y0) / pitch) * pitch
            for candidate in ordered
        ]
        phase = y0 + float(np.median(residuals))
        tolerance = max(1.0, 0.35 * pitch)
        rows = [
            candidate
            for candidate in ordered
            if abs(candidate.cy - (phase + round((candidate.cy - phase) / pitch) * pitch))
            <= tolerance
        ]
        if len(rows) < 2:
            return None
        x = float(np.median([c.cx for c in rows]))
        size = float(np.median([c.size for c in rows]))
        return {
            "x": x,
            "size": size,
            "pitch": pitch,
            "rows": len(rows),
            "phase": phase,
        }

    def _accept_for_grid(
        self, candidates: list[_Square], grid: dict
    ) -> list[_Square]:
        size = float(grid["size"])
        pitch = float(grid["pitch"])
        phase = float(grid["phase"])
        x = float(grid["x"])
        accepted = []
        for candidate in candidates:
            if abs(candidate.cx - x) > max(1.0, 0.35 * size):
                continue
            if not 0.70 <= candidate.size / max(1e-6, size) <= 1.35:
                continue
            line_y = phase + round((candidate.cy - phase) / pitch) * pitch
            if abs(candidate.cy - line_y) <= max(1.0, 0.35 * pitch):
                accepted.append(candidate)
        return accepted

    def _match_previous_grid(
        self, candidates: list[_Square], grid: dict
    ) -> tuple[dict | None, list[_Square]]:
        size = float(grid["size"])
        pitch = float(grid["pitch"])
        old_phase = float(grid["phase"])
        x = float(grid["x"])
        eligible = [
            candidate
            for candidate in candidates
            if abs(candidate.cx - x) <= max(1.0, 0.40 * size)
            and 0.70 <= candidate.size / max(1e-6, size) <= 1.35
        ]
        if not eligible:
            return None, []

        shifts = []
        for candidate in eligible:
            nearest = old_phase + round((candidate.cy - old_phase) / pitch) * pitch
            shift = candidate.cy - nearest
            if abs(shift) <= pitch / 2.0 + 1e-6:
                shifts.append(shift)
        if not shifts:
            return None, []

        phase = old_phase + float(np.median(shifts))
        matched = []
        tolerance = max(1.0, 0.35 * pitch)
        for candidate in eligible:
            line_y = phase + round((candidate.cy - phase) / pitch) * pitch
            if abs(candidate.cy - line_y) <= tolerance:
                matched.append(candidate)
        if not matched:
            return None, []

        updated = {
            "x": float(np.median([c.cx for c in matched])),
            "size": float(np.median([c.size for c in matched])),
            "pitch": pitch,
            "rows": len(matched),
            "phase": phase,
        }
        return updated, matched

    def _lock_or_keep_grid(self, candidates: list[_Square]) -> list[_Square]:
        if self._grid is not None:
            updated, accepted = self._match_previous_grid(candidates, self._grid)
            if updated is not None:
                self._grid = updated
                self._grid_misses = 0
                return accepted
            self._grid_misses += 1
            if self._grid_misses < self._grid_loss_limit:
                return []
            self._grid = None

        cluster = self._column_cluster(candidates)
        grid = self._fit_grid(cluster)
        if grid is None:
            return []
        accepted = self._accept_for_grid(cluster, grid)
        if len(accepted) < 2:
            return []
        grid["rows"] = len(accepted)
        grid["x"] = float(np.median([c.cx for c in accepted]))
        grid["size"] = float(np.median([c.size for c in accepted]))
        self._grid = grid
        self._grid_misses = 0
        return accepted

    @staticmethod
    def _slot_has_bright_edge_evidence(
        image_hsv: np.ndarray,
        x0: int,
        y0: int,
        size: int,
    ) -> bool:
        v_channel = image_hsv[..., 2]
        center_lo = size // 4
        center_hi = max(center_lo + 1, (size * 3) // 4)
        bright_count = int(
            np.count_nonzero(
                v_channel[
                    y0 + center_lo : y0 + center_hi,
                    x0 + center_lo : x0 + center_hi,
                ]
                >= 220
            )
        )
        if bright_count < max(6, size // 2):
            return False

        inset = max(1, size // 5)
        outer = max(1, size // 6)
        band = max(1, size // 8)
        height, width = v_channel.shape
        if (
            y0 - outer < 0
            or y0 + size + outer > height
            or x0 - outer < 0
            or x0 + size + outer > width
        ):
            return False
        inner = (
            v_channel[
                y0 + inset : y0 + inset + band,
                x0 + inset : x0 + size - inset,
            ],
            v_channel[
                y0 + size - inset - band : y0 + size - inset,
                x0 + inset : x0 + size - inset,
            ],
            v_channel[
                y0 + inset : y0 + size - inset,
                x0 + inset : x0 + inset + band,
            ],
            v_channel[
                y0 + inset : y0 + size - inset,
                x0 + size - inset - band : x0 + size - inset,
            ],
        )
        outside = (
            v_channel[
                y0 - outer : y0,
                x0 + inset : x0 + size - inset,
            ],
            v_channel[
                y0 + size : y0 + size + outer,
                x0 + inset : x0 + size - inset,
            ],
            v_channel[
                y0 + inset : y0 + size - inset,
                x0 - outer : x0,
            ],
            v_channel[
                y0 + inset : y0 + size - inset,
                x0 + size : x0 + size + outer,
            ],
        )
        steps = 0
        for inner_strip, outer_strip in zip(inner, outside):
            if (
                inner_strip.size
                and outer_strip.size
                and abs(float(inner_strip.mean()) - float(outer_strip.mean())) >= 4.0
            ):
                steps += 1
        return steps >= 2

    def _complete_grid_slots(
        self,
        accepted: list[_Square],
        image_lab: np.ndarray,
        image_hsv: np.ndarray,
    ) -> list[_Square]:
        """补回被友军网格夹住、但暗面种子缺失的低对比白方格。"""
        if not accepted or self._grid is None or int(self._grid["rows"]) < 2:
            return accepted

        size = max(5, int(round(float(self._grid["size"]))))
        x = float(self._grid["x"])
        pitch = float(self._grid["pitch"])
        phase = float(self._grid["phase"])
        x0 = int(round(x - size / 2.0))
        height, width = image_lab.shape[:2]
        if x0 < 0 or x0 + size > width:
            return accepted

        first = int(np.floor((0 - phase) / pitch)) - 1
        last = int(np.ceil((height - phase) / pitch)) + 1
        white_floor = int(round(75 + 30 * self._t))
        white_contrast_floor = 1.5 + 2.0 * self._t
        completed = list(accepted)
        first_cy = min(candidate.cy for candidate in accepted)
        last_cy = max(candidate.cy for candidate in accepted)

        for line in range(first, last + 1):
            line_cy = phase + line * pitch
            if line_cy < first_cy or line_cy > last_cy:
                continue
            base_y0 = int(round(line_cy - size / 2.0))
            selected: tuple[int, tuple[float, float, float, int, int, int]] | None = None
            for y0 in (base_y0, base_y0 - 1, base_y0 + 1):
                if y0 < 0 or y0 + size > height:
                    continue
                stats = self._surface_stats(image_lab, image_hsv, x0, y0, size, size)
                corner, face, contrast, med_h, med_s, med_v = stats
                if (
                    corner < 0.78
                    or face < 0.68
                    or med_s >= 70
                    or med_v < white_floor
                ):
                    continue
                if (
                    contrast >= white_contrast_floor
                    or self._slot_has_bright_edge_evidence(
                        image_hsv, x0, y0, size
                    )
                ):
                    selected = (y0, stats)
                    break
            if selected is None:
                continue
            y0, (corner, face, contrast, med_h, med_s, med_v) = selected
            cy = y0 + size / 2.0
            if any(
                abs(candidate.cx - x) <= 0.35 * size
                and abs(candidate.cy - cy) <= 0.35 * size
                for candidate in completed
            ):
                continue
            candidate = _Square(
                x=x0,
                y=y0,
                w=size,
                h=size,
                cx=x,
                cy=cy,
                size=float(size),
                score=0.70,
                corner_fill=corner,
                face_fill=face,
                contrast=contrast,
                med_h=med_h,
                med_s=med_s,
                med_v=med_v,
            )
            candidate.color = self._classify(candidate)
            if candidate.color != "gray_white":
                continue
            candidate.is_hostile = candidate.color in self._enabled_hostile
            completed.append(candidate)
        return completed

    def _analyze(self, frame_bgr: np.ndarray) -> list[_Square]:
        image = self._as_bgr(frame_bgr)
        if image is None:
            self.last_boxes = []
            self.last_all_boxes = []
            self.last_grid = None
            return []
        candidates, image_lab, image_hsv = self._extract_candidates(image)
        accepted = self._lock_or_keep_grid(candidates)

        if not accepted:
            # 无网格时只允许一个且形状/对比度都明显强于普通候选的方格。
            if len(candidates) == 1:
                candidate = candidates[0]
                single_contrast_floor = (
                    min(6.0, self._single_contrast_floor)
                    if candidate.med_s < 70
                    else self._single_contrast_floor
                )
                if (
                    candidate.score >= 0.72
                    and candidate.corner_fill >= 0.78
                    and candidate.face_fill >= 0.62
                    and candidate.contrast >= single_contrast_floor
                ):
                    accepted = candidates

        accepted = self._complete_grid_slots(accepted, image_lab, image_hsv)
        accepted.sort(key=lambda c: (c.cy, c.cx))
        if self._grid is not None and len(accepted) >= 2:
            self._grid["rows"] = len(accepted)
        self.last_all_boxes = [candidate.box for candidate in accepted]
        self.last_boxes = [candidate.box for candidate in accepted if candidate.hostile]
        self.last_grid = None
        if self._grid is not None and len(accepted) >= 1:
            self.last_grid = {
                "x": round(float(self._grid["x"]), 2),
                "size": round(float(self._grid["size"]), 2),
                "pitch": round(float(self._grid["pitch"]), 2),
                "rows": int(self._grid["rows"]),
                "phase": round(float(self._grid["phase"]), 2),
            }
        return accepted

    def red_mask(self, frame_bgr: np.ndarray) -> np.ndarray:
        """返回与输入帧同尺寸的 uint8 掩码，内部填充敌对方格。"""
        image = self._as_bgr(frame_bgr)
        if image is None:
            return np.zeros((0, 0), dtype=np.uint8)
        boxes = self.red_boxes(image)
        mask = np.zeros(image.shape[:2], dtype=np.uint8)
        for x, y, w, h in boxes:
            cv2.rectangle(mask, (x, y), (x + w - 1, y + h - 1), 255, -1)
        return mask

    def red_boxes(self, frame_bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
        """返回当前帧经网格/单方形模式确认的敌对颜色方格。"""
        return list(self.last_boxes) if self._analyze(frame_bgr) is not None else []

    def has_red(self, frame_bgr: np.ndarray) -> bool:
        """单帧敌对方格判定，不更新时间序。"""
        return bool(self.red_boxes(frame_bgr))

    def detect(self, frame_bgr: np.ndarray) -> bool:
        """按当前位置/颜色连续确认敌对方格，并在当前帧无敌对框时立即为 False。"""
        current = self._analyze(frame_bgr)
        hostile = [candidate for candidate in current if candidate.hostile]
        if not hostile:
            self._tracks.clear()
            return False

        pitch = float(self._grid["pitch"]) if self._grid is not None else 0.0
        old_tracks = list(self._tracks.values())
        used_old: set[int] = set()
        new_tracks: dict[tuple[int, int, str], _Track] = {}
        confirmed = False

        for candidate in hostile:
            quantum = max(1, int(round(candidate.size * 0.5)))
            key = (
                int(round(candidate.cx / quantum)),
                int(round(candidate.cy / quantum)),
                candidate.color,
            )
            count = 1
            nearest_index = None
            nearest_distance = float("inf")
            tolerance = max(0.62 * candidate.size, 0.50 * pitch)
            for index, track in enumerate(old_tracks):
                if index in used_old or track.color != candidate.color:
                    continue
                distance = max(
                    abs(candidate.cx - track.cx), abs(candidate.cy - track.cy)
                )
                if distance <= tolerance and distance < nearest_distance:
                    nearest_index = index
                    nearest_distance = distance
            if nearest_index is not None:
                used_old.add(nearest_index)
                count = old_tracks[nearest_index].count + 1
            new_tracks[key] = _Track(
                count=count,
                cx=candidate.cx,
                cy=candidate.cy,
                size=candidate.size,
                color=candidate.color,
            )
            if count >= self.confirm_frames:
                confirmed = True

        self._tracks = new_tracks
        return confirmed

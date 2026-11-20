"""无人机状态识别。

直接分析无人机列表原始像素，不做下采样。绿色 / 红色文字簇必须匹配对应字样
模板，并在同一行右侧找到血条，才计为有效无人机行。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from core.logger import get_logger
from core.paths import assets_dir

logger = get_logger(__name__)


@dataclass(frozen=True)
class DroneState:
    idle_count: int
    combat: bool
    damaged: bool
    all_idle: bool
    idle_chime: bool


_EMPTY_STATE = DroneState(0, False, False, False, False)
IDLE_ALERT_DELAY_SECONDS = 3.0

# 0.45 覆盖 0.5–1.5 倍实测下限；0.7 倍末行最低约 0.49，
# 与实心色块（约 0）及随机噪声（≤0.15）仍有充足区分度。
TEXT_MATCH_THRESHOLD = 0.45
TEXT_MIN_HEIGHT = 6
TEXT_ASPECT_MIN, TEXT_ASPECT_MAX = 1.8, 4.2
TEXT_MERGE_GAP = 6
BAR_MAX_HEIGHT = 12
BAR_MIN_WIDTH = 6
BAR_MIN_ASPECT = 2.5
BAR_VERTICAL_TOLERANCE = 0.7

_TEXT_KERNEL = np.ones((2, 1), dtype=np.uint8)
_GREEN_LOWER = np.array([40, 70, 80], dtype=np.uint8)
_GREEN_UPPER = np.array([80, 255, 255], dtype=np.uint8)
_RED_LOWER_1 = np.array([0, 120, 90], dtype=np.uint8)
_RED_UPPER_1 = np.array([10, 255, 255], dtype=np.uint8)
_RED_LOWER_2 = np.array([170, 120, 90], dtype=np.uint8)
_RED_UPPER_2 = np.array([179, 255, 255], dtype=np.uint8)
_WHITE_LOWER = np.array([0, 0, 140], dtype=np.uint8)
_WHITE_UPPER = np.array([179, 60, 255], dtype=np.uint8)


def to_bgr(frame: np.ndarray) -> np.ndarray | None:
    """把检测器支持的灰度 / BGRA / BGR 图像统一为 BGR。"""
    if not isinstance(frame, np.ndarray):
        return None
    if frame.ndim == 2:
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    if frame.ndim == 3 and frame.shape[2] == 4:
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    if frame.ndim == 3 and frame.shape[2] == 3:
        return frame
    return None


def build_color_masks(
    frame_bgr: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回检测器使用的 green / red / white 二值掩码。"""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, _GREEN_LOWER, _GREEN_UPPER)
    red = cv2.bitwise_or(
        cv2.inRange(hsv, _RED_LOWER_1, _RED_UPPER_1),
        cv2.inRange(hsv, _RED_LOWER_2, _RED_UPPER_2),
    )
    white = cv2.inRange(hsv, _WHITE_LOWER, _WHITE_UPPER)
    return green, red, white


def _merge_boxes(
    boxes: list[list[int]],
    *,
    max_gap: int,
) -> list[list[int]]:
    """合并水平间距不超过 ``max_gap`` 且垂直相交的相邻框。"""
    merged = [box[:] for box in boxes]
    changed = True
    while changed:
        changed = False
        index = 0
        while index < len(merged):
            other = index + 1
            while other < len(merged):
                left = merged[index]
                right = merged[other]
                overlap = min(left[3], right[3]) - max(left[1], right[1]) + 1
                gap = max(left[0], right[0]) - min(left[2], right[2]) - 1
                if overlap > 0 and gap <= max_gap:
                    merged[index] = [
                        min(left[0], right[0]),
                        min(left[1], right[1]),
                        max(left[2], right[2]),
                        max(left[3], right[3]),
                    ]
                    merged.pop(other)
                    changed = True
                else:
                    other += 1
            index += 1
    return merged


def _component_boxes(mask: np.ndarray) -> list[list[int]]:
    """返回连通域包含端点的矩形框 ``[x0, y0, x1, y1]``。"""
    try:
        _, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    except Exception:
        return []
    boxes: list[list[int]] = []
    for x, y, width, height, area in stats[1:]:
        if int(area) <= 0:
            continue
        x0, y0 = int(x), int(y)
        boxes.append([x0, y0, x0 + int(width) - 1, y0 + int(height) - 1])
    return boxes


def find_text_clusters(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """提取并过滤绿色 / 红色文字簇，返回 ``(x0, y0, x1, y1)``。"""
    closed = prepare_text_mask(mask)
    boxes = _component_boxes(closed)
    boxes.sort(key=lambda box: (box[1], box[0], box[2], box[3]))
    clusters: list[tuple[int, int, int, int]] = []
    for x0, y0, x1, y1 in _merge_boxes(boxes, max_gap=TEXT_MERGE_GAP):
        width = x1 - x0 + 1
        height = y1 - y0 + 1
        if height < TEXT_MIN_HEIGHT:
            continue
        aspect = width / height
        if TEXT_ASPECT_MIN <= aspect <= TEXT_ASPECT_MAX:
            clusters.append((x0, y0, x1, y1))
    return sorted(clusters, key=lambda box: (box[1], box[0]))


def prepare_text_mask(mask: np.ndarray) -> np.ndarray:
    """执行文字簇使用的 ``1×2`` 纵向闭运算。"""
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _TEXT_KERNEL)


def find_bar_components(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """提取短而宽的血条段，返回 ``(x0, y0, x1, y1)``。"""
    bars: list[tuple[int, int, int, int]] = []
    for x0, y0, x1, y1 in _component_boxes(mask):
        width = x1 - x0 + 1
        height = y1 - y0 + 1
        if (
            height <= BAR_MAX_HEIGHT
            and width >= BAR_MIN_WIDTH
            and width / height >= BAR_MIN_ASPECT
        ):
            bars.append((x0, y0, x1, y1))
    return sorted(bars, key=lambda box: (box[1], box[0]))


def _load_binary_template(path: Path) -> np.ndarray | None:
    """使用支持中文路径的方式读取单通道 0/255 模板。"""
    try:
        encoded = np.fromfile(path, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE)
    except Exception:
        return None
    if image is None or image.size == 0 or image.ndim != 2:
        return None
    if image.shape[0] < TEXT_MIN_HEIGHT or image.shape[1] < 2:
        return None
    return np.where(image > 0, 255, 0).astype(np.uint8)


def _bar_belongs_to_text(
    text: tuple[int, int, int, int],
    bar: tuple[int, int, int, int],
    text_height: int | None = None,
) -> bool:
    """判断血条是否位于文字右侧且垂直中心足够接近。"""
    _text_x0, text_y0, text_x1, text_y1 = text
    bar_x0, bar_y0, _bar_x1, bar_y1 = bar
    height = text_height or (text_y1 - text_y0 + 1)
    text_center = (text_y0 + text_y1) / 2.0
    bar_center = (bar_y0 + bar_y1) / 2.0
    return (
        bar_x0 >= text_x1 + 2
        and abs(bar_center - text_center) <= BAR_VERTICAL_TOLERANCE * height
    )


def _bar_center_inside_text(
    bar: tuple[int, int, int, int],
    text: tuple[int, int, int, int],
) -> bool:
    """判断红色短组件是否只是小尺度文字笔画，而非独立血条。"""
    bar_x0, bar_y0, bar_x1, bar_y1 = bar
    text_x0, text_y0, text_x1, text_y1 = text
    center_x = (bar_x0 + bar_x1) / 2.0
    center_y = (bar_y0 + bar_y1) / 2.0
    return (
        text_x0 - 1 <= center_x <= text_x1 + 1
        and text_y0 - 1 <= center_y <= text_y1 + 1
    )


def _standalone_red_bar_exists(
    red_bars: list[tuple[int, int, int, int]],
    text_clusters: list[tuple[int, int, int, int]],
) -> bool:
    """存在不属于文字笔画区域的红色血条段。"""
    return any(
        not any(_bar_center_inside_text(bar, text) for text in text_clusters)
        for bar in red_bars
    )


class DroneDetector:
    """带时序确认与一次性「叮咚」事件的无人机检测器。"""

    def __init__(
        self,
        count: int = 5,
        confirm_frames: int = 3,
        damage_frames: int = 2,
        idle_delay_seconds: float = IDLE_ALERT_DELAY_SECONDS,
        clock=None,
        templates_dir: str | Path | None = None,
    ):
        try:
            self.count = max(1, min(5, int(count)))
        except (TypeError, ValueError, OverflowError):
            self.count = 5
        try:
            self.confirm_frames = max(1, int(confirm_frames))
        except (TypeError, ValueError, OverflowError):
            self.confirm_frames = 3
        try:
            self.damage_frames = max(1, int(damage_frames))
        except (TypeError, ValueError, OverflowError):
            self.damage_frames = 2
        try:
            self.idle_delay_seconds = max(0.0, float(idle_delay_seconds))
        except (TypeError, ValueError, OverflowError):
            self.idle_delay_seconds = IDLE_ALERT_DELAY_SECONDS
        self._clock = clock or time.monotonic
        self._templates_dir = (
            Path(templates_dir)
            if templates_dir is not None
            else assets_dir() / "assets" / "drone_templates"
        )
        self._templates: dict[str, np.ndarray] = {}
        self._using_templates = False
        self._template_warning_emitted = False
        self._load_templates()
        self.reset()

    def _load_templates(self) -> None:
        templates: dict[str, np.ndarray] = {}
        for key in ("idle", "combat"):
            template = _load_binary_template(self._templates_dir / f"{key}.png")
            if template is not None:
                templates[key] = template
        if len(templates) == 2:
            self._templates = templates
            self._using_templates = True
            return
        self._templates = {}
        self._using_templates = False
        if not self._template_warning_emitted:
            self._template_warning_emitted = True
            logger.warning(
                "无人机文字模板缺失或不可读，降级为颜色行 + 血条确认：%s",
                self._templates_dir,
            )

    def reset(self) -> None:
        """清除连续帧计数、战斗锁存与一次性事件。"""
        self._idle_streak = 0
        self._alarm_streak = 0
        self._combat_seen = False
        self._all_idle_since: float | None = None

    def damage_alarm_active(self) -> bool:
        """「战斗中 + 红血条」是否已达到连续帧确认。"""
        return self._alarm_streak >= self.damage_frames

    def update(self, frame_bgr: np.ndarray | None) -> DroneState:
        """分析一帧并返回纯状态；非法输入安全返回全 False。"""
        analysis = self._analyze(frame_bgr)
        if analysis is None:
            self._idle_streak = 0
            self._alarm_streak = 0
            self._all_idle_since = None
            return _EMPTY_STATE

        idle_count, combat, damaged = analysis
        if idle_count >= self.count:
            self._idle_streak += 1
        else:
            self._idle_streak = 0

        if combat and damaged:
            self._alarm_streak += 1
        else:
            self._alarm_streak = 0

        if combat or damaged:
            self._combat_seen = True

        all_idle = self._idle_streak >= self.confirm_frames
        idle_chime = False
        if not all_idle or combat or damaged:
            self._all_idle_since = None
        else:
            now = self._clock()
            if self._all_idle_since is None:
                self._all_idle_since = now
            if (
                self._combat_seen
                and now - self._all_idle_since >= self.idle_delay_seconds
            ):
                idle_chime = True
                self._combat_seen = False

        return DroneState(
            idle_count=idle_count,
            combat=combat,
            damaged=damaged,
            all_idle=all_idle,
            idle_chime=idle_chime,
        )

    def _match_text(self, crop: np.ndarray, template: np.ndarray) -> float:
        """在簇高度 ``h`` 与 ``h-1`` 两个尺度上做归一化模板匹配。"""
        if crop.size == 0 or crop.ndim != 2:
            return 0.0
        source_height = template.shape[0]
        source_width = template.shape[1]
        best = -1.0
        for target_height in (crop.shape[0], crop.shape[0] - 1):
            if target_height < 2:
                continue
            target_width = max(
                1, int(round(source_width * target_height / source_height))
            )
            if target_height > crop.shape[0] or target_width > crop.shape[1]:
                continue
            resized = cv2.resize(
                template,
                (target_width, target_height),
                interpolation=cv2.INTER_AREA,
            )
            sample_blur = cv2.GaussianBlur(crop, (3, 3), 0)
            template_blur = cv2.GaussianBlur(resized, (3, 3), 0)
            score = cv2.matchTemplate(
                sample_blur, template_blur, cv2.TM_CCOEFF_NORMED
            )
            if score.size:
                best = max(best, float(np.max(score)))
        return max(0.0, best) if best > -1.0 else 0.0

    def _analyze(self, frame_bgr: np.ndarray | None) -> tuple[int, bool, bool] | None:
        """返回 ``(空闲行数, 战斗中, 红色血条)``；无效帧返回 None。"""
        if not isinstance(frame_bgr, np.ndarray) or frame_bgr.size == 0:
            return None
        if frame_bgr.ndim not in (2, 3):
            return None
        height, width = frame_bgr.shape[:2]
        if height < 3 or width < 3:
            return None
        try:
            bgr = to_bgr(frame_bgr)
            if bgr is None:
                return None
            green, red, white = build_color_masks(bgr)
            white_bars = find_bar_components(white)
            red_bars = find_bar_components(red)
            if not self._using_templates:
                return self._analyze_fallback(green, red, white_bars, red_bars)

            idle_count = 0
            for x0, y0, x1, y1 in find_text_clusters(green):
                crop = green[y0 : y1 + 1, x0 : x1 + 1]
                score = self._match_text(crop, self._templates["idle"])
                if score < TEXT_MATCH_THRESHOLD:
                    continue
                text = (x0, y0, x1, y1)
                if any(
                    _bar_belongs_to_text(text, bar, y1 - y0 + 1)
                    for bar in white_bars
                ):
                    idle_count += 1

            combat = False
            damaged = False
            valid_combat = False
            red_clusters = find_text_clusters(red)
            for x0, y0, x1, y1 in red_clusters:
                crop = red[y0 : y1 + 1, x0 : x1 + 1]
                score = self._match_text(crop, self._templates["combat"])
                if score < TEXT_MATCH_THRESHOLD:
                    continue
                text = (x0, y0, x1, y1)
                text_height = y1 - y0 + 1
                has_white_bar = any(
                    _bar_belongs_to_text(text, bar, text_height)
                    for bar in white_bars
                )
                has_red_bar = any(
                    _bar_belongs_to_text(text, bar, text_height)
                    for bar in red_bars
                )
                if has_white_bar or has_red_bar:
                    combat = True
                    valid_combat = True
                if has_red_bar:
                    damaged = True
            if not valid_combat:
                # 无战斗字样时保留纯红血条告警状态，但排除小尺度文字自身
                # 被宽高比规则误判成的“血条”。
                damaged = _standalone_red_bar_exists(red_bars, red_clusters)
            return idle_count, combat, damaged
        except Exception:
            logger.debug("无人机帧分析失败", exc_info=True)
            return None

    @staticmethod
    def _analyze_fallback(
        green: np.ndarray,
        red: np.ndarray,
        white_bars: list[tuple[int, int, int, int]],
        red_bars: list[tuple[int, int, int, int]],
    ) -> tuple[int, bool, bool]:
        """模板不可用时，以颜色行 + 右侧血条作为兼容降级路径。"""
        idle_count = 0
        row_counts = np.count_nonzero(green, axis=1)
        present_rows = np.flatnonzero(row_counts >= 8)
        groups: list[tuple[int, int]] = []
        for row in present_rows:
            current = int(row)
            if not groups or current - groups[-1][1] > 3:
                groups.append((current, current))
            else:
                groups[-1] = (groups[-1][0], current)
        for y0, y1 in groups:
            columns = np.flatnonzero(np.any(green[y0 : y1 + 1] > 0, axis=0))
            if columns.size == 0:
                continue
            text = (int(columns[0]), y0, int(columns[-1]), y1)
            if any(
                _bar_belongs_to_text(text, bar, y1 - y0 + 1)
                for bar in white_bars
            ):
                idle_count += 1

        combat = False
        damaged = False
        valid_combat = False
        red_clusters = find_text_clusters(red)
        for cluster in red_clusters:
            _x0, y0, _x1, y1 = cluster
            text_height = y1 - y0 + 1
            if text_height < 9:
                continue
            has_white_bar = any(
                _bar_belongs_to_text(cluster, bar, text_height)
                for bar in white_bars
            )
            has_red_bar = any(
                _bar_belongs_to_text(cluster, bar, text_height)
                for bar in red_bars
            )
            if has_white_bar or has_red_bar:
                combat = True
                valid_combat = True
            if has_red_bar:
                damaged = True
        if not valid_combat:
            damaged = _standalone_red_bar_exists(red_bars, red_clusters)
        return idle_count, combat, damaged

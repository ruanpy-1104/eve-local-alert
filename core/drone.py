"""无人机状态识别。

直接分析无人机列表原始像素，不做下采样。绿色行用于统计「空闲」，
红色连通域区分「战斗中」文字/图标与低矮的血条段。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class DroneState:
    idle_count: int
    combat: bool
    damaged: bool
    all_idle: bool
    idle_chime: bool


_EMPTY_STATE = DroneState(0, False, False, False, False)
IDLE_ALERT_DELAY_SECONDS = 3.0


class DroneDetector:
    """带时序确认与一次性「叮咚」事件的无人机检测器。"""

    def __init__(
        self,
        count: int = 5,
        confirm_frames: int = 3,
        damage_frames: int = 2,
        idle_delay_seconds: float = IDLE_ALERT_DELAY_SECONDS,
        clock=None,
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
        self.reset()

    def reset(self) -> None:
        """清除连续帧计数、战斗锁存与一次性事件。"""
        self._idle_streak = 0
        self._damage_streak = 0
        self._combat_seen = False
        self._all_idle_since: float | None = None

    def damage_confirmed(self) -> bool:
        """当前红色血条是否已达到连续帧确认；供报警边沿使用。"""
        return self._damage_streak >= self.damage_frames

    def update(self, frame_bgr: np.ndarray | None) -> DroneState:
        """分析一帧并返回纯状态；非法输入安静返回全 False。"""
        analysis = self._analyze(frame_bgr)
        if analysis is None:
            self._idle_streak = 0
            self._damage_streak = 0
            self._all_idle_since = None
            return _EMPTY_STATE

        idle_count, combat, damaged = analysis
        if idle_count >= self.count:
            self._idle_streak += 1
        else:
            # 空闲行数一旦掉下去，立即退出「全部空闲」。
            self._idle_streak = 0

        if damaged:
            self._damage_streak += 1
        else:
            self._damage_streak = 0

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
                # 一次性事件：发出后清除战斗记录，持续空闲不会重复响。
                self._combat_seen = False

        return DroneState(
            idle_count=idle_count,
            combat=combat,
            damaged=damaged,
            all_idle=all_idle,
            idle_chime=idle_chime,
        )

    @staticmethod
    def _analyze(frame_bgr: np.ndarray | None) -> tuple[int, bool, bool] | None:
        """返回 (空闲行数, 战斗中迹象, 红色血条段)；无效帧返回 None。"""
        if not isinstance(frame_bgr, np.ndarray) or frame_bgr.size == 0:
            return None
        if frame_bgr.ndim not in (2, 3):
            return None
        height, width = frame_bgr.shape[:2]
        if height < 3 or width < 3:
            return None
        try:
            if frame_bgr.ndim == 2:
                bgr = cv2.cvtColor(frame_bgr, cv2.COLOR_GRAY2BGR)
            elif frame_bgr.shape[2] == 4:
                bgr = cv2.cvtColor(frame_bgr, cv2.COLOR_BGRA2BGR)
            elif frame_bgr.shape[2] == 3:
                bgr = frame_bgr
            else:
                return None

            hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
            green = cv2.inRange(
                hsv,
                np.array([40, 70, 80], dtype=np.uint8),
                np.array([80, 255, 255], dtype=np.uint8),
            )
            red = cv2.bitwise_or(
                cv2.inRange(
                    hsv,
                    np.array([0, 120, 90], dtype=np.uint8),
                    np.array([10, 255, 255], dtype=np.uint8),
                ),
                cv2.inRange(
                    hsv,
                    np.array([170, 120, 90], dtype=np.uint8),
                    np.array([179, 255, 255], dtype=np.uint8),
                ),
            )
        except Exception:
            return None

        row_counts = np.count_nonzero(green, axis=1)
        present_rows = np.flatnonzero(row_counts >= 8)
        idle_count = 0
        previous = -100
        for row in present_rows:
            current = int(row)
            # 两个缺失扫描行以内视为同一段文字。
            if idle_count == 0 or current - previous > 3:
                idle_count += 1
            previous = current

        combat = False
        damaged = False
        try:
            _, _, stats, _ = cv2.connectedComponentsWithStats(red, 8)
        except Exception:
            stats = np.empty((0, 5), dtype=np.int32)
        for _left, _top, component_w, component_h, area in stats[1:]:
            width_i = int(component_w)
            height_i = int(component_h)
            area_i = int(area)
            if height_i >= 9 and width_i >= 6 and area_i >= 30:
                combat = True
            if (
                height_i <= 8
                and width_i >= 4
                and width_i / max(height_i, 1) >= 2
                and area_i >= 6
            ):
                damaged = True
        return idle_count, combat, damaged

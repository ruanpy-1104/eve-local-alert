"""固定列/行距网格、友军建网、滚动与 fail-safe 测试。"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from core.detector import Detector

BG = (18, 20, 24)
RED = (0, 0, 220)
WHITE = (255, 255, 255)
BLUE = (220, 0, 0)
GREEN = (0, 180, 0)
PURPLE = (190, 0, 190)


def _frame(width: int = 300, height: int = 260, bg=BG) -> np.ndarray:
    return np.full((height, width, 3), bg, dtype=np.uint8)


def _square(frame, color, x, y, size, center="blank"):
    cv2.rectangle(frame, (x, y), (x + size - 1, y + size - 1), color, -1)
    cx, cy = x + size // 2, y + size // 2
    if center == "star":
        points = np.array(
            [
                (cx, y + 2),
                (cx + size // 6, cy),
                (x + size - 2, cy),
                (cx + size // 8, cy + size // 6),
                (x + size - 3, y + size - 3),
                (cx, cy + size // 7),
                (x + 2, y + size - 3),
                (cx - size // 8, cy + size // 6),
                (x + 2, cy),
                (cx - size // 6, cy),
            ],
            dtype=np.int32,
        )
        cv2.fillPoly(frame, [points], WHITE)
    elif center == "cross":
        cv2.line(frame, (x + size // 4, cy), (x + size - size // 4, cy), WHITE, max(1, size // 8))
        cv2.line(frame, (cx, y + size // 4), (cx, y + size - size // 4), WHITE, max(1, size // 8))
    elif center == "bar":
        cv2.rectangle(frame, (x + size // 4, cy - 1), (x + size - size // 4, cy + 1), WHITE, -1)
    elif center == "dot":
        cv2.circle(frame, (cx, cy), max(1, size // 7), WHITE, -1)
    return frame


def _grid(
    colors,
    size=15,
    pitch=None,
    rows=4,
    x=24,
    y=20,
    centers=None,
    width=300,
    height=280,
):
    pitch = int(round(size * 1.4)) if pitch is None else pitch
    is_one_color = (
        not isinstance(colors, (list, tuple))
        or (len(colors) == 3 and all(isinstance(value, (int, np.integer)) for value in colors))
    )
    if is_one_color:
        colors = [colors] * rows
    else:
        colors = list(colors)
    centers = ["blank"] * len(colors) if centers is None else centers
    frame = _frame(width=width, height=height)
    for index, (color, center) in enumerate(zip(colors, centers)):
        _square(frame, color, x, y + index * pitch, size, center)
    return frame


class TestGridLock:
    @pytest.mark.parametrize("size", [6, 10, 15, 21, 36])
    @pytest.mark.parametrize("ratio", [1.3, 1.4, 1.5])
    def test_scale_and_pitch_adaptive_grid(self, size, ratio):
        pitch = int(round(size * ratio))
        detector = Detector()
        assert detector.has_red(_grid(RED, size=size, pitch=pitch))
        assert detector.last_grid is not None
        assert abs(detector.last_grid["pitch"] - pitch) <= 1
        assert abs(detector.last_grid["size"] - size) <= 3

    @pytest.mark.parametrize(
        ("color", "hostile"),
        [
            (RED, True),
            ((0, 89, 255), True),
            ((0, 178, 255), True),
            ((178, 178, 178), True),
            (BLUE, False),
            (GREEN, False),
            (PURPLE, False),
        ],
    )
    def test_all_palette_roles_build_grid(self, color, hostile):
        detector = Detector()
        assert detector.has_red(_grid(color, rows=4)) is hostile
        assert detector.last_grid is not None
        assert len(detector.last_all_boxes) >= 2

    @pytest.mark.parametrize("center", ["blank", "star", "cross", "bar", "dot"])
    def test_center_graphics_do_not_break_grid(self, center):
        detector = Detector()
        frame = _grid(RED, rows=4, centers=[center] * 4)
        assert detector.has_red(frame)
        assert detector.last_grid["rows"] >= 2

    def test_mixed_center_graphics_all_accepted(self):
        detector = Detector()
        frame = _grid(RED, rows=5, centers=["star", "cross", "bar", "dot", "blank"])
        assert detector.has_red(frame)
        assert len(detector.last_all_boxes) == 5

    def test_grid_requires_two_rows(self):
        frame = _grid(RED, rows=1)
        detector = Detector()
        assert detector.has_red(frame)  # 单方形模式仍可识别
        assert detector.last_grid is None

    def test_two_off_column_squares_do_not_form_grid(self):
        frame = _frame()
        _square(frame, RED, 30, 30, 15, "dot")
        _square(frame, RED, 190, 150, 15, "dot")
        detector = Detector()
        assert not detector.has_red(frame)
        assert detector.last_grid is None

    def test_one_hostile_square_uses_single_square_mode(self):
        detector = Detector()
        assert detector.has_red(_grid(RED, rows=1))
        assert detector.last_grid is None

    @pytest.mark.parametrize("size", [8, 40])
    def test_single_square_size_edges(self, size):
        detector = Detector()
        assert detector.has_red(_grid(RED, size=size, pitch=size * 2, rows=1, width=220, height=180))

    def test_grid_selects_most_populated_column(self):
        frame = _grid(BLUE, rows=6, x=24)
        _square(frame, RED, 190, 40, 15, "dot")
        _square(frame, RED, 190, 62, 15, "dot")
        detector = Detector()
        assert not detector.has_red(frame)
        assert detector.last_grid is not None
        assert detector.last_grid["x"] < 60

    def test_grid_discards_background_outlier(self):
        frame = _grid(BLUE, rows=5, x=24)
        _square(frame, RED, 180, 40, 18, "dot")
        detector = Detector()
        assert not detector.has_red(frame)
        assert not detector.last_boxes

    def test_grid_discards_off_phase_hostile(self):
        frame = _grid(BLUE, rows=5, x=24)
        _square(frame, RED, 24, 50, 15, "dot")  # 位于两行之间
        detector = Detector()
        assert not detector.has_red(frame)
        assert not detector.last_boxes

    def test_friend_grid_and_background_red_block_is_safe(self):
        frame = _grid([BLUE, PURPLE, BLUE, PURPLE, BLUE], rows=5, x=20)
        cv2.rectangle(frame, (180, 45), (205, 70), RED, -1)
        detector = Detector()
        for _ in range(3):
            assert not detector.detect(frame)
        assert not detector.last_boxes
        assert detector.last_grid is not None

    def test_empty_slot_between_hostiles_is_not_reported(self):
        frame = _grid([RED, BLUE, RED], rows=3, x=24, pitch=24)
        cv2.rectangle(frame, (24, 44), (38, 58), BG, -1)
        detector = Detector()
        assert detector.has_red(frame)
        assert len(detector.last_boxes) == 2
        assert all(box[1] != 44 for box in detector.last_boxes)

    def test_middle_empty_slot_does_not_double_estimated_pitch(self):
        frame = _frame(width=300, height=150, bg=(205, 205, 205))
        for y in (15, 36, 78):
            _square(frame, RED, 24, y, 15, "star")
        detector = Detector()
        assert detector.has_red(frame)
        assert detector.last_grid is not None
        assert detector.last_grid["pitch"] == pytest.approx(21, abs=0.5)
        assert detector.last_grid["rows"] == 3
        assert len(detector.last_all_boxes) == 3
        assert len(detector.last_boxes) == 3

    def test_grid_completion_stays_between_first_and_last_friend_rows(self):
        frame = _frame(width=300, height=170, bg=(205, 205, 205))
        for index in (0, 1, 3, 4):
            _square(frame, PURPLE, 24, 15 + index * 21, 15, "star")
        detector = Detector()
        assert not detector.has_red(frame)
        assert detector.last_grid is not None
        assert detector.last_grid["pitch"] == pytest.approx(21, abs=0.5)
        assert detector.last_grid["rows"] == 4
        assert len(detector.last_all_boxes) == 4
        assert not detector.last_boxes

    def test_name_text_in_empty_friend_slot_is_not_filled_as_white(self):
        frame = _frame(width=300, height=170, bg=(205, 205, 205))
        for index in (0, 1, 3, 4):
            _square(frame, PURPLE, 24, 15 + index * 21, 15, "star")
        cv2.rectangle(frame, (48, 59), (118, 70), WHITE, -1)
        cv2.rectangle(frame, (48, 84), (112, 95), WHITE, 2)
        detector = Detector()
        assert not detector.has_red(frame)
        assert detector.last_grid is not None
        assert len(detector.last_all_boxes) == 4
        assert not detector.last_boxes

    def test_white_slot_uses_bright_emblem_and_square_edges(self):
        frame = _frame(width=180, height=140, bg=(170, 170, 170))
        for index in (0, 1, 3, 4):
            _square(frame, BLUE, 24, 15 + index * 22, 15, "star")
        _square(frame, (179, 179, 179), 24, 59, 15, "star")
        detector = Detector()
        assert detector.has_red(frame)
        assert detector.last_grid is not None
        assert detector.last_grid["pitch"] == pytest.approx(22, abs=0.5)
        assert len(detector.last_boxes) == 1
        assert detector.last_boxes[0][0] == 24
        assert detector.last_boxes[0][1] == pytest.approx(59, abs=1)

    def test_empty_slot_with_bright_background_star_is_not_filled(self):
        frame = _frame(width=180, height=140, bg=(170, 170, 170))
        for index in (0, 1, 3, 4):
            _square(frame, BLUE, 24, 15 + index * 22, 15, "star")
        cv2.circle(frame, (31, 66), 2, WHITE, -1)
        detector = Detector()
        assert not detector.has_red(frame)
        assert detector.last_grid is not None
        assert not detector.last_boxes

    def test_friendly_rows_only_are_not_hostile_boxes(self):
        frame = _grid([BLUE, GREEN, PURPLE, BLUE], rows=4)
        detector = Detector()
        assert not detector.has_red(frame)
        assert detector.last_all_boxes
        assert detector.last_boxes == []

    def test_grid_outside_circle_rejected(self):
        frame = _grid(BLUE, rows=4)
        cv2.circle(frame, (220, 180), 22, RED, -1)
        detector = Detector()
        assert not detector.has_red(frame)


class TestScrollingGrid:
    @pytest.mark.parametrize("shift", [3, 4, 5, 6, 7])
    def test_rolling_rows_confirm_after_three_frames(self, shift):
        detector = Detector(confirm_frames=3)
        frame0 = _grid(RED, rows=4, y=20)
        frame1 = _grid(RED, rows=4, y=20 + shift)
        frame2 = _grid(RED, rows=4, y=20 + 2 * shift)
        assert not detector.detect(frame0)
        assert not detector.detect(frame1)
        assert detector.detect(frame2)

    def test_single_frame_scroll_keeps_grid(self):
        detector = Detector()
        assert detector.has_red(_grid(RED, rows=4, y=20))
        old_phase = detector.last_grid["phase"]
        assert detector.has_red(_grid(RED, rows=4, y=27))
        assert detector.last_grid is not None
        assert detector.last_grid["phase"] != old_phase

    def test_grid_relocks_after_loss_window(self):
        detector = Detector(confirm_frames=2)
        first = _grid(BLUE, rows=4, x=20)
        detector.has_red(first)
        for _ in range(2):
            detector.has_red(_frame(height=180))
        second = _grid(RED, rows=4, x=80)
        assert detector.has_red(second)
        assert detector.last_grid is not None


class TestGridGeometryAndSafety:
    @pytest.mark.parametrize("radius", [8, 14, 20])
    def test_circular_light_does_not_enter_grid(self, radius):
        frame = _frame()
        cv2.circle(frame, (150, 120), radius, RED, -1)
        assert not Detector().has_red(frame)

    def test_orange_planet_spot_does_not_enter_grid(self):
        frame = _frame(width=400, height=300)
        cv2.circle(frame, (250, 170), 55, (0, 80, 230), -1)
        cv2.circle(frame, (235, 155), 18, (0, 150, 255), -1)
        assert not Detector().has_red(frame)

    def test_wide_horizontal_bar_is_not_square(self):
        frame = _frame()
        frame[120:140, 30:270] = RED
        assert not Detector().has_red(frame)

    @pytest.mark.parametrize("thickness", [1, 2, 3])
    def test_hollow_slots_are_not_grid_squares(self, thickness):
        frame = _frame()
        for index in range(4):
            y = 20 + index * 24
            cv2.rectangle(frame, (30, y), (45, y + 15), RED, thickness)
        assert not Detector().has_red(frame)

    def test_hollow_text_like_rows_are_rejected(self):
        frame = _frame()
        for index in range(5):
            x = 50 + index * 28
            cv2.rectangle(frame, (x, 100), (x + 15, 115), WHITE, 2)
        assert not Detector(colors=["gray_white"]).has_red(frame)

    def test_dimensions_outside_contract_are_rejected(self):
        frame = _frame()
        cv2.rectangle(frame, (50, 50), (52, 52), RED, -1)
        cv2.rectangle(frame, (100, 80), (159, 139), RED, -1)
        assert not Detector().has_red(frame)

    def test_no_candidates_is_false(self):
        assert not Detector().has_red(_frame())

    def test_last_grid_has_required_keys(self):
        detector = Detector()
        detector.has_red(_grid(RED, rows=4))
        assert detector.last_grid is not None
        assert set(detector.last_grid) == {"x", "size", "pitch", "rows", "phase"}

    def test_downscale_is_noop_for_grid(self):
        frame = _grid(RED, rows=4)
        a, b = Detector(downscale=1), Detector(downscale=8)
        assert a.has_red(frame) == b.has_red(frame)
        assert a.last_all_boxes == b.last_all_boxes
        assert a.last_grid == b.last_grid

    def test_grid_color_change_still_confirms_new_color(self):
        detector = Detector(confirm_frames=3)
        red, orange = _grid(RED, rows=4), _grid((0, 89, 255), rows=4)
        detector.detect(red)
        detector.detect(red)
        assert not detector.detect(orange)
        assert not detector.detect(orange)
        assert detector.detect(orange)

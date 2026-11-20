"""方格、颜色分类、单方形模式与时序确认测试。"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from core.colors import DEFAULT_ALERT_COLORS, EVE_COLORS, color_ranges
from core.detector import Detector

BG = (18, 20, 24)
RED = (0, 0, 220)
ORANGE = (0, 89, 255)
YELLOW = (0, 178, 255)
GRAY = (129, 129, 129)
PALETTE_GRAY = (178, 178, 178)
WHITE = (255, 255, 255)
BLUE = (220, 0, 0)
GREEN = (0, 180, 0)
PURPLE = (190, 0, 190)


def _frame(width: int = 240, height: int = 200, bg=BG) -> np.ndarray:
    return np.full((height, width, 3), bg, dtype=np.uint8)


def _square(
    frame: np.ndarray,
    color,
    x: int,
    y: int,
    size: int,
    center: str = "blank",
) -> np.ndarray:
    cv2.rectangle(frame, (x, y), (x + size - 1, y + size - 1), color, -1)
    cx, cy = x + size / 2.0, y + size / 2.0
    inner = max(1, int(round(size * 0.34)))
    accent = WHITE
    if center == "star":
        points = []
        for index in range(10):
            radius = inner if index % 2 == 0 else inner * 0.45
            angle = -np.pi / 2 + index * np.pi / 5
            points.append((int(round(cx + radius * np.cos(angle))), int(round(cy + radius * np.sin(angle)))))
        cv2.fillPoly(frame, [np.array(points, dtype=np.int32)], accent)
    elif center == "cross":
        arm = max(1, inner // 2)
        cv2.line(frame, (int(cx - inner), int(cy)), (int(cx + inner), int(cy)), accent, arm)
        cv2.line(frame, (int(cx), int(cy - inner)), (int(cx), int(cy + inner)), accent, arm)
    elif center == "bar":
        cv2.rectangle(
            frame,
            (x + max(1, size // 5), y + size // 2 - max(1, size // 12)),
            (x + size - max(2, size // 5), y + size // 2 + max(1, size // 12)),
            accent,
            -1,
        )
    elif center == "dot":
        cv2.circle(frame, (int(cx), int(cy)), max(1, size // 7), accent, -1)
    return frame


def _one(color=RED, size: int = 15, center: str = "dot", **frame_kw) -> np.ndarray:
    frame = _frame(**frame_kw)
    return _square(frame, color, 100, 90, size, center)


def _column(
    color,
    size: int = 15,
    pitch: int | None = None,
    rows: int = 3,
    centers=None,
    x: int = 20,
    y: int = 20,
) -> np.ndarray:
    pitch = int(round(size * 1.4)) if pitch is None else pitch
    centers = ["blank"] * rows if centers is None else centers
    frame = _frame(width=max(260, x + size + 30), height=max(200, y + pitch * rows + size))
    for index, center in enumerate(centers):
        _square(frame, color, x, y + index * pitch, size, center)
    return frame


class TestColorClassification:
    @pytest.mark.parametrize(
        ("name", "color"),
        [
            ("red", RED),
            ("orange", ORANGE),
            ("yellow", YELLOW),
            ("gray_white", GRAY),
        ],
    )
    def test_each_default_hostile_color(self, name, color):
        assert Detector(colors=[name]).has_red(_one(color))

    @pytest.mark.parametrize("color", [BLUE, GREEN, PURPLE])
    def test_friend_colors_never_alert(self, color):
        detector = Detector()
        assert not detector.has_red(_one(color))
        assert detector.last_all_boxes
        assert detector.last_boxes == []

    def test_default_colors_are_the_four_presets(self):
        assert DEFAULT_ALERT_COLORS == ["red", "orange", "yellow", "gray_white"]

    @pytest.mark.parametrize("name", ["red", "orange", "yellow", "gray_white"])
    def test_presets_still_have_masks(self, name):
        assert color_ranges(name, 0.5)

    def test_each_palette_swatch_is_accepted(self):
        for name in ("red", "orange", "yellow", "gray_white"):
            red, green, blue = EVE_COLORS[name]["rgb"]
            color = (blue, green, red) if name != "red" else RED
            assert Detector(colors=[name]).has_red(_one(color))

    def test_colors_string_is_singleton(self):
        detector = Detector(colors="orange")
        assert detector.colors == ("orange",)
        assert detector.has_red(_one(ORANGE))
        assert not detector.has_red(_one(YELLOW))

    def test_multiple_enabled_colors_union(self):
        detector = Detector(colors=["red", "yellow"])
        assert detector.has_red(_one(RED))
        assert detector.has_red(_one(YELLOW))
        assert not detector.has_red(_one(ORANGE))

    def test_unknown_color_is_ignored(self):
        detector = Detector(colors=["red", "not_a_color"])
        assert detector.has_red(_one(RED))
        assert not detector.has_red(_one(BLUE))

    @pytest.mark.parametrize(("name", "color"), [("green", GREEN), ("blue", BLUE), ("black", (0, 0, 0))])
    def test_removed_config_color_does_not_alert(self, name, color):
        assert not Detector(colors=[name]).has_red(_one(color))

    def test_white_alias_maps_to_gray_white(self):
        assert Detector(colors=["white"]).has_red(_one(GRAY))
        assert Detector(colors=["white"]).has_red(_one(PALETTE_GRAY))

    def test_hue_windows_remain_disjoint(self):
        assert not Detector(colors=["orange"]).has_red(_one(YELLOW))
        assert not Detector(colors=["yellow"]).has_red(_one(ORANGE))
        assert not Detector(colors=["red"]).has_red(_one(ORANGE))

    def test_dark_gray_rejected(self):
        assert not Detector(colors=["gray_white"]).has_red(_one((70, 70, 70)))

    def test_black_is_not_a_default_target(self):
        assert not Detector().has_red(_one((0, 0, 0)))

    @pytest.mark.parametrize("compat", ["low_contrast", "auto", "strict"])
    def test_mid_gray_neutral_is_stable(self, compat):
        assert Detector(colors=["gray_white"], compat=compat).has_red(_one(GRAY))

    def test_strictness_clamped(self):
        assert Detector(strictness=999).strictness == 100
        assert Detector(strictness=-5).strictness == 0


class TestWhiteOnBrightBackground:
    @pytest.mark.parametrize("center", ["dot", "star"])
    def test_single_low_contrast_white_square_on_v195(self, center):
        frame = _frame(width=180, height=120, bg=(195, 195, 195))
        _square(frame, (185, 185, 185), 60, 45, 15, center)
        detector = Detector()
        assert detector.has_red(frame)
        assert detector.last_boxes == [(60, 45, 15, 15)]
        assert detector.last_grid is None

    def test_single_low_contrast_white_square_on_v205_remains_detected(self):
        frame = _frame(width=180, height=120, bg=(205, 205, 205))
        _square(frame, (185, 185, 185), 60, 45, 15, "star")
        detector = Detector()
        assert detector.has_red(frame)
        assert len(detector.last_boxes) == 1
        assert detector.last_grid is None

    def test_single_square_threshold_does_not_accept_empty_gradient(self):
        gradient = np.tile(
            np.linspace(190, 200, 180, dtype=np.uint8),
            (120, 1),
        )
        gradient = np.dstack((gradient, gradient, gradient))
        detector = Detector()
        assert not detector.has_red(gradient)
        assert detector.last_boxes == []
        assert detector.last_all_boxes == []

    def test_colored_single_square_threshold_is_unchanged(self):
        assert Detector().has_red(_one(RED, size=15, center="star"))
        assert not Detector().has_red(_one(BLUE, size=15, center="star"))

    def test_low_contrast_white_square_uses_darkness_not_absolute_brightness(self):
        frame = _frame(width=220, height=160, bg=(205, 205, 205))
        _square(frame, (185, 185, 185), 80, 60, 15, "dot")
        detector = Detector()
        assert detector.has_red(frame)
        assert detector.red_boxes(frame)

    def test_white_emblem_does_not_change_face_classification(self):
        frame = _frame(width=220, height=160, bg=(205, 205, 205))
        _square(frame, (185, 185, 185), 80, 60, 21, "star")
        assert Detector().has_red(frame)

    def test_pure_white_on_dark_background_is_allowed_as_white(self):
        frame = _frame()
        _square(frame, WHITE, 100, 80, 15, "blank")
        assert Detector(colors=["gray_white"]).has_red(frame)

    def test_four_low_contrast_white_squares_form_hostile_grid(self):
        frame = _frame(width=180, height=140, bg=(205, 205, 205))
        for index in range(4):
            _square(frame, (185, 185, 185), 24, 18 + index * 21, 15, "star")
        detector = Detector()
        assert detector.has_red(frame)
        assert len(detector.last_boxes) == 4

    def test_bright_background_gradient_has_no_square(self):
        gradient = np.tile(
            np.linspace(195, 205, 121, dtype=np.uint8),
            (315, 1),
        )
        gradient = np.dstack((gradient, gradient, gradient))
        detector = Detector()
        assert not detector.has_red(gradient)
        assert detector.last_boxes == []
        assert detector.last_all_boxes == []

    def test_white_name_text_does_not_become_a_square(self):
        frame = _frame(width=180, height=140, bg=(205, 205, 205))
        for index in range(4):
            y = 18 + index * 21
            _square(frame, (185, 185, 185), 24, y, 15, "star")
        cv2.rectangle(frame, (46, 20), (110, 31), WHITE, -1)
        cv2.rectangle(frame, (46, 43), (112, 54), WHITE, 2)
        detector = Detector()
        assert detector.has_red(frame)
        assert len(detector.last_boxes) == 4
        assert len(detector.last_all_boxes) == 4


class TestSquareGeometry:
    @pytest.mark.parametrize("size", [6, 8, 10, 15, 21, 36, 40])
    def test_single_square_scale_adaptive(self, size):
        detector = Detector()
        assert detector.has_red(_one(RED, size=size))
        assert detector.last_boxes

    @pytest.mark.parametrize("size,pitch", [(6, 8), (10, 13), (15, 21), (21, 28), (36, 48)])
    def test_size_adaptive_grid(self, size, pitch):
        frame = _column(RED, size=size, pitch=pitch, rows=3)
        detector = Detector()
        assert detector.has_red(frame)
        assert detector.last_grid is not None

    @pytest.mark.parametrize("center", ["blank", "star", "cross", "bar", "dot"])
    def test_center_graphic_is_irrelevant(self, center):
        assert Detector().has_red(_one(RED, size=15, center=center))

    def test_name_is_irrelevant(self):
        without_name = _column(RED, rows=4)
        with_name = without_name.copy()
        for y in (20, 41, 62, 83):
            cv2.rectangle(with_name, (42, y), (180, y + 12), WHITE, -1)
        a = Detector()
        b = Detector()
        assert a.has_red(without_name)
        assert b.has_red(with_name)
        assert a.last_boxes == b.last_boxes
        assert a.last_grid is not None and b.last_grid is not None

    def test_wide_bar_rejected(self):
        frame = _frame()
        frame[90:111, 40:210] = RED
        assert not Detector().has_red(frame)

    @pytest.mark.parametrize("radius", [7, 12, 18])
    def test_round_light_rejected(self, radius):
        frame = _frame()
        cv2.circle(frame, (120, 100), radius, RED, -1)
        assert not Detector().has_red(frame)

    @pytest.mark.parametrize("thickness", [1, 2, 4])
    def test_hollow_square_rejected(self, thickness):
        frame = _frame()
        cv2.rectangle(frame, (90, 80), (111, 101), RED, thickness)
        assert not Detector().has_red(frame)

    def test_hollow_glyph_rows_rejected(self):
        frame = _frame(width=260, height=220)
        for index in range(4):
            y = 30 + index * 36
            cv2.rectangle(frame, (40, y), (55, y + 15), WHITE, 2)
        assert not Detector(colors=["gray_white"]).has_red(frame)

    def test_two_noncollinear_squares_fail_safe(self):
        frame = _frame(width=260, height=220)
        _square(frame, RED, 30, 30, 15, "dot")
        _square(frame, RED, 160, 140, 15, "dot")
        assert not Detector().has_red(frame)

    def test_empty_frame_is_false(self):
        assert not Detector().has_red(_frame())
        assert not Detector().detect(None)

    def test_too_dark_colored_square_is_rejected(self):
        assert not Detector(compat="strict").has_red(_one((0, 0, 30)))

    def test_loose_vs_strict_borderline_red(self):
        frame = _one((0, 0, 52))
        assert Detector(compat="low_contrast").has_red(frame)
        assert not Detector(compat="strict").has_red(frame)

    def test_shape_rules_do_not_change_with_compat(self):
        low = Detector(compat="low_contrast")
        wide_bar = _frame()
        wide_bar[90:111, 40:210] = RED
        circle = _frame()
        cv2.circle(circle, (120, 100), 12, RED, -1)
        assert not low.has_red(wide_bar)
        assert not low.has_red(circle)
        assert Detector(compat="strict").has_red(_one(RED))


class TestTemporalConfirmation:
    def test_third_stable_frame_triggers(self):
        detector = Detector(confirm_frames=3)
        frame = _column(RED)
        assert not detector.detect(frame)
        assert not detector.detect(frame)
        assert detector.detect(frame)

    def test_missing_target_frame_clears_counter(self):
        detector = Detector(confirm_frames=3)
        red, empty = _column(RED), _frame()
        assert not detector.detect(red)
        assert not detector.detect(empty)
        assert not detector.detect(red)
        assert not detector.detect(red)
        assert detector.detect(red)

    def test_friend_only_frame_clears_counter(self):
        detector = Detector(confirm_frames=3)
        red, blue = _column(RED), _column(BLUE)
        detector.detect(red)
        detector.detect(red)
        assert not detector.detect(blue)
        assert not detector.detect(red)

    def test_color_change_restarts_counter(self):
        detector = Detector(confirm_frames=3)
        red, orange = _column(RED), _column(ORANGE)
        detector.detect(red)
        detector.detect(red)
        assert not detector.detect(orange)
        assert not detector.detect(orange)
        assert detector.detect(orange)

    def test_reset_clears_confirmation(self):
        detector = Detector(confirm_frames=3)
        frame = _column(RED)
        detector.detect(frame)
        detector.detect(frame)
        detector.reset()
        assert detector._tracks == {}
        assert not detector.detect(frame)

    @pytest.mark.parametrize("gap", [60, 120])
    def test_position_jump_restarts_counter(self, gap):
        detector = Detector(confirm_frames=3)
        first = _frame(width=320, height=200)
        second = _frame(width=320, height=200)
        _square(first, RED, 30, 80, 15, "dot")
        _square(second, RED, 30 + gap, 80, 15, "dot")
        detector.detect(first)
        detector.detect(first)
        assert not detector.detect(second)
        assert not detector.detect(second)
        assert detector.detect(second)

    def test_alternating_positions_never_trigger(self):
        detector = Detector(confirm_frames=3)
        left = _frame(width=320, height=200)
        right = _frame(width=320, height=200)
        _square(left, RED, 30, 80, 15, "dot")
        _square(right, RED, 230, 80, 15, "dot")
        for _ in range(10):
            assert not detector.detect(left)
            assert not detector.detect(right)

    def test_no_hostile_current_frame_returns_false_immediately(self):
        detector = Detector(confirm_frames=1)
        assert detector.detect(_column(RED))
        assert not detector.detect(_frame())
        assert detector.last_boxes == []


class TestCompatibilityAndMasks:
    def test_compat_normalization(self):
        assert Detector(compat="bogus").compat == "auto"
        assert Detector(strictness=0).compat == "low_contrast"
        assert Detector(strictness=100).compat == "strict"
        assert (
            Detector(compat="low_contrast", strictness=100).compat
            == "low_contrast"
        )
        assert Detector(compat="low_contrast").strictness == 0
        assert Detector(compat="auto").strictness == 50
        assert Detector(compat="strict").strictness == 100

    def test_downscale_parameter_does_not_change_result(self):
        frame = _column(RED)
        a = Detector(downscale=1)
        b = Detector(downscale=2)
        assert a.has_red(frame) == b.has_red(frame)
        assert a.last_boxes == b.last_boxes

    def test_legacy_kwargs_are_quietly_accepted(self):
        detector = Detector(min_area=3, need_name=True, name_v=90, another_old_key=1)
        assert detector.has_red(_one(RED))

    def test_red_boxes_only_returns_hostile(self):
        friend_frame = _column(BLUE)
        detector = Detector()
        assert detector.red_boxes(friend_frame) == []
        assert detector.last_all_boxes
        assert not detector.last_boxes

    def test_last_all_boxes_contains_both_roles(self):
        frame = _column(BLUE, rows=4)
        _square(frame, RED, 20, 20 + 4 * 21, 15, "dot")
        detector = Detector()
        assert detector.has_red(frame)
        assert len(detector.last_all_boxes) >= 5
        assert len(detector.last_boxes) == 1

    def test_red_mask_matches_input_dimensions(self):
        frame = _frame(width=300, height=200)
        _square(frame, RED, 100, 80, 15, "dot")
        mask = Detector(downscale=2).red_mask(frame)
        assert mask.shape == frame.shape[:2]
        assert mask.dtype == np.uint8
        assert np.count_nonzero(mask) > 0

    def test_friend_only_mask_is_empty(self):
        mask = Detector().red_mask(_one(BLUE))
        assert mask.shape == (200, 240)
        assert not mask.any()

    def test_grayscale_frame_is_accepted(self):
        gray = np.full((200, 240), 20, dtype=np.uint8)
        cv2.rectangle(gray, (80, 70), (94, 84), 145, -1)
        assert Detector().has_red(gray)

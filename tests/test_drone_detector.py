"""无人机检测器合成帧测试。"""
from __future__ import annotations

import cv2
import numpy as np
from pathlib import Path

from core.capture import PW_RENDERFULLCONTENT, Capture
from core.drone import DroneDetector

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "assets" / "drone_templates"


class _Clock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _blank(height: int = 260, width: int = 320) -> np.ndarray:
    return np.full((height, width, 3), (24, 20, 18), dtype=np.uint8)


def _template(name: str) -> np.ndarray:
    encoded = np.fromfile(TEMPLATE_DIR / name, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE)
    assert image is not None
    return image


def _add_drone_row(
    frame: np.ndarray,
    text: str,
    row: int,
    *,
    scale: float = 1.0,
    x: int = 40,
    bar_color: tuple[int, int, int] = (235, 235, 235),
    with_bar: bool = True,
) -> None:
    template = _template("idle.png" if text == "idle" else "combat.png")
    text_height = max(1, int(round(template.shape[0] * scale)))
    text_width = max(1, int(round(template.shape[1] * scale)))
    resized = cv2.resize(
        template, (text_width, text_height), interpolation=cv2.INTER_AREA
    )
    y = 8 + row * max(26, text_height + 10)
    color = (0, 210, 20) if text == "idle" else (0, 0, 255)
    region = frame[y : y + text_height, x : x + text_width]
    region[resized > 0] = color
    if not with_bar:
        return

    bar_width = max(8, int(round(40 * scale)))
    bar_height = max(2, int(round(5 * scale)))
    gap = max(2, int(round(5 * scale)))
    bar_x = 150
    bar_y = y + (text_height - bar_height) // 2
    for index in range(3):
        left = bar_x + index * (bar_width + gap)
        cv2.rectangle(
            frame,
            (left, bar_y),
            (left + bar_width - 1, bar_y + bar_height - 1),
            bar_color,
            -1,
        )


def _add_idle_rows(frame: np.ndarray, count: int, start_y: int = 8) -> None:
    for index in range(count):
        y = start_y + index * 30
        cv2.rectangle(frame, (8, y), (70, y + 8), (0, 210, 20), -1)


def _add_return_rows(frame: np.ndarray, count: int, start_y: int = 8) -> None:
    for index in range(count):
        y = start_y + index * 30
        cv2.rectangle(frame, (8, y), (70, y + 8), (0, 140, 255), -1)


def _idle_frame(count: int = 5) -> np.ndarray:
    frame = _blank()
    for row in range(count):
        _add_drone_row(frame, "idle", row)
    return frame


def _combat_frame() -> np.ndarray:
    frame = _blank()
    _add_drone_row(frame, "combat", 0)
    return frame


def _damaged_frame() -> np.ndarray:
    frame = _blank()
    _add_drone_row(frame, "combat", 0, bar_color=(0, 0, 255))
    return frame


def _red_bar_only_frame() -> np.ndarray:
    frame = _blank()
    cv2.rectangle(frame, (150, 16), (189, 20), (0, 0, 255), -1)
    return frame


def test_counts_idle_rows_and_ignores_return_rows():
    detector = DroneDetector(count=5, confirm_frames=3, damage_frames=2)
    assert detector.update(_idle_frame(5)).idle_count == 5

    mixed = _blank()
    for row in range(3):
        _add_drone_row(mixed, "idle", row)
    _add_return_rows(mixed, 2, start_y=98)
    assert detector.update(mixed).idle_count == 3


def test_template_match_and_blood_bar_require_both_signals():
    detector = DroneDetector(count=5)

    solid_block = _blank()
    cv2.rectangle(solid_block, (40, 16), (99, 33), (0, 210, 20), -1)
    cv2.rectangle(solid_block, (150, 22), (189, 26), (235, 235, 235), -1)
    assert detector.update(solid_block).idle_count == 0

    no_bar = _blank()
    _add_drone_row(no_bar, "idle", 0, with_bar=False)
    assert detector.update(no_bar).idle_count == 0


def test_mixed_idle_and_combat_rows_counts_only_idle():
    frame = _blank()
    for row in range(3):
        _add_drone_row(frame, "idle", row)
    for row in range(3, 5):
        _add_drone_row(frame, "combat", row)
    state = DroneDetector(count=5).update(frame)
    assert state.idle_count == 3
    assert state.combat is True
    assert state.damaged is False


def test_template_rows_work_at_scaled_sizes():
    for scale in (0.5, 0.7, 1.3):
        frame = _blank()
        for row in range(5):
            _add_drone_row(frame, "idle", row, scale=scale)
        state = DroneDetector(count=5).update(frame)
        assert state.idle_count == 5


def test_half_scale_combat_text_is_not_damaged_bar():
    frame = _blank()
    _add_drone_row(frame, "combat", 0, scale=0.5)
    detector = DroneDetector(count=5, damage_frames=2)
    for _ in range(5):
        state = detector.update(frame)
    assert state.combat is True
    assert state.damaged is False
    assert detector.damage_alarm_active() is False


def test_combat_bar_colors_distinguish_damage():
    detector = DroneDetector(count=5, damage_frames=2)
    white = detector.update(_combat_frame())
    assert white.combat is True
    assert white.damaged is False

    red = detector.update(_damaged_frame())
    assert red.combat is True
    assert red.damaged is True


def test_empty_template_directory_uses_color_bar_fallback(tmp_path):
    fallback_dir = tmp_path / "empty_templates"
    fallback_dir.mkdir()
    frame = _blank()
    _add_idle_rows(frame, 5)
    for row in range(5):
        y = 8 + row * 30
        cv2.rectangle(frame, (150, y + 1), (189, y + 5), (235, 235, 235), -1)

    detector = DroneDetector(count=5, templates_dir=fallback_dir)
    state = detector.update(frame)
    assert detector._using_templates is False
    assert state.idle_count == 5

    combat = _blank()
    cv2.rectangle(combat, (40, 16), (75, 33), (0, 0, 255), -1)
    cv2.rectangle(combat, (150, 22), (189, 26), (0, 0, 255), -1)
    combat_state = detector.update(combat)
    assert combat_state.combat is True
    assert combat_state.damaged is True

    half_scale_combat = _blank()
    _add_drone_row(half_scale_combat, "combat", 0, scale=0.5)
    half_scale_state = detector.update(half_scale_combat)
    assert half_scale_state.combat is True
    assert half_scale_state.damaged is False
    assert detector.damage_alarm_active() is False


def test_all_idle_requires_configured_count_and_confirm_frames():
    three_rows = _idle_frame(3)
    detector_three = DroneDetector(count=3, confirm_frames=3)
    states = [detector_three.update(three_rows) for _ in range(3)]
    assert [state.all_idle for state in states] == [False, False, True]

    detector_five = DroneDetector(count=5, confirm_frames=3)
    assert not any(detector_five.update(three_rows).all_idle for _ in range(5))


def test_combat_while_all_idle_never_chimes():
    frame = _idle_frame(3)
    _add_drone_row(frame, "combat", 3)
    clock = _Clock()
    detector = DroneDetector(count=3, confirm_frames=3, clock=clock)
    states = [detector.update(frame) for _ in range(10)]
    clock.now = 20.0
    states.extend(detector.update(frame) for _ in range(2))
    assert all(state.all_idle for state in states[2:])
    assert all(state.combat for state in states)
    assert sum(state.idle_chime for state in states) == 0


def test_starting_idle_never_chimes_without_combat():
    clock = _Clock()
    detector = DroneDetector(count=5, confirm_frames=3, clock=clock)
    states = [detector.update(_idle_frame()) for _ in range(10)]
    clock.now = 20.0
    states.append(detector.update(_idle_frame()))
    assert all(state.all_idle for state in states[2:])
    assert not any(state.idle_chime for state in states)


def test_combat_then_idle_chimes_exactly_once():
    clock = _Clock()
    detector = DroneDetector(count=5, confirm_frames=3, clock=clock)
    assert detector.update(_combat_frame()).combat is True
    states = [detector.update(_idle_frame()) for _ in range(3)]
    assert not any(state.idle_chime for state in states)
    clock.now = 9.9
    assert detector.update(_idle_frame()).idle_chime is False
    clock.now = 10.0
    assert detector.update(_idle_frame()).idle_chime is True
    clock.now = 10.1
    assert detector.update(_idle_frame()).idle_chime is False


def test_return_only_then_idle_does_not_chime():
    clock = _Clock()
    detector = DroneDetector(count=5, confirm_frames=3, clock=clock)
    returned = _blank()
    _add_return_rows(returned, 5)
    for _ in range(4):
        assert detector.update(returned).idle_chime is False
    states = [detector.update(_idle_frame()) for _ in range(3)]
    clock.now = 20.0
    states.append(detector.update(_idle_frame()))
    assert not any(state.idle_chime for state in states)


def test_combat_return_idle_chimes():
    clock = _Clock()
    detector = DroneDetector(count=5, confirm_frames=3, clock=clock)
    detector.update(_combat_frame())
    returned = _blank()
    _add_return_rows(returned, 5)
    for _ in range(3):
        assert detector.update(returned).idle_chime is False
    for _ in range(3):
        assert detector.update(_idle_frame()).idle_chime is False
    clock.now = 10.0
    assert detector.update(_idle_frame()).idle_chime is True


def test_damaged_then_idle_chimes_after_confirmation():
    clock = _Clock()
    detector = DroneDetector(
        count=5, confirm_frames=3, damage_frames=2, clock=clock
    )
    detector.update(_damaged_frame())
    assert detector.update(_damaged_frame()).damaged is True
    for _ in range(3):
        assert detector.update(_idle_frame()).idle_chime is False
    clock.now = 9.9
    assert detector.update(_idle_frame()).idle_chime is False
    clock.now = 10.0
    assert detector.update(_idle_frame()).idle_chime is True


def test_idle_timer_resets_after_non_idle_combat_or_damage():
    returned = _blank()
    _add_return_rows(returned, 5)
    for interrupt in (returned, _combat_frame(), _damaged_frame()):
        clock = _Clock()
        detector = DroneDetector(count=5, confirm_frames=3, clock=clock)
        detector.update(_combat_frame())
        for _ in range(3):
            detector.update(_idle_frame())
        clock.now = 9.9
        assert detector.update(_idle_frame()).idle_chime is False

        detector.update(interrupt)
        assert detector._all_idle_since is None
        clock.now = 10.0
        for _ in range(3):
            assert detector.update(_idle_frame()).idle_chime is False
        assert detector._all_idle_since == 10.0
        clock.now = 19.9
        assert detector.update(_idle_frame()).idle_chime is False
        clock.now = 20.0
        assert detector.update(_idle_frame()).idle_chime is True
        clock.now = 20.1
        assert detector.update(_idle_frame()).idle_chime is False


def test_red_bar_and_red_text_are_distinguished():
    detector = DroneDetector(count=5, confirm_frames=3, damage_frames=2)
    first = detector.update(_damaged_frame())
    assert first.damaged is True
    assert detector.damage_alarm_active() is False
    second = detector.update(_damaged_frame())
    assert second.damaged is True
    assert detector.damage_alarm_active() is True

    text_state = detector.update(_combat_frame())
    assert text_state.combat is True
    assert text_state.damaged is False
    assert detector.damage_alarm_active() is False


def test_red_bar_without_combat_never_activates_alarm():
    detector = DroneDetector(count=5, confirm_frames=3, damage_frames=2)
    states = [detector.update(_red_bar_only_frame()) for _ in range(5)]
    assert all(state.damaged for state in states)
    assert all(not state.combat for state in states)
    assert detector.damage_alarm_active() is False


def test_damage_alarm_stops_when_combat_ends_even_if_bar_stays_red():
    detector = DroneDetector(count=5, confirm_frames=3, damage_frames=2)
    detector.update(_damaged_frame())
    assert detector.update(_damaged_frame()).damaged is True
    assert detector.damage_alarm_active() is True

    state = detector.update(_red_bar_only_frame())
    assert state.damaged is True
    assert state.combat is False
    assert detector.damage_alarm_active() is False


def test_invalid_frames_are_safe():
    clock = _Clock()
    detector = DroneDetector(clock=clock)
    detector.update(_combat_frame())
    for _ in range(3):
        detector.update(_idle_frame())
    assert detector._all_idle_since == 0.0
    for frame in (None, np.empty((0, 0, 3), dtype=np.uint8), np.zeros((2, 2, 3), dtype=np.uint8)):
        state = detector.update(frame)
        assert state.idle_count == 0
        assert state.combat is False
        assert state.damaged is False
        assert state.all_idle is False
        assert state.idle_chime is False
        assert detector._all_idle_since is None
    detector._all_idle_since = 1.0
    detector.reset()
    assert detector._all_idle_since is None


def test_grab_regions_retries_flags_zero_then_crops_same_frame():
    frame = np.zeros((40, 60, 3), dtype=np.uint8)
    frame[:] = (10, 20, 30)
    capture = object.__new__(Capture)
    calls = []

    def render(hwnd, flags):
        calls.append((hwnd, flags))
        return None if flags == PW_RENDERFULLCONTENT else frame

    capture._render_window = render
    regions = [
        {"left": 0, "top": 0, "width": 20, "height": 10},
        {"left": 30, "top": 10, "width": 20, "height": 20},
    ]
    cropped = capture.grab_regions(99, regions)
    assert calls == [(99, PW_RENDERFULLCONTENT), (99, 0)]
    assert cropped[0].shape == (10, 20, 3)
    assert cropped[1].shape == (20, 20, 3)

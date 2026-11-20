"""无人机检测器合成帧测试。"""
from __future__ import annotations

import cv2
import numpy as np

from core.capture import PW_RENDERFULLCONTENT, Capture
from core.drone import DroneDetector


class _Clock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _blank(height: int = 240, width: int = 220) -> np.ndarray:
    return np.full((height, width, 3), (24, 20, 18), dtype=np.uint8)


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
    _add_idle_rows(frame, count)
    return frame


def _combat_frame() -> np.ndarray:
    frame = _blank()
    cv2.rectangle(frame, (20, 40), (55, 57), (0, 0, 255), -1)
    return frame


def _damaged_frame() -> np.ndarray:
    frame = _blank()
    cv2.rectangle(frame, (20, 40), (55, 57), (0, 0, 255), -1)
    cv2.rectangle(frame, (100, 40), (139, 44), (0, 0, 255), -1)
    return frame


def _red_bar_only_frame() -> np.ndarray:
    frame = _blank()
    cv2.rectangle(frame, (100, 40), (139, 44), (0, 0, 255), -1)
    return frame


def test_counts_idle_rows_and_ignores_return_rows():
    detector = DroneDetector(count=5, confirm_frames=3, damage_frames=2)
    assert detector.update(_idle_frame(5)).idle_count == 5

    mixed = _blank()
    _add_idle_rows(mixed, 3)
    _add_return_rows(mixed, 2, start_y=98)
    assert detector.update(mixed).idle_count == 3


def test_all_idle_requires_configured_count_and_confirm_frames():
    three_rows = _idle_frame(3)
    detector_three = DroneDetector(count=3, confirm_frames=3)
    states = [detector_three.update(three_rows) for _ in range(3)]
    assert [state.all_idle for state in states] == [False, False, True]

    detector_five = DroneDetector(count=5, confirm_frames=3)
    assert not any(detector_five.update(three_rows).all_idle for _ in range(5))


def test_combat_while_all_idle_never_chimes():
    frame = _idle_frame(3)
    cv2.rectangle(frame, (20, 100), (55, 117), (0, 0, 255), -1)
    clock = _Clock()
    detector = DroneDetector(count=3, confirm_frames=3, clock=clock)
    states = [detector.update(frame) for _ in range(10)]
    clock.now = 10.0
    states.extend(detector.update(frame) for _ in range(2))
    assert all(state.all_idle for state in states[2:])
    assert all(state.combat for state in states)
    assert sum(state.idle_chime for state in states) == 0


def test_starting_idle_never_chimes_without_combat():
    clock = _Clock()
    detector = DroneDetector(count=5, confirm_frames=3, clock=clock)
    states = [detector.update(_idle_frame()) for _ in range(10)]
    clock.now = 10.0
    states.append(detector.update(_idle_frame()))
    assert all(state.all_idle for state in states[2:])
    assert not any(state.idle_chime for state in states)


def test_combat_then_idle_chimes_exactly_once():
    clock = _Clock()
    detector = DroneDetector(count=5, confirm_frames=3, clock=clock)
    assert detector.update(_combat_frame()).combat is True
    states = [detector.update(_idle_frame()) for _ in range(3)]
    assert not any(state.idle_chime for state in states)
    clock.now = 2.9
    assert detector.update(_idle_frame()).idle_chime is False
    clock.now = 3.0
    assert detector.update(_idle_frame()).idle_chime is True
    clock.now = 3.1
    assert detector.update(_idle_frame()).idle_chime is False


def test_return_only_then_idle_does_not_chime():
    clock = _Clock()
    detector = DroneDetector(count=5, confirm_frames=3, clock=clock)
    returned = _blank()
    _add_return_rows(returned, 5)
    for _ in range(4):
        assert detector.update(returned).idle_chime is False
    states = [detector.update(_idle_frame()) for _ in range(3)]
    clock.now = 10.0
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
    clock.now = 3.0
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
    clock.now = 2.9
    assert detector.update(_idle_frame()).idle_chime is False
    clock.now = 3.0
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
        clock.now = 2.9
        assert detector.update(_idle_frame()).idle_chime is False

        detector.update(interrupt)
        assert detector._all_idle_since is None
        clock.now = 3.0
        for _ in range(3):
            assert detector.update(_idle_frame()).idle_chime is False
        assert detector._all_idle_since == 3.0
        clock.now = 5.9
        assert detector.update(_idle_frame()).idle_chime is False
        clock.now = 6.0
        assert detector.update(_idle_frame()).idle_chime is True
        clock.now = 6.1
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

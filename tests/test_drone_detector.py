"""无人机检测器合成帧测试。"""
from __future__ import annotations

import cv2
import numpy as np

from core.capture import PW_RENDERFULLCONTENT, Capture
from core.drone import DroneDetector


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
    detector = DroneDetector(count=3, confirm_frames=3)
    states = [detector.update(frame) for _ in range(10)]
    assert all(state.all_idle for state in states[2:])
    assert all(state.combat for state in states)
    assert sum(state.idle_chime for state in states) == 0


def test_starting_idle_never_chimes_without_combat():
    detector = DroneDetector(count=5, confirm_frames=3)
    states = [detector.update(_idle_frame()) for _ in range(10)]
    assert all(state.all_idle for state in states[2:])
    assert not any(state.idle_chime for state in states)


def test_combat_then_idle_chimes_exactly_once():
    detector = DroneDetector(count=5, confirm_frames=3)
    assert detector.update(_combat_frame()).combat is True
    states = [detector.update(_idle_frame()) for _ in range(6)]
    assert [state.idle_chime for state in states] == [
        False,
        False,
        True,
        False,
        False,
        False,
    ]


def test_return_only_then_idle_does_not_chime():
    detector = DroneDetector(count=5, confirm_frames=3)
    returned = _blank()
    _add_return_rows(returned, 5)
    for _ in range(4):
        assert detector.update(returned).idle_chime is False
    assert not any(detector.update(_idle_frame()).idle_chime for _ in range(5))


def test_combat_return_idle_chimes():
    detector = DroneDetector(count=5, confirm_frames=3)
    detector.update(_combat_frame())
    returned = _blank()
    _add_return_rows(returned, 5)
    for _ in range(3):
        assert detector.update(returned).idle_chime is False
    states = [detector.update(_idle_frame()) for _ in range(3)]
    assert states[-1].idle_chime is True


def test_damaged_then_idle_chimes_after_confirmation():
    detector = DroneDetector(count=5, confirm_frames=3, damage_frames=2)
    detector.update(_damaged_frame())
    assert detector.update(_damaged_frame()).damaged is True
    states = [detector.update(_idle_frame()) for _ in range(3)]
    assert states[-1].idle_chime is True


def test_red_bar_and_red_text_are_distinguished():
    detector = DroneDetector(count=5, confirm_frames=3, damage_frames=2)
    first = detector.update(_damaged_frame())
    assert first.damaged is True
    assert detector.damage_confirmed() is False
    second = detector.update(_damaged_frame())
    assert second.damaged is True
    assert detector.damage_confirmed() is True

    text_state = detector.update(_combat_frame())
    assert text_state.combat is True
    assert text_state.damaged is False


def test_invalid_frames_are_safe():
    detector = DroneDetector()
    for frame in (None, np.empty((0, 0, 3), dtype=np.uint8), np.zeros((2, 2, 3), dtype=np.uint8)):
        state = detector.update(frame)
        assert state.idle_count == 0
        assert state.combat is False
        assert state.damaged is False
        assert state.all_idle is False
        assert state.idle_chime is False


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

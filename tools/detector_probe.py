"""Run the detector three times over the checked-in sample images."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.detector import Detector

EXPECTED = {
    "红名.png": True,
    "白名.png": True,
    "白色明亮背景.png": True,
    "所有可能的方块及内部图标.png": True,
    "蓝色紫色列表.png": False,
    "列表尺寸放大.png": False,
    "列表尺寸缩小.png": False,
    "空间站红灯列表.png": False,
    "太空中列表.png": False,
    "一行自己的情况.png": False,
    "空间站环境.png": False,
    "空间站环境2.png": False,
    "太空中的空间站.png": False,
}


def _imread(path: Path) -> np.ndarray | None:
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _grid_text(grid: dict | None) -> str:
    if grid is None:
        return "-"
    return f"{grid['rows']},{grid['pitch']:.1f},{grid['size']:.1f}"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    sample_dir = Path(args[0]) if args else Path("样例")
    failures: list[str] = []
    rows = []

    for filename, expected in EXPECTED.items():
        frame = _imread(sample_dir / filename)
        if frame is None:
            failures.append(f"{filename}: missing/unreadable")
            rows.append((filename, "ERROR", [], [], "-", "missing"))
            continue

        detector = Detector(confirm_frames=3)
        alerts = [detector.detect(frame) for _ in range(3)]
        alert = any(alerts)
        count_ok = filename != "白色明亮背景.png" or len(detector.last_boxes) >= 5
        if not count_ok:
            failures.append(
                f"{filename}: hostile_boxes={len(detector.last_boxes)}, expected >=5"
            )
        status = (
            "OK"
            if alert == expected and count_ok
            else f"FAIL(expected {expected}, hostile>=5)"
        )
        if alert != expected:
            failures.append(f"{filename}: alert={alert}, expected={expected}")
        rows.append(
            (
                filename,
                str(alert),
                detector.last_boxes,
                detector.last_all_boxes,
                _grid_text(detector.last_grid),
                status,
            )
        )

    print("image | alert | hostile_boxes | all_boxes | grid(rows,pitch,size) | status")
    print("--- | --- | --- | --- | --- | ---")
    for filename, alert, hostile, all_boxes, grid, status in rows:
        print(f"{filename} | {alert} | {hostile} | {all_boxes} | {grid} | {status}")

    if failures:
        print("\nFailures:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

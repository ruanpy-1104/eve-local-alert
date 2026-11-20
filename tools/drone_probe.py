"""无人机样例校准工具。

读取目录中的空闲 / 战斗 / 返回样例，分别连续送入检测器 5 帧，
输出每张图最后一帧的识别结果 JSON。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.drone import DroneDetector


def _load_image(path: Path) -> np.ndarray | None:
    """支持 Windows 中文路径的 OpenCV 读取。"""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def probe(directory: Path, scale: float = 1.0) -> dict:
    names = ("无人机空闲.png", "无人机战斗.png", "无人机返回.png")
    if not directory.is_dir():
        return {"skip": True}
    existing = [name for name in names if (directory / name).is_file()]
    if not existing:
        return {"skip": True}

    results: dict[str, dict[str, int | bool]] = {}
    for name in existing:
        frame = _load_image(directory / name)
        if frame is None:
            results[name] = {
                "idle_count": 0,
                "combat": False,
                "damaged": False,
            }
            continue
        if scale != 1.0:
            frame = cv2.resize(
                frame,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_AREA,
            )
        detector = DroneDetector(count=5)
        state = None
        for _ in range(5):
            state = detector.update(frame)
        results[name] = {
            "idle_count": state.idle_count if state is not None else 0,
            "combat": bool(state.combat) if state is not None else False,
            "damaged": bool(state.damaged) if state is not None else False,
        }
    return results


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="无人机样例多尺度探针")
    parser.add_argument("directory", nargs="?", default="样例")
    parser.add_argument("--scale", type=float, default=1.0)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.scale <= 0:
        parser.error("--scale 必须大于 0")
    print(
        json.dumps(
            probe(Path(args.directory), scale=args.scale),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

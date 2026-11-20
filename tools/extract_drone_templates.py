"""从样例截图提取无人机「空闲 / 战斗中 / 返回中」二值文字模板。"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.drone import (
    build_color_masks,
    find_text_clusters,
    prepare_text_mask,
    to_bgr,
)

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "样例"
OUTPUT_DIR = ROOT / "assets" / "drone_templates"


def _load_image(path: Path) -> np.ndarray | None:
    """支持 Windows 中文路径的 OpenCV 读取。"""
    try:
        encoded = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _write_png(path: Path, image: np.ndarray) -> None:
    """以 0/255 单通道 PNG 写盘，避免依赖中文路径的 ``imwrite``。"""
    binary = np.where(image > 0, 255, 0).astype(np.uint8)
    ok, encoded = cv2.imencode(".png", binary)
    if not ok:
        raise RuntimeError(f"PNG 编码失败：{path}")
    encoded.tofile(path)


def _extract_cluster(mask: np.ndarray, cluster: tuple[int, int, int, int]) -> np.ndarray:
    """按包含端点的框裁剪已经做过同一闭运算的掩码。"""
    closed = prepare_text_mask(mask)
    x0, y0, x1, y1 = cluster
    return closed[y0 : y1 + 1, x0 : x1 + 1].copy()


def main() -> int:
    if not SAMPLE_DIR.is_dir():
        print("skip: 未找到样例目录")
        return 0

    idle_path = SAMPLE_DIR / "无人机空闲.png"
    combat_path = SAMPLE_DIR / "无人机战斗.png"
    return_path = SAMPLE_DIR / "无人机返回.png"
    if not idle_path.is_file() or not combat_path.is_file() or not return_path.is_file():
        print("skip: 未找到无人机空闲 / 战斗 / 返回样例")
        return 0

    idle_frame = _load_image(idle_path)
    combat_frame = _load_image(combat_path)
    return_frame = _load_image(return_path)
    if idle_frame is None or combat_frame is None or return_frame is None:
        print("skip: 无人机样例不可读")
        return 0
    idle_bgr = to_bgr(idle_frame)
    combat_bgr = to_bgr(combat_frame)
    return_bgr = to_bgr(return_frame)
    if idle_bgr is None or combat_bgr is None or return_bgr is None:
        print("skip: 无人机样例格式不支持")
        return 0

    idle_green, _, _, _ = build_color_masks(idle_bgr)
    _, combat_red, _, _ = build_color_masks(combat_bgr)
    _, _, return_orange, _ = build_color_masks(return_bgr)
    idle_clusters = find_text_clusters(idle_green)
    combat_clusters = find_text_clusters(combat_red)
    return_clusters = find_text_clusters(return_orange)
    if len(idle_clusters) < 3 or not combat_clusters or not return_clusters:
        print("skip: 样例中未找到预期的文字簇")
        return 0

    # 空闲取第 3 行，避开第 1 行的选中高亮；战斗 / 返回取第 1 行。
    idle_template = _extract_cluster(idle_green, idle_clusters[2])
    combat_template = _extract_cluster(combat_red, combat_clusters[0])
    return_template = _extract_cluster(return_orange, return_clusters[0])

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _write_png(OUTPUT_DIR / "idle.png", idle_template)
    _write_png(OUTPUT_DIR / "combat.png", combat_template)
    _write_png(OUTPUT_DIR / "return.png", return_template)
    print(
        f"已写入：{OUTPUT_DIR / 'idle.png'} "
        f"({idle_template.shape[1]}x{idle_template.shape[0]})"
    )
    print(
        f"已写入：{OUTPUT_DIR / 'combat.png'} "
        f"({combat_template.shape[1]}x{combat_template.shape[0]})"
    )
    print(
        f"已写入：{OUTPUT_DIR / 'return.png'} "
        f"({return_template.shape[1]}x{return_template.shape[0]})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""EVE 总览可用颜色（警报颜色预设）。

从用户提供的「样例/总览可选颜色.png」中提取的 12 种总览名称颜色——这是 EVE 全部
可自定义的颜色，游戏内每个阵营图标（玩家名前的色块）都可改为其中任意一种，因此软件
的「颜色选择」功能以该图为基准。
每种颜色以「中心色相 + 识别严格度带宽」描述：识别程度滑块（宽松~严格）按比例缩放
色相带宽与饱和度/明度下限，越严格判定越苛刻、误报越少。
"""
from __future__ import annotations

# 每种颜色:
#   label    = 显示名称
#   rgb      = 色块显示颜色 (R, G, B)
#   center_h = 色相中心（彩色）；wrap 表示跨越色相环两端（红色）
#   achromatic = 无色相（灰白/黑色），用明度区间描述
EVE_COLORS: dict[str, dict] = {
    "red": {"label": "红色", "rgb": (191, 0, 0), "center_h": 0, "wrap": True},
    "orange_red": {"label": "橙红", "rgb": (255, 89, 0), "center_h": 10},
    "orange": {"label": "橙色", "rgb": (255, 178, 0), "center_h": 21},
    "gray_white": {
        "label": "灰白",
        "rgb": (178, 178, 178),
        "achromatic": True,
        "v_loose": 86,  # 宽松时明度下限（下探以捕获中立灰白图标 V≈112）
        "v_strict": 106,  # 严格时明度下限（仍能捕获中立图标，仅排除更暗灰底）
    },
    "green": {"label": "绿色", "rgb": (25, 153, 25), "center_h": 60},
    "teal": {"label": "青绿", "rgb": (0, 160, 145), "center_h": 87},
    "dark_teal": {"label": "深青", "rgb": (0, 87, 84), "center_h": 89},
    "blue": {"label": "蓝色", "rgb": (0, 38, 153), "center_h": 113},
    "azure": {"label": "天蓝", "rgb": (51, 127, 255), "center_h": 109},
    "purple": {"label": "紫色", "rgb": (76, 0, 127), "center_h": 138},
    "magenta": {"label": "品红", "rgb": (153, 38, 229), "center_h": 138},
    "black": {
        "label": "黑色",
        "rgb": (0, 0, 0),
        "achromatic": True,
        "v_hi_loose": 100,  # 宽松时明度上限
        "v_hi_strict": 60,  # 严格时明度上限（只认更暗）
    },
}

# UI 展示顺序
COLOR_ORDER: tuple[str, ...] = tuple(EVE_COLORS.keys())

# 默认启用的警报颜色
DEFAULT_ALERT_COLORS: list[str] = ["red", "orange_red", "orange", "gray_white"]

# 默认识别程度（0 宽松 ~ 100 严格）
DEFAULT_STRICTNESS: int = 50

# 彩色阈值带宽（宽松 / 严格两端）
_H_LOOSE, _H_STRICT = 15, 5       # 色相半宽
_S_LOOSE, _S_STRICT = 80, 150     # 饱和度下限
_V_LOOSE, _V_STRICT = 40, 70      # 明度下限


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def color_ranges(name: str, strictness: float = 0.5) -> list[tuple[tuple, tuple]]:
    """返回指定颜色在给定严格度(0 宽松 ~ 1 严格)下的 HSV 阈值区间列表。"""
    info = EVE_COLORS.get(name)
    if info is None:
        return []  # 未知颜色忽略
    if info.get("achromatic"):
        if name == "gray_white":
            v_min = int(_lerp(info["v_loose"], info["v_strict"], strictness))
            return [((0, 0, v_min), (179, 50, 255))]
        if name == "black":
            v_max = int(_lerp(info["v_hi_loose"], info["v_hi_strict"], strictness))
            return [((0, 0, 0), (179, 60, v_max))]
        return []
    center = info["center_h"]
    h_half = int(_lerp(_H_LOOSE, _H_STRICT, strictness))
    s_min = int(_lerp(_S_LOOSE, _S_STRICT, strictness))
    v_min = int(_lerp(_V_LOOSE, _V_STRICT, strictness))
    hi_h = min(179, center + h_half)
    if info.get("wrap"):  # 红色跨越色相环两端
        return [
            ((0, s_min, v_min), (hi_h, 255, 255)),
            ((179 - h_half, s_min, v_min), (179, 255, 255)),
        ]
    lo_h = max(0, center - h_half)
    return [((lo_h, s_min, v_min), (hi_h, 255, 255))]

"""EVE 总览警报颜色（颜色预设与识别程度换算）。

总览里每个玩家名字前的阵营图标可取「样例/总览可选颜色.png」中的预设色，本工具只保留
区分「敌对 / 中立」最常用的 4 种（取值即调色板实测采样）：

    红 (191,0,0)   橙 (255,89,0)   黄 (255,178,0)   灰白 (178,178,178)

其余 8 种（绿 / 青绿 / 深青 / 蓝 / 天蓝 / 紫 / 品红 / 黑）已移除：紫与品红在 HSV 上
几乎重合、黑在深色总览背景上无法与背景分离（掩码会与背景连成一块），其余冷色与
「红名预警」场景无关，列出只会让「颜色选择」变成多选题海。

两条关键约定：

1. **色相窗口按相邻预设的中点固定划分，互不重叠。** 三个暖色在 OpenCV 色相环
   （H∈[0,179]）上只相距 10~11（红 0 / 橙 10 / 黄 21），若沿用「中心色相 ± 半带宽」
   必然互相重叠（勾「橙」会同时命中「黄」）；红色跨色相环 0 点，拆成两段区间。
2. **识别程度（0 宽松 ~ 100 严格）只调节饱和度 / 明度下限**，不再缩放色相窗口——
   实测总览红图标色相 H≈1.5–3.4，收窄窗口会把真实图标挤出窗口造成漏报。
"""
from __future__ import annotations

# 每种颜色:
#   label      = 显示名称
#   rgb        = 色块显示颜色 (R, G, B)，取自「样例/总览可选颜色.png」实测值
#   h_ranges   = 色相窗口（OpenCV H∈[0,179]），按相邻预设中点划分，彼此不重叠
#   achromatic = 无色相（灰白），改用明度区间描述
EVE_COLORS: dict[str, dict] = {
    "red": {"label": "红", "rgb": (191, 0, 0), "h_ranges": ((0, 5), (175, 179))},
    "orange": {"label": "橙", "rgb": (255, 89, 0), "h_ranges": ((6, 15),)},
    "yellow": {"label": "黄", "rgb": (255, 178, 0), "h_ranges": ((16, 26),)},
    "gray_white": {
        "label": "灰白",
        "rgb": (178, 178, 178),
        "achromatic": True,
        "v_loose": 88,  # 宽松时明度下限（下探以捕获较暗的中立灰白图标 V≈107–138）
        "v_strict": 100,  # 严格时明度下限（仍能捕获中立图标，仅排除更暗灰底）
    },
}

# UI 展示顺序（色相环由红到黄，灰白收尾）
COLOR_ORDER: tuple[str, ...] = tuple(EVE_COLORS.keys())

# 默认启用的警报颜色：敌对三色（红 / 橙 / 黄）+ 中立灰白
DEFAULT_ALERT_COLORS: list[str] = list(COLOR_ORDER)

# 默认识别程度（0 宽松 ~ 100 严格）
DEFAULT_STRICTNESS: int = 50

# 彩色阈值带宽（宽松端 / 严格端）。识别程度在此区间线性插值：越严格下限越高，
# 越能排除背景里灰暗、发白的杂色。
_S_LOOSE, _S_STRICT = 70, 140   # 饱和度下限（越高越排除粉白灰）
_V_LOOSE, _V_STRICT = 40, 75    # 明度下限（越高越排除暗部）


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def is_achromatic(name: str) -> bool:
    """该颜色是否无色相（仅用明度区间描述，如灰白）。未知颜色返回 False。"""
    info = EVE_COLORS.get(name)
    return bool(info and info.get("achromatic"))


def color_ranges(name: str, strictness: float = 0.5) -> list[tuple[tuple, tuple]]:
    """返回指定颜色在给定严格度(0 宽松 ~ 1 严格)下的 HSV 阈值区间列表。

    彩色返回 1~2 个色相区间（红色跨色相环两端拆为两个），灰白返回明度区间；
    未知颜色（含旧配置里已移除的颜色）返回空列表，由调用方安静忽略。
    """
    info = EVE_COLORS.get(name)
    if info is None:
        return []
    t = max(0.0, min(1.0, strictness))
    if info.get("achromatic"):
        v_min = int(_lerp(info["v_loose"], info["v_strict"], t))
        return [((0, 0, v_min), (179, 50, 255))]
    s_min = int(_lerp(_S_LOOSE, _S_STRICT, t))
    v_min = int(_lerp(_V_LOOSE, _V_STRICT, t))
    return [((h_lo, s_min, v_min), (h_hi, 255, 255)) for h_lo, h_hi in info["h_ranges"]]

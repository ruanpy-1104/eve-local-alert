"""颜色预设单元测试：4 色预设、色相窗口互斥、识别程度插值、未知颜色忽略。"""
import pytest

from core.colors import (
    COLOR_ORDER,
    DEFAULT_ALERT_COLORS,
    EVE_COLORS,
    color_ranges,
    is_achromatic,
)

PRESETS = ["red", "orange", "yellow", "gray_white"]


def _hues(name: str, strictness: float = 0.5) -> set[int]:
    """该颜色覆盖的色相集合。"""
    covered: set[int] = set()
    for low, high in color_ranges(name, strictness):
        covered |= set(range(low[0], high[0] + 1))
    return covered


def test_only_four_presets():
    assert list(COLOR_ORDER) == PRESETS
    assert set(EVE_COLORS) == set(PRESETS)
    assert DEFAULT_ALERT_COLORS == PRESETS


def test_every_preset_has_display_info():
    for name in PRESETS:
        info = EVE_COLORS[name]
        assert info["label"] and isinstance(info["label"], str)
        assert len(info["rgb"]) == 3
        assert all(0 <= c <= 255 for c in info["rgb"])


def test_color_ranges_non_empty_for_presets():
    for name in PRESETS:
        assert color_ranges(name, 0.5), name


def test_hue_windows_are_pairwise_disjoint():
    colored = [n for n in PRESETS if not is_achromatic(n)]
    for i, a in enumerate(colored):
        for b in colored[i + 1 :]:
            assert not (_hues(a) & _hues(b)), f"{a} 与 {b} 的色相窗口重叠"


@pytest.mark.parametrize("strictness", [0.0, 0.5, 1.0])
def test_hue_window_is_stable_across_strictness(strictness):
    # 色相窗口固定（不随识别程度收窄），避免严格档把真实图标挤出窗口
    assert _hues("orange", strictness) == _hues("orange", 0.5)
    assert _hues("yellow", strictness) == _hues("yellow", 0.5)
    assert _hues("red", strictness) == _hues("red", 0.5)


def test_red_wraps_around_hue_circle():
    ranges = color_ranges("red", 0.5)
    assert len(ranges) == 2
    assert ranges[0][0][0] == 0  # 低端（0 附近）
    assert ranges[1][1][0] == 179  # 高端（色相环末端）


def test_strictness_raises_floors_only():
    loose = color_ranges("orange", 0.0)[0]
    strict = color_ranges("orange", 1.0)[0]
    assert strict[0][1] > loose[0][1]  # 饱和度下限抬高
    assert strict[0][2] > loose[0][2]  # 明度下限抬高


def test_strictness_is_clamped():
    assert color_ranges("orange", 999) == color_ranges("orange", 1.0)
    assert color_ranges("orange", -5) == color_ranges("orange", 0.0)


def test_gray_white_is_achromatic_with_v_floor():
    assert is_achromatic("gray_white")
    assert not is_achromatic("red")
    (low, high), = color_ranges("gray_white", 0.5)
    assert low[0] == 0 and high[0] == 179  # 无色相约束
    assert high[1] == 50  # 低饱和（灰 / 白）才进入灰白掩码
    # 中立图标实测 V≈107–138 与调色板灰白 V=178 都应落在明度区间内
    assert low[2] <= 107 and 178 <= high[2]
    # 严格档抬高明度下限，排除更暗的灰底
    assert color_ranges("gray_white", 1.0)[0][0][2] > color_ranges("gray_white", 0.0)[0][0][2]


def test_removed_and_unknown_colors_return_empty():
    for removed in ("orange_red", "green", "teal", "dark_teal", "blue", "azure",
                    "purple", "magenta", "black", "not_a_color"):
        assert color_ranges(removed, 0.5) == [], removed
        assert not is_achromatic(removed)

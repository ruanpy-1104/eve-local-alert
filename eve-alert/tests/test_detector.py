"""Detector 单元测试：颜色阈值、面积 / 宽高比筛选、时序确认。"""
import numpy as np

from core.detector import Detector

RED = (0, 0, 255)      # BGR 纯红
BLUE = (255, 0, 0)     # BGR 纯蓝
GRAY = (128, 128, 128)  # BGR 灰


def _solid(color, size=(200, 300, 3)) -> np.ndarray:
    return np.full(size, color, dtype=np.uint8)


def _black(size=(200, 300, 3)) -> np.ndarray:
    return np.zeros(size, dtype=np.uint8)


class TestDetectorThreshold:
    def test_red_frame_detected(self):
        assert Detector().has_red(_solid(RED))

    def test_blue_frame_not_detected(self):
        assert not Detector().has_red(_solid(BLUE))

    def test_gray_frame_not_detected(self):
        assert not Detector().has_red(_solid(GRAY))

    def test_dark_red_above_v_low_detected(self):
        # V=80 高于默认 V 下限 60，应命中
        assert Detector().has_red(_solid((0, 0, 80)))

    def test_too_dark_red_rejected(self):
        # V=30 低于默认 V 下限 60，应排除
        assert not Detector().has_red(_solid((0, 0, 30)))


class TestDetectorGeometry:
    def test_tiny_red_blob_ignored(self):
        d = Detector(min_area=80)
        frame = _black()
        frame[100:105, 100:105] = RED  # 5x5，下采样后面积不足
        assert not d.has_red(frame)

    def test_wide_red_bar_detected(self):
        d = Detector(min_area=80)
        frame = _black()
        frame[90:105, 80:200] = RED  # 宽扁名牌形态
        assert d.has_red(frame)

    def test_square_red_blob_rejected_by_aspect(self):
        d = Detector(min_area=80, min_aspect_ratio=1.5)
        frame = _black()
        frame[60:160, 80:180] = RED  # 100x100 方形，宽高比 1.0 < 1.5
        assert not d.has_red(frame)

    def test_text_glyphs_merged_by_horizontal_dilation(self):
        # 模拟名称文字：多个分散的小色块（文字笔画），水平膨胀后被合并为长条并命中
        d = Detector(min_area=80)
        frame = _black()
        for x in (100, 115, 130):
            frame[120:130, x : x + 8] = RED
        assert d.has_red(frame)

    def test_last_boxes_in_original_coords(self):
        d = Detector(downscale=2)
        frame = _black()
        frame[90:105, 80:200] = RED  # 宽扁名牌
        d.detect(frame)  # 首帧未到确认帧数，但 last_boxes 已填充
        assert d.last_boxes
        x, y, w, h = d.last_boxes[0]
        # 原分辨率坐标：框应覆盖原始矩形 (x 80-200)
        assert x <= 200 and x + w >= 80 and w > 0


class TestDetectorTemporal:
    def test_confirm_requires_n_frames(self):
        d = Detector(confirm_frames=3)
        frame = _solid(RED)
        assert not d.detect(frame)
        assert not d.detect(frame)
        assert d.detect(frame)  # 第 3 帧触发

    def test_missing_frame_resets_counter(self):
        d = Detector(confirm_frames=3)
        red, blue = _solid(RED), _solid(BLUE)
        assert not d.detect(red)
        d.detect(blue)  # 未命中，计数清零
        assert not d.detect(red)
        assert not d.detect(red)
        assert d.detect(red)

    def test_reset_clears_confirmation(self):
        d = Detector(confirm_frames=3)
        frame = _solid(RED)
        d.detect(frame)
        d.detect(frame)
        d.reset()
        assert not d.detect(frame)  # 计数清空，重新累计


class TestDetectorMask:
    def test_red_mask_shape_matches_downscaled(self):
        d = Detector(downscale=2)
        frame = _solid(RED, size=(200, 300, 3))
        mask = d.red_mask(frame)
        assert mask.shape == (100, 150)

    def test_red_boxes_only_for_red(self):
        d = Detector()
        assert d.red_boxes(_solid(BLUE)) == []
        assert len(d.red_boxes(_solid(RED))) >= 1


class TestMultiColor:
    """多警报颜色：按 EVE 总览预设色进行检测。"""

    def test_blue_detected_when_enabled(self):
        d = Detector(colors=["blue"])
        assert d.has_red(_solid(BLUE))
        assert not d.has_red(_solid(RED))

    def test_green_detected_when_enabled(self):
        d = Detector(colors=["green"])
        assert d.has_red(_solid((0, 255, 0)))  # BGR 绿 -> H=60

    def test_red_default_only(self):
        d = Detector()
        assert d.has_red(_solid(RED))
        assert not d.has_red(_solid(BLUE))

    def test_multiple_colors_union(self):
        d = Detector(colors=["red", "blue"])
        assert d.has_red(_solid(RED))
        assert d.has_red(_solid(BLUE))
        assert not d.has_red(_solid((0, 255, 0)))  # 未启用绿色

    def test_colors_str_single(self):
        d = Detector(colors="blue")
        assert d.colors == ("blue",)

    def test_unknown_color_ignored(self):
        d = Detector(colors=["red", "not_a_color"])
        assert d.has_red(_solid(RED))
        assert not d.has_red(_solid(BLUE))

    def test_gray_white_detected_when_enabled(self):
        d = Detector(colors=["gray_white"])
        assert d.has_red(_solid((178, 178, 178)))
        assert not d.has_red(_solid((128, 128, 128)))  # 中灰低于灰白 V 下限

    def test_black_detected_when_enabled(self):
        d = Detector(colors=["black"])
        assert d.has_red(_solid((0, 0, 0)))
        assert not d.has_red(_solid((0, 0, 100)))  # V 过高非黑色

    def test_default_colors_include_gray_white_not_black(self):
        d = Detector()
        assert d.has_red(_solid((178, 178, 178)))  # 灰白默认启用
        assert not d.has_red(_solid((0, 0, 0)))  # 黑色默认未启用


class TestStrictness:
    """识别程度：宽松包含更多，严格判定更苛刻。"""

    def test_loose_detects_borderline(self):
        # 暗红 V=50：宽松(0)时 v_min=40 命中，严格(100)时 v_min=70 不命中
        frame = _solid((0, 0, 50))
        assert Detector(strictness=0).has_red(frame)
        assert not Detector(strictness=100).has_red(frame)

    def test_strict_still_detects_strong_color(self):
        # 高饱和高亮红色在严格模式下仍命中
        assert Detector(strictness=100).has_red(_solid(RED))

    def test_strictness_clamped(self):
        assert Detector(strictness=999).strictness == 100
        assert Detector(strictness=-5).strictness == 0

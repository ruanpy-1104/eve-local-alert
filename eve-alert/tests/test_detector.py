"""Detector 单元测试：颜色阈值、图标几何筛选、名字验证、时序确认。"""
import cv2
import numpy as np

from core.detector import Detector

RED = (0, 0, 255)        # BGR 纯红
BLUE = (255, 0, 0)       # BGR 纯蓝
GRAY = (129, 129, 129)   # BGR 中立灰白图标色（V≈129）
DARK_GRAY = (80, 80, 80)  # BGR 暗灰（低于灰白 V 下限）
WHITE = (255, 255, 255)  # 名字文字白色


def _frame(size=(400, 400), bg=(0, 0, 0)) -> np.ndarray:
    return np.full((size[0], size[1], 3), bg, dtype=np.uint8)


def _icon(frame, color, x=100, y=100, size=15) -> np.ndarray:
    """绘制一个实心方块图标（EVE 总览名字前的阵营标识）。"""
    cv2.rectangle(frame, (x, y), (x + size - 1, y + size - 1), color, -1)
    return frame


def _name(frame, x=None, y=100, width=70, height=12) -> np.ndarray:
    """在图标右侧绘制一条明亮白色文字条，用于「名字验证」。"""
    if x is None:
        x = 100 + 15 + 6
    cv2.rectangle(frame, (x, y), (x + width - 1, y + height - 1), WHITE, -1)
    return frame


def _icon_frame(color, name=True, **kw) -> np.ndarray:
    """构造「图标 + 可选名字」的标准帧。"""
    f = _frame(**kw)
    _icon(f, color)
    if name:
        _name(f)
    return f


class TestDetectorThreshold:
    def test_red_icon_detected(self):
        assert Detector().has_red(_icon_frame(RED))

    def test_blue_icon_not_detected(self):
        # 默认未启用蓝色，图标不应命中
        assert not Detector().has_red(_icon_frame(BLUE))

    def test_gray_white_icon_detected(self):
        # 中立灰白图标（V≈129）应被识别（旧算法漏检中立）
        assert Detector().has_red(_icon_frame(GRAY))

    def test_dark_gray_rejected(self):
        # 低于灰白明度下限的暗灰，应排除
        assert not Detector().has_red(_icon_frame(DARK_GRAY))

    def test_black_icon_not_default(self):
        # 黑色默认未启用
        assert not Detector().has_red(_icon_frame((0, 0, 0)))

    def test_too_dark_red_rejected(self):
        # V=30 低于彩色 V 下限 70（严格时），应排除
        assert not Detector(strictness=100).has_red(_icon_frame((0, 0, 30)))


class TestDetectorGeometry:
    def test_tiny_icon_ignored(self):
        f = _frame()
        cv2.rectangle(f, (100, 100), (103, 103), RED, -1)  # 4x4，面积不足
        _name(f, x=110)
        assert not Detector().has_red(f)

    def test_square_icon_detected(self):
        assert Detector().has_red(_icon_frame(RED))

    def test_wide_bar_rejected(self):
        # 旧算法命中的「宽扁名牌条」，如今按图标判定应被宽高比过滤（aspect > 1.6）
        f = _frame()
        f[120:135, 80:250] = RED
        assert not Detector().has_red(f)

    def test_bare_square_rejected_when_names_present(self):
        # 帧内含「带名字的图标」，说明是正常总览 → 仅保留名字对齐的图标；
        # 一个无名字的实心方块（如背景红色恒星）应被名字验证剔除。
        f = _frame()
        _icon(f, RED)
        _name(f)
        cv2.rectangle(f, (100, 160), (114, 174), RED, -1)  # 无名字的方块
        assert len(Detector().red_boxes(f)) == 1

    def test_icon_only_roi_detected(self):
        # 纯图标 ROI（整帧无任何名字）→ 退化为几何判定，无名字参考也能识别图标
        f = _icon_frame(RED, name=False)
        assert Detector().has_red(f)

    def test_need_name_false_accepts_all(self):
        # 关闭名字验证后，所有几何通过的色块都命中
        f = _icon_frame(RED, name=False)
        assert Detector(need_name=False).has_red(f)

    def test_icon_size_adaptive_small(self):
        # 小字体图标（8px，旧固定下限附近）不应被丢下
        f = _frame()
        cv2.rectangle(f, (100, 100), (107, 107), RED, -1)
        cv2.rectangle(f, (112, 101), (150, 106), WHITE, -1)
        assert Detector().has_red(f)

    def test_icon_size_adaptive_large(self):
        # 大字体图标（40px，旧固定上限会丢弃）应能识别——不能用固定像素判定大小
        f = _frame()
        cv2.rectangle(f, (150, 100), (189, 139), RED, -1)
        cv2.rectangle(f, (196, 103), (300, 136), WHITE, -1)
        assert Detector().has_red(f)


class TestDetectorTemporal:
    def _red_icon(self) -> np.ndarray:
        return _icon_frame(RED)

    def test_confirm_requires_n_frames(self):
        d = Detector(confirm_frames=3)
        frame = self._red_icon()
        assert not d.detect(frame)
        assert not d.detect(frame)
        assert d.detect(frame)  # 第 3 帧触发

    def test_missing_frame_resets_counter(self):
        d = Detector(confirm_frames=3)
        red, blue = self._red_icon(), _icon_frame(BLUE)
        assert not d.detect(red)
        d.detect(blue)  # 未命中，计数清零
        assert not d.detect(red)
        assert not d.detect(red)
        assert d.detect(red)

    def test_reset_clears_confirmation(self):
        d = Detector(confirm_frames=3)
        frame = self._red_icon()
        d.detect(frame)
        d.detect(frame)
        d.reset()
        assert not d.detect(frame)  # 计数清空，重新累计


class TestDetectorMask:
    def test_red_mask_shape_matches_downscaled(self):
        d = Detector(downscale=2)
        frame = np.full((200, 300, 3), RED, dtype=np.uint8)
        mask = d.red_mask(frame)
        assert mask.shape == (100, 150)

    def test_red_boxes_only_for_red(self):
        d = Detector()
        assert d.red_boxes(_icon_frame(BLUE)) == []
        assert len(d.red_boxes(_icon_frame(RED))) >= 1


class TestMultiColor:
    """多警报颜色：按 EVE 总览预设色进行检测。"""

    def test_blue_detected_when_enabled(self):
        d = Detector(colors=["blue"])
        assert d.has_red(_icon_frame(BLUE))
        assert not d.has_red(_icon_frame(RED))

    def test_green_detected_when_enabled(self):
        d = Detector(colors=["green"])
        assert d.has_red(_icon_frame((0, 255, 0)))  # BGR 绿 -> H=60

    def test_red_default_only(self):
        d = Detector()
        assert d.has_red(_icon_frame(RED))
        assert not d.has_red(_icon_frame(BLUE))

    def test_multiple_colors_union(self):
        d = Detector(colors=["red", "blue"])
        assert d.has_red(_icon_frame(RED))
        assert d.has_red(_icon_frame(BLUE))
        assert not d.has_red(_icon_frame((0, 255, 0)))  # 未启用绿色

    def test_colors_str_single(self):
        d = Detector(colors="blue")
        assert d.colors == ("blue",)

    def test_unknown_color_ignored(self):
        d = Detector(colors=["red", "not_a_color"])
        assert d.has_red(_icon_frame(RED))
        assert not d.has_red(_icon_frame(BLUE))

    def test_gray_white_detected_when_enabled(self):
        d = Detector(colors=["gray_white"])
        assert d.has_red(_icon_frame(GRAY))
        assert not d.has_red(_icon_frame(DARK_GRAY))  # 暗灰低于灰白 V 下限

    def test_black_detected_when_enabled(self):
        d = Detector(colors=["black"])
        # 黑色图标需置于明亮背景才可分辨（纯黑背景会与图标融为一体）
        assert d.has_red(_icon_frame((0, 0, 0), bg=(200, 200, 200)))
        assert not d.has_red(_icon_frame((0, 0, 100), bg=(200, 200, 200)))  # V 过高非黑色

    def test_default_colors_include_gray_white_not_black(self):
        d = Detector()
        assert d.has_red(_icon_frame(GRAY))  # 灰白默认启用
        assert not d.has_red(_icon_frame((0, 0, 0)))  # 黑色默认未启用


class TestGrayWhiteSeparation:
    """灰白掩码会被名字文字污染，须用更高填充率把「实心图标」与「名字字形」分开。"""

    def test_gray_white_icon_detected(self):
        d = Detector(colors=["gray_white"])
        assert d.has_red(_icon_frame(GRAY, name=True))

    def test_gray_white_icon_detected_without_name(self):
        # 纯图标 ROI（无名字）也能识别
        d = Detector(colors=["gray_white"])
        assert d.has_red(_icon_frame(GRAY, name=False))

    def test_hollow_gray_blob_rejected(self):
        # 空心/低填充的灰色形状（模拟名字字形 O/D 之类）应被填充率剔除，仅实心方块命中
        d = Detector(colors=["gray_white"])
        f = _frame()
        cv2.rectangle(f, (100, 100), (114, 114), GRAY, -1)       # 外框
        cv2.rectangle(f, (102, 102), (112, 112), (0, 0, 0), -1)  # 内部挖空，填充率低
        assert not d.has_red(f)

    def test_name_glyphs_not_detected(self):
        # 一排空心白色字形（低填充率）代表名字文字：不应被当作中立图标目标
        d = Detector(colors=["gray_white"])
        f = _frame()
        for i in range(4):
            x = 100 + i * 18
            cv2.rectangle(f, (x, 100), (x + 14, 114), WHITE, -1)
            cv2.rectangle(f, (x + 2, 102), (x + 12, 112), (0, 0, 0), -1)  # 空心
        assert d.red_boxes(f) == []

    def test_gray_icon_detected_among_name_text(self):
        # 实心中立图标 + 空心名字字形同帧：仅实心图标命中，名字字形不作为目标
        d = Detector(colors=["gray_white"])
        f = _frame()
        _icon(f, GRAY, x=60, y=100)
        for i in range(4):
            x = 90 + i * 18
            cv2.rectangle(f, (x, 100), (x + 14, 114), WHITE, -1)
            cv2.rectangle(f, (x + 2, 102), (x + 12, 112), (0, 0, 0), -1)  # 空心
        assert len(d.red_boxes(f)) == 1


class TestStrictness:
    """识别程度：宽松包含更多，严格判定更苛刻。"""

    def test_loose_detects_borderline(self):
        # 暗红 V=50：宽松(0)时 v_min=40 命中，严格(100)时 v_min=70 不命中
        f = _icon_frame((0, 0, 50))
        assert Detector(strictness=0).has_red(f)
        assert not Detector(strictness=100).has_red(f)

    def test_strict_still_detects_strong_color(self):
        # 高饱和高亮红色在严格模式下仍是实心方块图标
        assert Detector(strictness=100).has_red(_icon_frame(RED))

    def test_gray_white_detected_across_strictness(self):
        # 中立灰白图标 V≈129 即使在严格模式也应命中（旧算法严格模式漏检）
        for s in (0, 50, 75, 100):
            assert Detector(strictness=s, colors=["gray_white"]).has_red(_icon_frame(GRAY))

    def test_strictness_clamped(self):
        assert Detector(strictness=999).strictness == 100
        assert Detector(strictness=-5).strictness == 0

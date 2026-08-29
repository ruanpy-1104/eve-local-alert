"""ROI 坐标换算单元测试：归一化 <-> 像素 往返一致。"""
from core.region_selector import ROI


class TestROI:
    def test_pixel_round_trip_within_1px(self):
        win = (100, 200, 1300, 920)          # 窗口客户区 (left, top, right, bottom)
        rect = (350, 300, 700, 500)          # 窗口内像素矩形
        roi = ROI.from_pixels(*rect, *win)
        back = roi.to_pixels(*win)
        for expected, actual in zip(rect, back):
            assert abs(expected - actual) < 1

    def test_to_capture_region(self):
        roi = ROI(0.1, 0.2, 0.3, 0.4)
        region = roi.to_capture_region(0, 0, 1000, 1000)
        assert region == {"left": 100, "top": 200, "width": 300, "height": 400}

    def test_full_window_bounds(self):
        roi = ROI(0.0, 0.0, 1.0, 1.0)
        assert roi.to_pixels(0, 0, 100, 100) == (0, 0, 100, 100)

    def test_clamp_to_zero_size(self):
        roi = ROI(0.5, 0.5, 0.0, 0.0)
        region = roi.to_capture_region(100, 100, 500, 500)
        assert region["width"] == 0
        assert region["height"] == 0

    def test_from_pixels_normalized_values(self):
        roi = ROI.from_pixels(200, 100, 600, 300, 0, 0, 1000, 500)
        assert roi.x == 0.2
        assert roi.y == 0.2
        assert abs(roi.width - 0.4) < 1e-9
        assert abs(roi.height - 0.4) < 1e-9

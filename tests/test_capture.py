"""Capture 纯函数测试（裁剪与钳制逻辑）。"""
import numpy as np

from core.capture import Capture


class TestCrop:
    def test_crop_subregion(self):
        frame = np.zeros((10, 20, 3), dtype=np.uint8)
        out = Capture._crop(frame, {"left": 5, "top": 2, "width": 8, "height": 4})
        assert out.shape == (4, 8, 3)

    def test_crop_clamps_beyond_edge(self):
        frame = np.zeros((10, 20, 3), dtype=np.uint8)
        out = Capture._crop(frame, {"left": 15, "top": 8, "width": 20, "height": 20})
        assert out.shape == (2, 5, 3)  # 越界自动钳制到剩余区域

    def test_crop_negative_clamped(self):
        frame = np.zeros((10, 20, 3), dtype=np.uint8)
        out = Capture._crop(frame, {"left": -5, "top": -3, "width": 10, "height": 10})
        assert out.shape == (10, 10, 3)

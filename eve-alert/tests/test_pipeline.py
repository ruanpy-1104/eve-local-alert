"""集成测试：config -> ROI -> Detector 全链路。

使用合成帧（不依赖真实屏幕与 Qt），验证项目自带 config.json 的默认参数
能够正确装配各模块，并对红名 / 非红名场景做出正确判定。
"""
import numpy as np

from core.config import ConfigManager
from core.detector import Detector
from core.region_selector import ROI


class FakeFrameSource:
    """按帧序返回合成画面：前 N 帧无红名，之后持续出现红色名牌。"""

    def __init__(self, appear_after: int = 5):
        self._appear_after = appear_after
        self.count = 0

    def next(self) -> np.ndarray:
        self.count += 1
        frame = np.zeros((288, 512, 3), dtype=np.uint8)
        if self.count > self._appear_after:
            frame[120:140, 180:380] = (0, 0, 255)  # 红色宽扁条（名牌形态）
        return frame


def _pipeline():
    cfg = ConfigManager("config.json")
    roi = ROI(**cfg.data["roi"])
    detector = Detector(**cfg.data["detection"])
    return cfg, roi, detector


def test_pipeline_detects_after_confirm():
    _, _, detector = _pipeline()
    source = FakeFrameSource(appear_after=5)
    hits = sum(1 for _ in range(30) if detector.detect(source.next()))
    assert hits >= 1  # 红名出现后，时序确认生效并触发


def test_pipeline_never_detects_blue():
    _, _, detector = _pipeline()
    blue = np.full((288, 512, 3), (255, 0, 0), dtype=np.uint8)
    assert not any(detector.detect(blue) for _ in range(10))


def test_config_feeds_detector_and_roi():
    cfg, roi, detector = _pipeline()
    assert roi.x == cfg.data["roi"]["x"]
    assert detector.confirm_frames == cfg.data["detection"]["confirm_frames"]
    region = roi.to_capture_region(100, 100, 2100, 1100)
    assert region["width"] > 0 and region["height"] > 0


def test_detector_rebuild_with_same_config_preserves_behavior():
    # 模拟校准器写回配置后 Detector 重建：判定行为一致
    cfg, _, _ = _pipeline()
    det_a = Detector(**cfg.data["detection"])
    det_b = Detector(**cfg.data["detection"])
    frame = np.zeros((288, 512, 3), dtype=np.uint8)
    frame[120:140, 180:380] = (0, 0, 255)
    assert det_a.has_red(frame) == det_b.has_red(frame)

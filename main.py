"""EVE Alert 程序入口。

默认启动 PySide6 控制面板（UI 模式）：框选监控区域、颜色选择、启动 / 停止监控。
`--cli` 参数启动无 UI 的最小闭环（原 P0 行为），供自动化验证使用。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from core.config import ConfigManager
from core.logger import get_logger, setup_logging
from core.paths import app_dir, assets_dir

logger = get_logger(__name__)

# Qt6 关闭高 DPI 坐标缩放，使 Qt 坐标与 win32 屏幕物理像素一致，
# 保证遮罩框选 / 窗口矩形 / 捕获区域三者坐标同源不偏移。
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

CONFIG_PATH = app_dir() / "config.json"


def _set_dpi_awareness() -> None:
    """声明 Per-Monitor V2 DPI 感知，保证 win32 窗口矩形使用物理像素。"""
    try:
        import ctypes

        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.shcore.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def run_headless(config_path: Path) -> None:
    """无 UI 最小闭环：定位窗口 -> 捕获 -> 检测 -> 警报。"""
    from core.alerter import Alerter
    from core.capture import Capture, client_size
    from core.config import ConfigManager
    from core.detector import Detector
    from core.region_selector import ROI
    from core.window_locator import WindowLocator

    cfg = ConfigManager(config_path)
    locator = WindowLocator(
        title_keyword=cfg.data["window"].get("title_keyword"),
        process_name=cfg.data["window"].get("process_name"),
    )
    win = locator.find()

    capture = Capture()
    roi = ROI(**cfg.data["roi"])
    detector = Detector(**cfg.data["detection"])
    sound_file = cfg.data["alert"].get("sound_file")
    if sound_file:
        sound_file = str((assets_dir() / sound_file).resolve())
    alerter = Alerter(sound_file)
    interval = 1.0 / cfg.data["loop"].get("fps", 12)

    alerting = False
    try:
        while True:
            started = time.perf_counter()
            cw, ch = client_size(win.handle)
            region = roi.to_capture_region(0, 0, cw, ch)
            frame = capture.grab_window(win.handle, region)
            if detector.detect(frame):
                if not alerting:
                    alerting = True
                    alerter.start()
            elif alerting:
                alerting = False
                alerter.stop()
            time.sleep(max(0.0, interval - (time.perf_counter() - started)))
    except KeyboardInterrupt:
        pass
    finally:
        alerter.stop()
        capture.close()


def run_ui(config_path: Path) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from ui.panel import ControlPanel
    from ui.theme import apply

    app = QApplication(sys.argv)
    # 统一深色主题：Fusion 风格 + 全局 QSS + 调色板，不随 Windows 深浅色模式切换而变化
    apply(app)
    app.setWindowIcon(_app_icon())
    panel = ControlPanel(config_path)
    panel.show()
    # 启动引导：选择目标程序 -> 框选监控区域
    QTimer.singleShot(0, panel.guide_target_selection)
    return app.exec()


def _app_icon():
    from PySide6.QtGui import QIcon

    icon = QIcon(str(assets_dir() / "assets" / "logo.png"))
    return icon if not icon.isNull() else QIcon()


def _install_crash_logging() -> None:
    """未捕获异常（闪退 / 崩溃）统一记录到日志，便于事后排查。"""

    def _hook(exc_type, exc_value, exc_tb):
        logger.critical(
            "程序未捕获异常（闪退），traceback：", exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = _hook


def main() -> None:
    _set_dpi_awareness()
    # 用配置初始化日志（仅记录异常 / 崩溃；级别 / 文件路径可经 config.json 的 logging 段调整）
    cfg = ConfigManager(CONFIG_PATH)
    log_cfg = cfg.data.get("logging", {})
    setup_logging(
        CONFIG_PATH.parent,
        level=log_cfg.get("level", "error"),
        log_file=log_cfg.get("file"),
    )
    _install_crash_logging()
    if "--cli" in sys.argv:
        run_headless(CONFIG_PATH)
    else:
        sys.exit(run_ui(CONFIG_PATH))


if __name__ == "__main__":
    main()

"""无人机提示音与急促报警播放器。

QtMultimedia 只在真正播放时延迟导入；无人机声音使用独立 QSoundEffect，
不会与敌袭使用的 winsound 相互打断。
"""
from __future__ import annotations

import os
from pathlib import Path

from core.logger import get_logger

logger = get_logger(__name__)


class SoundPlayer:
    """封装独立的无人机「叮咚」与循环报警音实例。"""

    def __init__(
        self,
        assets_root: Path,
        idle_sound: str = "assets/drone_idle.wav",
        alert_sound: str = "assets/drone_alert.wav",
    ):
        self._root = Path(assets_root)
        self._idle_sound = idle_sound
        self._alert_sound = alert_sound
        self._idle_effect = None
        self._alert_effect = None
        self._pending_idle = False
        self._pending_alert_loop = False
        self._pending_alert_once = False
        self._idle_missing_logged = False
        self._alert_missing_logged = False

    def _can_play(self) -> bool:
        """无应用或 offscreen 测试环境时保持完全静默。"""
        if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
            return False
        try:
            from PySide6.QtCore import QCoreApplication
        except Exception:
            logger.debug("无法导入 QtCore，跳过无人机音效", exc_info=True)
            return False
        return QCoreApplication.instance() is not None

    def _sound_path(self, relative: str) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else self._root / path

    def _ensure_effects(self) -> None:
        if self._idle_effect is not None and self._alert_effect is not None:
            return
        if not self._can_play():
            return
        if self._idle_effect is None:
            self._ensure_idle_effect()
        if self._alert_effect is None:
            self._ensure_alert_effect()

    def _ensure_idle_effect(self) -> None:
        """独立加载提示音；缺失或失败只跳过提示音。"""
        idle_path = self._sound_path(self._idle_sound)
        if not idle_path.is_file():
            if not self._idle_missing_logged:
                logger.warning("无人机提示音文件缺失：%s", idle_path)
                self._idle_missing_logged = True
            return
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect

            effect = QSoundEffect()
            self._idle_effect = effect
            effect.statusChanged.connect(self._on_idle_status_changed)
            effect.setSource(QUrl.fromLocalFile(str(idle_path.resolve())))
        except Exception:
            self._idle_effect = None
            logger.debug("创建无人机提示音实例失败", exc_info=True)

    def _ensure_alert_effect(self) -> None:
        """独立加载报警音；缺失或失败只跳过报警音。"""
        alert_path = self._sound_path(self._alert_sound)
        if not alert_path.is_file():
            if not self._alert_missing_logged:
                logger.warning("无人机报警音文件缺失：%s", alert_path)
                self._alert_missing_logged = True
            return
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect

            effect = QSoundEffect()
            self._alert_effect = effect
            effect.statusChanged.connect(self._on_alert_status_changed)
            effect.setSource(QUrl.fromLocalFile(str(alert_path.resolve())))
        except Exception:
            self._alert_effect = None
            logger.debug("创建无人机报警音实例失败", exc_info=True)

    @staticmethod
    def _is_ready(effect) -> bool:
        from PySide6.QtMultimedia import QSoundEffect

        return effect.status() == QSoundEffect.Status.Ready

    def _play_idle_now(self) -> None:
        self._idle_effect.setLoopCount(1)
        self._idle_effect.play()

    def _play_alert_now(self, loop: bool) -> None:
        if not loop:
            self._alert_effect.stop()
        self._alert_effect.setLoopCount(-2 if loop else 1)
        self._alert_effect.play()

    def _on_idle_status_changed(self, *_args) -> None:
        """音效源 Ready 后补播尚未执行的「叮咚」。"""
        try:
            if (
                self._pending_idle
                and self._idle_effect is not None
                and self._is_ready(self._idle_effect)
            ):
                self._pending_idle = False
                self._play_idle_now()
        except Exception:
            logger.debug("补播无人机提示音失败", exc_info=True)

    def _on_alert_status_changed(self, *_args) -> None:
        """音效源 Ready 后执行合并后的报警请求。"""
        try:
            if self._alert_effect is None or not self._is_ready(self._alert_effect):
                return
            if self._pending_alert_loop:
                self._pending_alert_loop = False
                self._pending_alert_once = False
                self._play_alert_now(True)
            elif self._pending_alert_once:
                self._pending_alert_once = False
                self._play_alert_now(False)
        except Exception:
            logger.debug("补播无人机报警失败", exc_info=True)

    def play_idle(self) -> None:
        """播放一次「叮咚」。"""
        try:
            self._ensure_effects()
            if self._idle_effect is None:
                return
            if self._is_ready(self._idle_effect):
                self._pending_idle = False
                self._play_idle_now()
            else:
                self._pending_idle = True
        except Exception:
            logger.debug("播放无人机提示音失败", exc_info=True)

    def start_alert(self) -> None:
        """循环播放急促报警，直到 stop_alert。"""
        try:
            self._ensure_effects()
            if self._alert_effect is None:
                return
            if self._is_ready(self._alert_effect):
                self._pending_alert_loop = False
                self._pending_alert_once = False
                self._play_alert_now(True)
            else:
                self._pending_alert_loop = True
                self._pending_alert_once = False
        except Exception:
            logger.debug("播放无人机循环报警失败", exc_info=True)

    def stop_alert(self) -> None:
        """立即停止无人机循环报警。"""
        self._pending_alert_loop = False
        self._pending_alert_once = False
        try:
            if self._alert_effect is not None:
                self._alert_effect.stop()
        except Exception:
            logger.debug("停止无人机循环报警失败", exc_info=True)

    def play_alert_once(self) -> None:
        """试听：播放一轮报警音后自然结束。"""
        try:
            self._ensure_effects()
            if self._alert_effect is None:
                return
            if self._is_ready(self._alert_effect):
                self._pending_alert_loop = False
                self._pending_alert_once = False
                self._play_alert_now(False)
            elif not self._pending_alert_loop:
                self._pending_alert_once = True
        except Exception:
            logger.debug("试听无人机报警失败", exc_info=True)

    def close(self) -> None:
        """停止并释放两个音效实例。"""
        self._pending_idle = False
        self.stop_alert()
        try:
            if self._idle_effect is not None:
                self._idle_effect.stop()
        except Exception:
            logger.debug("关闭无人机提示音失败", exc_info=True)
        self._idle_effect = None
        self._alert_effect = None

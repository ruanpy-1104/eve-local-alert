"""警报模块。

识别到目标即持续播放警报音，直到目标消失才停止（无冷却防抖）。
"""
from __future__ import annotations

import os
import winsound

from core.logger import get_logger

logger = get_logger(__name__)


class Alerter:
    """持续警报器：start 循环播放，stop 停止。"""

    def __init__(self, sound_file: str | None = None):
        self.sound_file = sound_file
        self._playing = False

    def is_active(self) -> bool:
        return self._playing

    def start(self) -> None:
        """开始警报：循环播放警报音（已播放中则忽略）。"""
        if self._playing:
            return
        self._playing = True
        try:
            if self.sound_file and os.path.exists(self.sound_file):
                winsound.PlaySound(
                    self.sound_file,
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP,
                )
            else:
                # 无警报音文件时退化为系统蜂鸣
                winsound.Beep(1000, 500)
        except Exception:
            # 播放失败时保持尽力而为，不中断监控流程
            logger.exception("警报音播放失败")
            self._playing = False

    def stop(self) -> None:
        """停止警报。"""
        if not self._playing:
            return
        self._playing = False
        winsound.PlaySound(None, winsound.SND_PURGE)

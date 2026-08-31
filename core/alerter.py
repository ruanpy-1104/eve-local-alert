"""警报模块。

识别到目标即持续播放警报音，直到目标消失才停止（无冷却防抖）。
播放采用「按周期播放」：
- start：播完一个完整周期（wav 时长）后，若仍处于警报态则自动续播下一周期，等效连续警报；
- stop：仅置位停止标志，当前周期自然放完即静音——不再用 SND_PURGE 立即截断产生爆音。
"""
from __future__ import annotations

import os
import threading
import wave
import winsound

from core.logger import get_logger

logger = get_logger(__name__)

# wav 时长解析失败（非标准文件 / 用户替换为其它格式）时的周期兜底时长
_FALLBACK_CYCLE_SECONDS = 1.0


def _wav_duration(path: str) -> float | None:
    """读取 wav 文件时长（秒）；非标准 wav 返回 None。"""
    try:
        with wave.open(path, "rb") as w:
            rate = w.getframerate()
            return w.getnframes() / rate if rate > 0 else None
    except Exception:
        return None


class Alerter:
    """持续警报器：按周期播放，start 循环续播，stop 当前周期自然放完即停。"""

    def __init__(self, sound_file: str | None = None):
        self.sound_file = sound_file
        if sound_file and os.path.exists(sound_file):
            self.cycle_seconds = _wav_duration(sound_file) or _FALLBACK_CYCLE_SECONDS
        else:
            self.cycle_seconds = _FALLBACK_CYCLE_SECONDS
        self._playing = False
        self._lock = threading.Lock()
        self._generation = 0  # start/stop 时递增，用于使在途周期线程过期

    def is_active(self) -> bool:
        return self._playing

    def start(self) -> None:
        """开始警报：播放一个完整周期，周期结束若仍警报则续播（已在播放则忽略）。"""
        with self._lock:
            if self._playing:
                return
            if not (self.sound_file and os.path.exists(self.sound_file)):
                # 无警报音文件时退化为系统蜂鸣（同步播完，等效一个周期）
                try:
                    winsound.Beep(1000, 500)
                except Exception:
                    logger.exception("警报音播放失败")
                return
            self._playing = True
            self._generation += 1
            gen = self._generation
        self._play(gen)

    def stop(self) -> None:
        """停止警报：当前周期自然放完即静音，不截断。"""
        with self._lock:
            if not self._playing:
                return
            self._playing = False
            self._generation += 1  # 使在途周期线程失效

    def _play(self, gen: int) -> None:
        """播放一个完整周期（不循环），周期结束后由 _cycle_done 决定是否续播。"""
        winsound.PlaySound(self.sound_file, winsound.SND_FILENAME | winsound.SND_ASYNC)
        timer = threading.Timer(self.cycle_seconds, self._cycle_done, args=(gen,))
        timer.daemon = True
        timer.start()

    def _cycle_done(self, gen: int) -> None:
        with self._lock:
            continue_alerting = self._playing and gen == self._generation
        if continue_alerting:
            self._play(gen)
        # 否则：当前周期已自然放完，静音结束

"""生成可重复输出的无人机 WAV 音效。

输出统一为 44100 Hz、16 bit、单声道。脚本不含随机数或时间戳，
重复执行会得到逐字节一致的文件。
"""
from __future__ import annotations

import math
import os
import sys
import wave
from pathlib import Path

import numpy as np

RATE = 44_100


def _fade_and_scale(
    samples: np.ndarray, peak_seconds: float = 0.002, tail_seconds: float = 0.005
) -> np.ndarray:
    """增加短淡入淡出，并把峰值缩放到指定 dBFS。"""
    samples = samples.astype(np.float64, copy=True)
    if samples.size:
        fade_in = min(samples.size, max(1, int(RATE * peak_seconds)))
        fade_out = min(samples.size, max(1, int(RATE * tail_seconds)))
        samples[:fade_in] *= np.linspace(0.0, 1.0, fade_in, endpoint=False)
        samples[-fade_out:] *= np.linspace(1.0, 0.0, fade_out, endpoint=True)
        peak = float(np.max(np.abs(samples)))
        if peak > 0:
            samples *= (10 ** (-9.0 / 20.0) * 32767.0) / peak
    return samples


def _note(frequency: float, seconds: float, decay_seconds: float) -> np.ndarray:
    count = int(round(RATE * seconds))
    time_axis = np.arange(count, dtype=np.float64) / RATE
    envelope = np.exp(-time_axis / decay_seconds)
    return np.sin(2.0 * math.pi * frequency * time_axis) * envelope


def _idle_chime() -> np.ndarray:
    """880 Hz 160 ms + 660 Hz 260 ms，指数衰减，峰值约 -9 dBFS。"""
    samples = np.concatenate(
        (
            _note(880.0, 0.160, 0.090),
            _note(660.0, 0.260, 0.140),
        )
    )
    return _fade_and_scale(samples, peak_seconds=0.002, tail_seconds=0.008)


def _drone_alert() -> np.ndarray:
    """4 次 1500 Hz / 80 ms 响、50 ms 停，总长约 0.5 秒。"""
    samples = np.zeros(int(round(RATE * 0.500)), dtype=np.float64)
    burst_count = int(round(RATE * 0.080))
    cycle_count = int(round(RATE * 0.125))
    burst_t = np.arange(burst_count, dtype=np.float64) / RATE
    burst = np.sin(2.0 * math.pi * 1500.0 * burst_t)
    fade_in = int(RATE * 0.003)
    fade_out = int(RATE * 0.006)
    burst[:fade_in] *= np.linspace(0.0, 1.0, fade_in, endpoint=False)
    burst[-fade_out:] *= np.linspace(1.0, 0.0, fade_out, endpoint=True)
    for index in range(4):
        start = index * cycle_count
        end = min(start + burst_count, samples.size)
        samples[start:end] = burst[: end - start]
    # 报警音统一缩放到约 -6 dBFS；仍保持短促、无削波。
    peak = float(np.max(np.abs(samples)))
    if peak > 0:
        samples *= (10 ** (-6.0 / 20.0) * 32767.0) / peak
    return samples


def _write_wav(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(np.rint(samples), -32768, 32767).astype("<i2")
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with wave.open(str(temp_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(pcm.tobytes())
    os.replace(temp_path, path)


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    assets = root / "assets"
    outputs = {
        assets / "drone_idle.wav": _idle_chime(),
        assets / "drone_alert.wav": _drone_alert(),
    }
    for path, samples in outputs.items():
        _write_wav(path, samples)
        print(path.relative_to(root))
    # 固定结果不依赖平台默认编码或 stderr。
    sys.stdout.flush()


if __name__ == "__main__":
    main()

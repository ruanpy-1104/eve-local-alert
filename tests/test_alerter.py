"""Alerter 测试：按周期播放（start 续播，stop 当前周期自然放完即停，无截断）。"""
import time
import wave

from core.alerter import Alerter


def _make_wav(tmp_path):
    """非标准 wav（无法解析时长，走兜底周期）。"""
    wav = tmp_path / "alert.wav"
    wav.write_bytes(b"RIFF")
    return str(wav)


def _make_short_wav(tmp_path, seconds=0.1):
    """生成有效短 wav，便于测试周期续播 / 停止。"""
    wav = tmp_path / "alert.wav"
    rate = 8000
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return str(wav)


class TestAlerter:
    def test_start_plays_cycle_without_loop(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr("core.alerter.winsound.PlaySound", lambda *a: calls.append(a))
        a = Alerter(_make_wav(tmp_path))
        assert not a.is_active()
        a.start()
        assert a.is_active()
        flags = calls[-1][1]
        assert flags & 0x1  # SND_ASYNC
        assert not (flags & 0x8)  # 不再使用 SND_LOOP（按周期播放）
        a.stop()
        assert not a.is_active()
        # stop 不调用 SND_PURGE（当前周期自然放完），全部调用都有文件名
        assert all(c[0] is not None for c in calls)

    def test_double_start_plays_once(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr("core.alerter.winsound.PlaySound", lambda *a: calls.append(a))
        a = Alerter(_make_wav(tmp_path))
        a.start()
        a.start()  # 重复 start 不重复播放
        assert len(calls) == 1
        a.stop()

    def test_cycle_continues_while_alerting(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr("core.alerter.winsound.PlaySound", lambda *a: calls.append(a))
        a = Alerter(_make_short_wav(tmp_path))
        a.start()
        time.sleep(0.3)  # 跨过多个周期（0.1s/个）
        assert len(calls) >= 2  # 仍处于警报态 → 周期结束自动续播
        a.stop()
        before = len(calls)
        time.sleep(0.35)  # 停止后不再续播，也无 SND_PURGE
        assert len(calls) == before
        assert not a.is_active()

    def test_stop_without_start_noop(self, monkeypatch):
        monkeypatch.setattr("core.alerter.winsound.PlaySound", lambda *a: None)
        a = Alerter(None)
        a.stop()  # 未播放时 stop 不应报错

    def test_missing_sound_file_beeps(self, monkeypatch):
        beeps = []
        monkeypatch.setattr("core.alerter.winsound.Beep", lambda *a: beeps.append(a))
        a = Alerter("不存在.wav")
        a.start()
        assert beeps  # 无文件时退化为系统蜂鸣
        a.stop()

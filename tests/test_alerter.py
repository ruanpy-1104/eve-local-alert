"""Alerter 测试：连续警报（识别到即循环播放，未识别停止，无冷却）。"""
from core.alerter import Alerter


def _make_wav(tmp_path):
    wav = tmp_path / "alert.wav"
    wav.write_bytes(b"RIFF")
    return str(wav)


class TestAlerter:
    def test_start_loops_sound(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr("core.alerter.winsound.PlaySound", lambda *a: calls.append(a))
        a = Alerter(_make_wav(tmp_path))
        assert not a.is_active()
        a.start()
        assert a.is_active()
        assert calls[-1][1] & 0x8  # SND_LOOP
        a.stop()
        assert not a.is_active()
        assert calls[-1][0] is None  # SND_PURGE

    def test_double_start_plays_once(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr("core.alerter.winsound.PlaySound", lambda *a: calls.append(a))
        a = Alerter(_make_wav(tmp_path))
        a.start()
        a.start()  # 重复 start 不重复播放
        assert len(calls) == 1
        a.stop()

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

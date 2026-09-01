"""Notifier 测试：Server酱 推送请求构造、结果解析、冷却防抖、异步触发条件。"""
import json
import threading
from urllib.parse import parse_qs

import pytest

from core import notifier
from core.notifier import send_alert_async, send_serverchan


@pytest.fixture(autouse=True)
def _isolated_cooldown():
    """每个用例前清零模块级冷却状态，避免用例间相互抑制。"""
    notifier.reset_cooldown()
    yield
    notifier.reset_cooldown()


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._payload


class TestSendServerchan:
    def test_posts_to_api_with_title_and_desp(self, monkeypatch):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["data"] = request.data.decode("utf-8")
            captured["timeout"] = timeout
            return _FakeResponse(json.dumps({"code": 0}).encode("utf-8"))

        monkeypatch.setattr(notifier.urllib.request, "urlopen", fake_urlopen)
        ok, message = send_serverchan("SCT_test_key", "标题", "内容")
        assert ok and message == "已发送"
        assert captured["url"] == "https://sctapi.ftqq.com/SCT_test_key.send"
        form = parse_qs(captured["data"])
        assert form["title"] == ["标题"]
        assert form["desp"] == ["内容"]
        assert captured["timeout"] == notifier._HTTP_TIMEOUT_SECONDS

    def test_api_error_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            notifier.urllib.request,
            "urlopen",
            lambda *a, **k: _FakeResponse(
                json.dumps({"code": 40001, "message": "bad key"}).encode("utf-8")
            ),
        )
        ok, message = send_serverchan("SCT_x", "t", "d")
        assert not ok and "40001" in message and "bad key" in message

    def test_network_error_returns_false(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("unreachable")

        monkeypatch.setattr(notifier.urllib.request, "urlopen", boom)
        ok, message = send_serverchan("SCT_x", "t", "d")
        assert not ok and "unreachable" in message

    def test_empty_sendkey_rejected_without_request(self, monkeypatch):
        monkeypatch.setattr(
            notifier.urllib.request,
            "urlopen",
            lambda *a, **k: pytest.fail("不应发起请求"),
        )
        ok, _ = send_serverchan("  ", "t", "d")
        assert not ok


class TestSendAlertAsync:
    def test_disabled_or_empty_config_never_sends(self, monkeypatch):
        for cfg in (None, {}, {"enabled": False, "sendkey": "SCT_x"}, {"enabled": True, "sendkey": ""}):
            monkeypatch.setattr(
                notifier,
                "send_serverchan",
                lambda *a, **k: pytest.fail("不应发起请求"),
            )
            send_alert_async(cfg)  # 不抛异常、不产生线程请求

    def _wait_called(self, called: threading.Event) -> None:
        assert called.wait(timeout=5), "异步发送未执行"

    def _rollback_sent(self, seconds: float) -> None:
        """把上次发送时刻回拨，模拟冷却时间已流逝。"""
        with notifier._cooldown_lock:
            notifier._last_sent_monotonic -= seconds

    def test_enabled_sends_in_daemon_thread(self, monkeypatch):
        called = threading.Event()
        captured = {}

        def fake_send(sendkey, title, desp):
            captured["sendkey"] = sendkey
            captured["title"] = title
            called.set()
            return True, "已发送"

        monkeypatch.setattr(notifier, "send_serverchan", fake_send)
        send_alert_async({"enabled": True, "sendkey": "SCT_key"})
        self._wait_called(called)
        assert captured["sendkey"] == "SCT_key"
        assert captured["title"]  # 警报标题非空

    def test_builtin_5s_cooldown_blocks_rapid_resend(self, monkeypatch):
        calls = []
        called = threading.Event()

        def fake_send(sendkey, title, desp):
            calls.append(sendkey)
            called.set()
            return True, "已发送"

        monkeypatch.setattr(notifier, "send_serverchan", fake_send)
        cfg = {"enabled": True, "sendkey": "SCT_key"}
        send_alert_async(cfg)
        self._wait_called(called)
        notifier.mark_enemy_gone()  # 敌方已消失，但 5 秒内置冷却未过
        send_alert_async(cfg)
        threading.Event().wait(0.2)
        assert len(calls) == 1

    def test_requires_enemy_gone_even_after_5s(self, monkeypatch):
        calls = []
        gate = threading.Event()

        def fake_send(sendkey, title, desp):
            calls.append(sendkey)
            gate.set()
            return True, "已发送"

        monkeypatch.setattr(notifier, "send_serverchan", fake_send)
        cfg = {"enabled": True, "sendkey": "SCT_key"}
        send_alert_async(cfg)
        gate.wait(timeout=5)
        # 5 秒内置冷却已过，但敌方始终未消失（未上报未命中）：仍被抑制
        self._rollback_sent(10.0)
        send_alert_async(cfg)
        threading.Event().wait(0.2)
        assert len(calls) == 1
        # 敌方消失后：双条件同时满足，才再次发送
        notifier.mark_enemy_gone()
        send_alert_async(cfg)
        assert gate.wait(timeout=5) and len(calls) == 2

    def test_user_cooldown_checked_first(self, monkeypatch):
        calls = []
        gate = threading.Event()

        def fake_send(sendkey, title, desp):
            calls.append(sendkey)
            gate.set()
            return True, "已发送"

        monkeypatch.setattr(notifier, "send_serverchan", fake_send)
        cfg = {
            "enabled": True,
            "sendkey": "SCT_key",
            "cooldown_enabled": True,
            "cooldown_minutes": 5,
        }
        send_alert_async(cfg)
        gate.wait(timeout=5)
        # 内置条件已满足（5 秒已过 + 敌方已消失），但用户 5 分钟冷却优先：仍被抑制
        self._rollback_sent(10.0)
        notifier.mark_enemy_gone()
        send_alert_async(cfg)
        threading.Event().wait(0.2)
        assert len(calls) == 1

    def test_user_cooldown_released_after_interval(self, monkeypatch):
        calls = []
        gate = threading.Event()

        def fake_send(sendkey, title, desp):
            calls.append(sendkey)
            gate.set()
            return True, "已发送"

        monkeypatch.setattr(notifier, "send_serverchan", fake_send)
        cfg = {
            "enabled": True,
            "sendkey": "SCT_key",
            "cooldown_enabled": True,
            "cooldown_minutes": 1,
        }
        send_alert_async(cfg)
        gate.wait(timeout=5)
        # 用户冷却（1 分钟）已过 + 内置 5 秒已过 + 敌方已消失：允许再次发送
        self._rollback_sent(120.0)
        notifier.mark_enemy_gone()
        send_alert_async(cfg)
        assert gate.wait(timeout=5) and len(calls) == 2

    # ---- 用户澄清的内置冷却语义（5 秒从「推送消息后」起算，而非敌方消失后）----
    def test_example1_trigger_7s_after_send(self, monkeypatch):
        """例一：推送后敌方停留 6 秒消失、再过 1 秒出现（距推送 7 秒）→ 触发。

        关键点：5 秒冷却从推送时刻起算，敌方停留期间（持续命中）不重置它。
        """
        calls = []
        gate = threading.Event()

        def fake_send(sendkey, title, desp):
            calls.append(sendkey)
            gate.set()
            return True, "已发送"

        monkeypatch.setattr(notifier, "send_serverchan", fake_send)
        cfg = {"enabled": True, "sendkey": "SCT_key"}
        send_alert_async(cfg)  # t=0 推送
        gate.wait(timeout=5)
        notifier.mark_enemy_gone()  # t=6 敌方消失
        self._rollback_sent(7.0)  # t=7 敌方再次出现
        send_alert_async(cfg)
        assert gate.wait(timeout=5) and len(calls) == 2

    def test_example2_no_trigger_3s_after_send(self, monkeypatch):
        """例二：推送后敌方停留 2 秒消失、再过 1 秒出现（距推送 3 秒）→ 不触发。

        敌方已消失（条件二满足），但距推送不足 5 秒（条件一不满足）。
        """
        calls = []
        called = threading.Event()

        def fake_send(sendkey, title, desp):
            calls.append(sendkey)
            called.set()
            return True, "已发送"

        monkeypatch.setattr(notifier, "send_serverchan", fake_send)
        cfg = {"enabled": True, "sendkey": "SCT_key"}
        send_alert_async(cfg)  # t=0 推送
        called.wait(timeout=5)
        notifier.mark_enemy_gone()  # t=2 敌方消失
        self._rollback_sent(3.0)  # t=3 敌方再次出现
        send_alert_async(cfg)
        threading.Event().wait(0.2)
        assert len(calls) == 1

    def test_example3_trigger_7s_after_send(self, monkeypatch):
        """例三：推送后敌方停留 2 秒消失、再过 5 秒出现（距推送 7 秒）→ 触发。"""
        calls = []
        gate = threading.Event()

        def fake_send(sendkey, title, desp):
            calls.append(sendkey)
            gate.set()
            return True, "已发送"

        monkeypatch.setattr(notifier, "send_serverchan", fake_send)
        cfg = {"enabled": True, "sendkey": "SCT_key"}
        send_alert_async(cfg)  # t=0 推送
        gate.wait(timeout=5)
        notifier.mark_enemy_gone()  # t=2 敌方消失
        self._rollback_sent(7.0)  # t=7 敌方再次出现（消失 5 秒后）
        send_alert_async(cfg)
        assert gate.wait(timeout=5) and len(calls) == 2

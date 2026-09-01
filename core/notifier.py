"""远程预警通知模块（Server酱 微信推送）。

开启远程预警后，警报触发时向用户自己的微信推送一条提醒（经 Server酱 服务，
仅需一个 HTTP 请求，无需额外依赖）：

- **状态变化才发送**：仅在本轮警报开始（目标命中并触发警报）时发一条，
  持续命中期间不重复发送；由调用方（MonitorWorker / CLI 闭环）在警报
  False -> True 的状态转换处调用，天然去重。
- **内置双条件冷却**：再次发送必须**同时满足**——① 距上次发送 ≥ 5 秒；
  ② 敌方已消失（本帧未命中，调用方经 `mark_enemy_gone()` 上报）。
  两者都满足后才允许再次发送，避免目标短暂闪烁反复推送。
- **用户冷却独立优先**：若用户开启了冷却间隔（`cooldown_enabled`，默认 5 分钟，
  免费版每日 5 条额度建议开启），先判定用户冷却，其次才判定内置双条件；
  两者互不影响，用户等待时间不会被内置 5 秒缩短或延长。
- **尽力而为**：发送在独立 daemon 线程执行，短超时；失败仅记录日志，
  不影响本地警报音，也不阻塞监控循环。
- **默认关闭**：remote_alert.enabled 为 false 时不产生任何网络请求，
  保持「默认完全本地」的产品定位。

SendKey 保存在本地 config.json 中（明文），仅用于调用 Server酱 接口。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import datetime
from urllib.parse import urlencode

from core.logger import get_logger

logger = get_logger(__name__)

# Server酱 推送接口（sendkey 即鉴权凭证）
SERVERCHAN_API_URL = "https://sctapi.ftqq.com/{key}.send"
# Server酱 注册 / 登录页（推广计划链接），供 UI 引导首次使用的用户获取 SendKey
SERVERCHAN_REGISTER_URL = "https://sct.ftqq.com/r/3353"

# 短超时：远程提醒是尽力而为的增强功能，不能拖慢监控循环
_HTTP_TIMEOUT_SECONDS = 5
# 内置冷却（秒）：两次远程提醒的最小间隔，与用户冷却独立，始终生效
_BUILTIN_COOLDOWN_SECONDS = 5.0

_ALTER_TITLE = "EVE Local Alert 警报"
_ALERT_DESP = "检测到敌方目标，请尽快回到电脑前查看！"

# 冷却状态：以「发起发送」时刻计，无论成败本窗口内不再推送（防失败重试轰炸）
_cooldown_lock = threading.Lock()
_last_sent_monotonic = 0.0
_enemy_gone_since_send = True  # 上次发送后敌方是否已消失（未命中）；初始允许首次发送


def reset_cooldown() -> None:
    """清零冷却与敌方消失状态（仅供单元测试隔离使用）。"""
    global _last_sent_monotonic, _enemy_gone_since_send
    with _cooldown_lock:
        _last_sent_monotonic = 0.0
        _enemy_gone_since_send = True


def mark_enemy_gone() -> None:
    """上报敌方已消失（本帧未命中）。

    内置冷却条件之二：目标消失（出现未命中帧）后，才允许再次触发远程提醒。
    调用方在每个未命中帧调用（幂等）。
    """
    global _enemy_gone_since_send
    with _cooldown_lock:
        _enemy_gone_since_send = True


def send_serverchan(sendkey: str, title: str, desp: str = "") -> tuple[bool, str]:
    """向 Server酱 发送一条消息，返回 (是否成功, 结果描述)。

    同步阻塞调用（内部短超时），异步场景请用 :func:`send_alert_async`。
    """
    key = (sendkey or "").strip()
    if not key:
        return False, "未配置 SendKey"
    url = SERVERCHAN_API_URL.format(key=key)
    data = urlencode({"title": title, "desp": desp}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001  网络层任何失败都不应影响本地警报
        return False, f"网络错误：{exc}"
    code = payload.get("code")
    if code == 0:
        return True, "已发送"
    return False, f"Server酱错误（code={code}）：{payload.get('message', '')}"


def send_alert_async(remote_cfg: dict) -> None:
    """警报触发时发送远程提醒（独立 daemon 线程，立即返回）。

    判定在调用线程内完成（顺序）：
    1. 用户冷却（cooldown_enabled 开启时，取 cooldown_minutes）；
    2. 内置双条件——距上次发送 ≥ 5 秒，且敌方已消失（mark_enemy_gone 上报）。
    任一不满足即跳过，不产生网络请求。
    :param remote_cfg: config.json 的 remote_alert 配置节
        （``{"enabled", "sendkey", "cooldown_enabled", "cooldown_minutes"}``）。
    """
    global _last_sent_monotonic, _enemy_gone_since_send
    if not remote_cfg or not remote_cfg.get("enabled"):
        return
    sendkey = str(remote_cfg.get("sendkey") or "")
    if not sendkey.strip():
        logger.warning("远程预警已开启但未配置 SendKey，跳过远程提醒")
        return

    now = time.monotonic()
    with _cooldown_lock:
        # 第一步：免费用户冷却优先判定，与内置条件互不影响
        if remote_cfg.get("cooldown_enabled"):
            user_interval = max(
                0.0, float(remote_cfg.get("cooldown_minutes", 5) or 0) * 60.0
            )
            if now - _last_sent_monotonic < user_interval:
                logger.info("远程提醒处于用户冷却中（%.0f 秒），本次跳过", user_interval)
                return
        # 第二步：内置双条件——5 秒冷却 + 敌方已消失，须同时满足
        if now - _last_sent_monotonic < _BUILTIN_COOLDOWN_SECONDS:
            logger.info("远程提醒处于 5 秒冷却中，本次跳过")
            return
        if not _enemy_gone_since_send:
            logger.info("敌方尚未消失（未上报未命中），本次跳过远程提醒")
            return
        _last_sent_monotonic = now
        _enemy_gone_since_send = False

    desp = f"{_ALERT_DESP}\n\n触发时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    def _work() -> None:
        ok, message = send_serverchan(sendkey, _ALTER_TITLE, desp)
        if ok:
            logger.info("远程提醒已发送")
        else:
            logger.warning("远程提醒发送失败：%s", message)

    threading.Thread(target=_work, daemon=True, name="remote-alert").start()

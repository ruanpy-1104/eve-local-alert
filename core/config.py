"""配置管理模块。

负责 config.json 的读取、与默认值深度合并以及写回，供主入口与控制面板共用。
所有字段均为本地配置；唯一的网络项是远程预警的 Server酱 SendKey
（remote_alert.sendkey），仅在用户显式开启远程预警后使用。
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from core.colors import DEFAULT_ALERT_COLORS, EVE_COLORS
from core.logger import get_logger

logger = get_logger(__name__)

# 默认配置：旧版 config.json 缺字段时，通过深度合并自动补全
DEFAULT_CONFIG: dict[str, Any] = {
    "window": {"title_keyword": "EVE", "process_name": "exefile.exe"},
    "roi": {"x": 0.10, "y": 0.10, "width": 0.30, "height": 0.40},
    "detection": {
        # 图标面积/宽高比/填充率等几何约束改为检测器内部自适应，不再暴露为配置项
        "confirm_frames": 3,
        "downscale": 2,
        # 警报颜色：4 种总览预设色（红 / 橙 / 黄 / 灰白），默认全部启用
        "colors": list(DEFAULT_ALERT_COLORS),
        # 兼容模式只调整对比度、边缘与颜色阈值，不改变方格和网格结构判定
        "compat": "auto",
    },
    "alert": {
        "sound_file": "assets/alert.wav",
        # 暂停预警至少持续多少秒，且目标不在视野时才自动恢复（0 表示黑屏即恢复）
        "resume_delay": 10,
    },
    # 远程预警（Server酱 微信推送）：每次启动都重置为关闭（见 _load），
    # 其余选项（SendKey / 冷却）保留用户最后一次修改。
    # cooldown_minutes 为可选冷却间隔（分钟）；免费版每日 5 条额度，建议开启冷却
    "remote_alert": {
        "enabled": False,
        "sendkey": "",
        "cooldown_enabled": False,
        "cooldown_minutes": 5,
    },
    "loop": {"fps": 6},
    "logging": {"level": "error", "file": "logs/eve-alert.log"},
    "drone": {
        "enabled": False,
        "idle_delay": 30,
        "roi": {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0},
        "idle_sound": "assets/drone_idle.wav",
        "alert_sound": "assets/drone_alert.wav",
    },
    "overlay": {"visible": False, "locked": False, "x": None, "y": None},
}

# 旧版 config.json 中已废弃的检测字段（文字条形检测遗留），加载时自动剔除，避免传入 Detector 报错
_OBSOLETE_DETECTION_KEYS = ("min_area", "min_aspect_ratio")

_VALID_COMPAT = ("auto", "low_contrast", "strict")
_EMPTY_ROI = {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}


def _compat_from_strictness(value: object) -> str:
    """把旧识别程度迁移为兼容模式。"""
    try:
        strictness = int(value)
    except (TypeError, ValueError, OverflowError):
        return "auto"
    if strictness <= 25:
        return "low_contrast"
    if strictness >= 75:
        return "strict"
    return "auto"


def _normalize_detection(detection: dict) -> dict:
    for key in _OBSOLETE_DETECTION_KEYS:
        detection.pop(key, None)
    strictness = detection.pop("strictness", None)
    compat = detection.get("compat")
    if compat not in _VALID_COMPAT:
        compat = _compat_from_strictness(strictness)
    detection["compat"] = compat
    return detection


def _normalize_colors(value: object) -> list[str]:
    """过滤已移除 / 未知的颜色键，去重保序；结果为空时回退默认四色。

    旧版 config.json 可能保存着已移除的颜色（green / blue / magenta / black ...）：
    这些键会被检测器安静忽略，若全被忽略则掩码恒为空、警报永不触发。故在此收敛，
    并保证至少保留一种颜色（面板取消全部勾选时同样由此兜底）。
    """
    if not isinstance(value, (list, tuple)):
        return list(DEFAULT_ALERT_COLORS)
    kept: list[str] = []
    for name in value:
        if isinstance(name, str) and name in EVE_COLORS and name not in kept:
            kept.append(name)
    return kept or list(DEFAULT_ALERT_COLORS)


def _normalize_idle_delay(value: object) -> int:
    """把无人机空闲提示延时收敛到 1–120 秒；非法值回退默认 30。"""
    if isinstance(value, bool):
        return 30
    try:
        idle_delay = int(value)
    except (TypeError, ValueError, OverflowError):
        return 30
    return max(1, min(120, idle_delay))


def _normalize_drone_roi(value: object) -> dict[str, float]:
    """规整无人机 ROI；任一字段非法或越界即视为未框选。"""
    if not isinstance(value, dict):
        return dict(_EMPTY_ROI)
    numbers: list[float] = []
    for key in ("x", "y", "width", "height"):
        item = value.get(key)
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return dict(_EMPTY_ROI)
        number = float(item)
        if not math.isfinite(number):
            return dict(_EMPTY_ROI)
        numbers.append(number)
    x, y, width, height = numbers
    if (
        x < 0.0
        or y < 0.0
        or width <= 0.0
        or height <= 0.0
        or x + width > 1.0 + 1e-9
        or y + height > 1.0 + 1e-9
    ):
        return dict(_EMPTY_ROI)
    return {"x": x, "y": y, "width": width, "height": height}


def _normalize_drone(drone: object) -> dict[str, Any]:
    """补全并规整 drone 配置，不改变其它配置节。"""
    current = drone if isinstance(drone, dict) else {}
    enabled = current.get("enabled", False)
    idle_sound = current.get("idle_sound", DEFAULT_CONFIG["drone"]["idle_sound"])
    alert_sound = current.get("alert_sound", DEFAULT_CONFIG["drone"]["alert_sound"])
    return {
        "enabled": enabled if isinstance(enabled, bool) else False,
        "idle_delay": _normalize_idle_delay(current.get("idle_delay", 30)),
        "roi": _normalize_drone_roi(current.get("roi")),
        "idle_sound": (
            idle_sound
            if isinstance(idle_sound, str) and idle_sound
            else DEFAULT_CONFIG["drone"]["idle_sound"]
        ),
        "alert_sound": (
            alert_sound
            if isinstance(alert_sound, str) and alert_sound
            else DEFAULT_CONFIG["drone"]["alert_sound"]
        ),
    }


def _normalize_overlay(overlay: object) -> dict[str, Any]:
    """规整悬浮窗持久化字段；坐标只接受整数或 null。"""
    current = overlay if isinstance(overlay, dict) else {}

    def _coordinate(key: str) -> int | None:
        value = current.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    visible = current.get("visible", False)
    locked = current.get("locked", False)
    return {
        "visible": visible if isinstance(visible, bool) else False,
        "locked": locked if isinstance(locked, bool) else False,
        "x": _coordinate("x"),
        "y": _coordinate("y"),
    }


def _normalize_extended_sections(data: dict[str, Any]) -> None:
    """统一补齐 v1.2 新增配置，兼容缺节与旧类型数据。"""
    data["drone"] = _normalize_drone(data.get("drone"))
    data["overlay"] = _normalize_overlay(data.get("overlay"))


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并：override 覆盖 base；双方共有的 dict 递归合并。"""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class ConfigManager:
    """配置读写：加载时与默认值深度合并，保存时格式化写回 JSON。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            data = copy.deepcopy(DEFAULT_CONFIG)
            _normalize_extended_sections(data)
            return data
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            loaded_detection = loaded.get("detection")
            if isinstance(loaded_detection, dict):
                # 迁移必须在深度合并前识别旧 strictness；否则默认 compat=auto
                # 会被误认为用户已显式选择 auto。
                _normalize_detection(loaded_detection)
            data = _deep_merge(DEFAULT_CONFIG, loaded)
        except Exception:
            # 配置文件损坏 / 不可读 / 结构非法时回退默认配置，避免启动即崩溃
            logger.error("config.json 读取失败，使用默认配置", exc_info=True)
            return copy.deepcopy(DEFAULT_CONFIG)
        if isinstance(data.get("detection"), dict):
            _normalize_detection(data["detection"])
            # 警报颜色收敛到 4 色预设：旧配置里已移除的颜色键在此被剔除（见函数注释）
            data["detection"]["colors"] = _normalize_colors(
                data["detection"].get("colors")
            )
        # 远程预警每次启动都默认不开启（enabled 强制置 False），
        # 其余选项保留用户最后一次修改——避免用户忘记关闭导致意外联网推送。
        if isinstance(data.get("remote_alert"), dict):
            data["remote_alert"]["enabled"] = False
        _normalize_extended_sections(data)
        return data

    def save(self) -> None:
        """将当前数据写回 JSON 文件。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def update(self, section: str, values: dict[str, Any]) -> None:
        """更新某个配置节（如 detection / alert / roi）并立即持久化。

        以「整体替换配置节」的方式写入：工作线程持有顶层 data 引用，替换是
        原子的引用赋值，避免 UI 线程与监控线程并发读写同一嵌套 dict。
        """
        current = self.data.get(section)
        merged = dict(current) if isinstance(current, dict) else {}
        merged.update(values)
        self.data[section] = merged
        self.save()

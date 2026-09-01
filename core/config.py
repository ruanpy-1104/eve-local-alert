"""配置管理模块。

负责 config.json 的读取、与默认值深度合并以及写回，供主入口与控制面板共用。
所有字段均为本地配置；唯一的网络项是远程预警的 Server酱 SendKey
（remote_alert.sendkey），仅在用户显式开启远程预警后使用。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

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
        "colors": ["red", "orange_red", "orange", "gray_white"],
        # 识别程度三档：0（宽松）/ 50（适中）/ 100（严格）
        "strictness": 0,
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
}

# 旧版 config.json 中已废弃的检测字段（文字条形检测遗留），加载时自动剔除，避免传入 Detector 报错
_OBSOLETE_DETECTION_KEYS = ("min_area", "min_aspect_ratio")

# 识别程度从五档（0/25/50/75/100）收编为三档（0/50/100）的迁移映射：
# 原 75（较严格）-> 50，原 50（适中）-> 0，100 不变，0/25 并入 0
_STRICTNESS_MIGRATION = {0: 0, 25: 0, 50: 0, 75: 50, 100: 100}


def _migrate_strictness(value: object) -> object:
    """把旧五档识别程度迁移到新三档；未知值就近归档到 0/50/100。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return value
    if v in _STRICTNESS_MIGRATION:
        return _STRICTNESS_MIGRATION[v]
    return min((0, 50, 100), key=lambda x: abs(x - v))


def _normalize_detection(detection: dict) -> dict:
    for key in _OBSOLETE_DETECTION_KEYS:
        detection.pop(key, None)
    return detection


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
            return copy.deepcopy(DEFAULT_CONFIG)
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            data = _deep_merge(DEFAULT_CONFIG, loaded)
        except Exception:
            # 配置文件损坏 / 不可读 / 结构非法时回退默认配置，避免启动即崩溃
            logger.error("config.json 读取失败，使用默认配置", exc_info=True)
            return copy.deepcopy(DEFAULT_CONFIG)
        if isinstance(data.get("detection"), dict):
            _normalize_detection(data["detection"])
            # 识别程度档位迁移：五档(0/25/50/75/100) -> 三档(0/50/100)
            data["detection"]["strictness"] = _migrate_strictness(
                data["detection"].get("strictness", 0)
            )
        # 远程预警每次启动都默认不开启（enabled 强制置 False），
        # 其余选项保留用户最后一次修改——避免用户忘记关闭导致意外联网推送。
        if isinstance(data.get("remote_alert"), dict):
            data["remote_alert"]["enabled"] = False
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

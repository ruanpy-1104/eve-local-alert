"""配置管理模块。

负责 config.json 的读取、与默认值深度合并以及写回，供主入口与控制面板共用。
所有字段均为本地配置，不含任何网络项。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

# 默认配置：旧版 config.json 缺字段时，通过深度合并自动补全
DEFAULT_CONFIG: dict[str, Any] = {
    "window": {"title_keyword": "EVE", "process_name": "exefile.exe"},
    "roi": {"x": 0.10, "y": 0.10, "width": 0.30, "height": 0.40},
    "detection": {
        # 图标面积/宽高比/填充率等几何约束改为检测器内部自适应，不再暴露为配置项
        "confirm_frames": 3,
        "downscale": 2,
        "colors": ["red", "orange_red", "orange", "gray_white"],
        "strictness": 50,
    },
    "alert": {"sound_file": "assets/alert.wav"},
    "loop": {"fps": 6},
    "logging": {"level": "error", "file": "logs/eve-alert.log"},
}

# 旧版 config.json 中已废弃的检测字段（文字条形检测遗留），加载时自动剔除，避免传入 Detector 报错
_OBSOLETE_DETECTION_KEYS = ("min_area", "min_aspect_ratio")


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
        with open(self.path, encoding="utf-8") as f:
            loaded = json.load(f)
        data = _deep_merge(DEFAULT_CONFIG, loaded)
        if isinstance(data.get("detection"), dict):
            _normalize_detection(data["detection"])
        return data

    def save(self) -> None:
        """将当前数据写回 JSON 文件。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def update(self, section: str, values: dict[str, Any]) -> None:
        """更新某个配置节（如 detection / alert / roi）并立即持久化。"""
        if section not in self.data or not isinstance(self.data[section], dict):
            self.data[section] = {}
        self.data[section].update(values)
        self.save()

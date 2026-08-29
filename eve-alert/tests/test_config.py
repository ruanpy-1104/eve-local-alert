"""ConfigManager 单元测试：默认值合并、读写持久化、局部更新。"""
import json

from core.config import ConfigManager


class TestConfig:
    def test_missing_file_uses_defaults(self, tmp_path):
        cfg = ConfigManager(tmp_path / "config.json")
        assert cfg.data["loop"]["fps"] == 12
        assert cfg.data["detection"]["confirm_frames"] == 3

    def test_save_and_reload(self, tmp_path):
        path = tmp_path / "config.json"
        cfg = ConfigManager(path)
        cfg.data["window"]["title_keyword"] = "TEST"
        cfg.save()
        reloaded = ConfigManager(path)
        assert reloaded.data["window"]["title_keyword"] == "TEST"
        assert reloaded.data["loop"]["fps"] == 12  # 未改动字段保留

    def test_update_persists_and_preserves_others(self, tmp_path):
        path = tmp_path / "config.json"
        cfg = ConfigManager(path)
        cfg.update("roi", {"x": 0.2, "y": 0.3})
        reloaded = ConfigManager(path)
        assert reloaded.data["roi"]["x"] == 0.2
        assert reloaded.data["roi"]["height"] == 0.4  # 未更新的字段保留

    def test_partial_config_file_merges_defaults(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"roi": {"x": 0.5}}), encoding="utf-8")
        cfg = ConfigManager(path)
        assert cfg.data["roi"]["x"] == 0.5
        assert cfg.data["detection"]["confirm_frames"] == 3  # 缺省字段用默认值

    def test_project_config_roundtrip(self):
        # 项目自带的 config.json 必须可读且完整（阈值等用户可调值不在此断言）
        cfg = ConfigManager("config.json")
        assert 0 <= cfg.data["detection"]["strictness"] <= 100
        assert cfg.data["detection"]["confirm_frames"] > 0
        assert cfg.data["roi"]["width"] > 0

    def test_default_alert_colors(self):
        # 默认配置（用户实时 config.json 可能被手动修改，故断言 DEFAULT_CONFIG）
        from core.config import DEFAULT_CONFIG

        assert DEFAULT_CONFIG["detection"]["colors"] == [
            "red",
            "orange_red",
            "orange",
            "gray_white",
        ]

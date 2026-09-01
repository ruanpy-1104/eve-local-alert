"""ConfigManager 单元测试：默认值合并、读写持久化、局部更新。"""
import json

from core.config import ConfigManager


class TestConfig:
    def test_missing_file_uses_defaults(self, tmp_path):
        cfg = ConfigManager(tmp_path / "config.json")
        assert cfg.data["loop"]["fps"] == 6
        assert cfg.data["detection"]["confirm_frames"] == 3

    def test_save_and_reload(self, tmp_path):
        path = tmp_path / "config.json"
        cfg = ConfigManager(path)
        cfg.data["window"]["title_keyword"] = "TEST"
        cfg.save()
        reloaded = ConfigManager(path)
        assert reloaded.data["window"]["title_keyword"] == "TEST"
        assert reloaded.data["loop"]["fps"] == 6  # 未改动字段保留

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

    def test_remote_alert_disabled_by_default(self, tmp_path):
        # 远程预警默认关闭（保持「默认完全本地」），冷却默认 5 分钟可选开启；
        # 旧 config.json 缺该节时深度合并补全
        from core.config import DEFAULT_CONFIG

        assert DEFAULT_CONFIG["remote_alert"] == {
            "enabled": False,
            "sendkey": "",
            "cooldown_enabled": False,
            "cooldown_minutes": 5,
        }
        path = tmp_path / "old.json"
        path.write_text(json.dumps({"roi": {"x": 0.5}}), encoding="utf-8")
        cfg = ConfigManager(path)
        assert cfg.data["remote_alert"] == {
            "enabled": False,
            "sendkey": "",
            "cooldown_enabled": False,
            "cooldown_minutes": 5,
        }

    def test_remote_alert_enabled_resets_on_reload(self, tmp_path):
        # 每次启动加载配置时 enabled 强制重置为关闭，其余选项保留最后一次修改
        path = tmp_path / "config.json"
        cfg = ConfigManager(path)
        cfg.update(
            "remote_alert",
            {
                "enabled": True,
                "sendkey": "SCT_keep",
                "cooldown_enabled": True,
                "cooldown_minutes": 10,
            },
        )
        # 当前运行实例内仍为开启状态（不打断本次会话）
        assert cfg.data["remote_alert"]["enabled"] is True
        reloaded = ConfigManager(path)  # 模拟下次启动
        ra = reloaded.data["remote_alert"]
        assert ra["enabled"] is False
        assert ra["sendkey"] == "SCT_keep"
        assert ra["cooldown_enabled"] is True
        assert ra["cooldown_minutes"] == 10

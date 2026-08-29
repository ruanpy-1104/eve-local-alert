"""日志模块测试：级别解析、配置后写文件。"""
import logging

import core.logger as logger_mod

# 供测试隔离日志全局状态
from core.logger import get_logger, parse_level, setup_logging  # noqa: F401


def test_parse_level():
    assert parse_level("debug") == logging.DEBUG
    assert parse_level("INFO") == logging.INFO
    assert parse_level("error") == logging.ERROR
    assert parse_level("weird") == logging.INFO  # 非法值回退
    assert parse_level(30) == 30  # int 直通
    assert parse_level(None) == logging.INFO


def test_get_logger_namespaced():
    assert get_logger("a.b").name == "a.b"


def test_setup_writes_log_file(tmp_path):
    # 隔离全局日志状态（避免污染 / 受其他用例影响）
    root = logging.getLogger()
    prev_handlers = list(root.handlers)
    prev_level = root.level
    root.handlers.clear()
    logger_mod._configured = False
    try:
        path = setup_logging(
            tmp_path, level="info", log_file="test.log",
            max_bytes=1024, backup_count=2,
        )
        assert path == tmp_path / "test.log"
        assert path.exists()

        get_logger("test.logger").info("hello eve alert")
        for h in root.handlers:
            h.flush()

        content = path.read_text(encoding="utf-8")
        assert "hello eve alert" in content
        assert "test.logger" in content
    finally:
        logger_mod._configured = False
        root.handlers.clear()
        root.handlers.extend(prev_handlers)
        root.setLevel(prev_level)

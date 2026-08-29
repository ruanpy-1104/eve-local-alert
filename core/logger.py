"""日志模块。

统一配置 Python 标准库 `logging`，为整个工具提供结构化日志：
- **滚动文件**：默认写入「项目根/logs/eve-alert.log」，超过 max_bytes 自动轮转，
  避免长时间运行的监控日志无限增长；
- **控制台**：同步输出（对 `--cli` 无界面模式与启动排查尤其有用）；
- 日志级别可通过 config.json 的 `logging.level` 配置（debug/info/warning/error/critical）。

日志点覆盖关键生命周期事件，便于排查识别误报 / 漏报、窗口定位失败等问题：
启动/退出、配置载入与保存、窗口定位命中与否、捕获方式（PrintWindow / mss 回退）、
检测命中、警报启停与暂停恢复、以及各类异常（含 traceback）。
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 默认日志目录与文件名（相对项目根）
DEFAULT_LOG_FILENAME = "logs/eve-alert.log"
_LOGGER_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

_configured = False
_formatter = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    "%Y-%m-%d %H:%M:%S",
)


def parse_level(value: object) -> int:
    """把配置里的级别（字符串或数字）转为 logging 级别；非法值回退 INFO。"""
    if isinstance(value, bool):
        return logging.INFO
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return _LOGGER_LEVELS.get(value.strip().lower(), logging.INFO)
    return logging.INFO


def setup_logging(
    base_dir: Path | None = None,
    level: object = logging.INFO,
    log_file: str | None = DEFAULT_LOG_FILENAME,
    max_bytes: int = 2_000_000,
    backup_count: int = 3,
) -> Path | None:
    """初始化根日志：滚动文件 + 控制台。重复调用幂等。

    :param base_dir: 项目根目录（日志文件的相对基准）；None 表示当前工作目录。
    :param level: 日志级别（字符串或 int）。
    :param log_file: 日志文件相对路径；None 表示不写文件。
    :return: 日志文件绝对路径；未启用文件日志时返回 None。
    """
    global _configured
    root = logging.getLogger()
    root.setLevel(parse_level(level))

    if not _configured:
        console = logging.StreamHandler()
        console.setFormatter(_formatter)
        root.addHandler(console)

        log_path = None
        if log_file:
            base = Path(base_dir) if base_dir else Path(".")
            log_path = base / log_file
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                file_handler = RotatingFileHandler(
                    log_path,
                    maxBytes=max(1024, int(max_bytes)),
                    backupCount=max(1, int(backup_count)),
                    encoding="utf-8",
                )
                file_handler.setFormatter(_formatter)
                root.addHandler(file_handler)
            except OSError:
                # 日志目录不可写时降级为仅控制台输出，不中断程序
                log_path = None

        _configured = True
        return log_path

    return None


def get_logger(name: str | None = None) -> logging.Logger:
    """获取带命名空间的 logger（模块级使用，如 ``get_logger(__name__)``）。"""
    return logging.getLogger(name)

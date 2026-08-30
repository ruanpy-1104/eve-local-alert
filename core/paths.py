"""运行时路径解析。

区分「开发期」与「打包后（PyInstaller frozen）」两种运行环境：

- ``app_dir()``   —— 可写数据目录（config.json、logs/）：打包后为系统用户数据目录，
                     开发期为项目根目录。
- ``assets_dir()``—— 只读资源目录（assets/）：打包后为 PyInstaller 解包目录
                     （``sys._MEIPASS``），开发期为项目根目录。

打包（--onefile / --onedir）后 ``__file__`` 指向临时解包目录，不能用于定位可写数据；
logo / 警报音等资源经 ``--add-data`` 打进包内，运行时从 ``sys._MEIPASS`` 读取。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 打包后的用户数据目录名（config.json / logs/ 的父目录）
_APP_DATA_DIR_NAME = "eve-alert"


def app_dir() -> Path:
    """可写数据目录（config.json、logs/）。

    打包后写入系统用户数据目录 ``%APPDATA%\\eve-alert``（回退 ``%LOCALAPPDATA%`` /
    用户主目录），避免在 exe 所在目录或用户当前目录产生 config.json 与 logs/。
    开发期为项目根目录。
    """
    if getattr(sys, "frozen", False):
        base = (
            os.environ.get("APPDATA")
            or os.environ.get("LOCALAPPDATA")
            or str(Path.home())
        )
        return Path(base) / _APP_DATA_DIR_NAME
    return Path(__file__).resolve().parent.parent


def assets_dir() -> Path:
    """只读资源目录：打包后为 PyInstaller 解包目录，开发期为项目根目录。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return app_dir()

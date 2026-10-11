"""共享测试 fixture。

全程共用**一个** QApplication（offscreen 渲染）。此前每个测试模块各自定义 ``app``
fixture：模块级 fixture 只在本模块生命周期内持有引用，模块切换时引用计数一度归零，
Qt 侧的单例可能被回收——继续创建控件就会在 ``QWidget.__init__`` 里访问违例崩溃
（表现为「加了另一个 Qt 测试文件后，本模块第 N 个测试随机崩掉」）。会话级持有引用
可以让它跟测试进程同生共死。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session")
def app():
    from PySide6.QtWidgets import QApplication

    instance = QApplication.instance()
    return instance or QApplication([])

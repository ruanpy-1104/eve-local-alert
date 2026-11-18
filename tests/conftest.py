"""共享测试 fixture。

全程共用**一个** QApplication（offscreen 渲染）。此前每个测试模块各自定义 ``app``
fixture：模块级 fixture 只在本模块生命周期内持有引用，模块切换时引用计数一度归零，
Qt 侧的单例可能被回收——继续创建控件就会在 ``QWidget.__init__`` 里访问违例崩溃
（表现为「加了另一个 Qt 测试文件后，本模块第 N 个测试随机崩掉」）。会话级持有引用
可以让它跟测试进程同生共死。
"""
import os
import gc

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session")
def app():
    from PySide6.QtWidgets import QApplication

    instance = QApplication.instance()
    return instance or QApplication([])


def pytest_runtest_teardown(item, nextitem):
    """每个测试后主动排空 Qt 延迟删除并回收包装器。

    PySide6/Qt 在单进程内反复创建、销毁大量 QWidget（面板、悬浮窗）时，
    延迟删除事件与 Python 包装器析构交错会触发堆损坏；每个测试后主动
    排空事件队列并回收，保证测试进程稳定。不改变任何断言语义。
    """
    gc.collect()
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance()
    if application is not None:
        application.processEvents()

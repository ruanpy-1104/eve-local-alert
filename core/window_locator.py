"""窗口定位模块。

枚举 Windows 顶层窗口，按标题关键字 / 进程名命中 EVE 客户端窗口，
并返回窗口客户区矩形（屏幕坐标），供捕获模块按此区域抓取。
"""
from __future__ import annotations

from dataclasses import dataclass

import psutil
import win32gui
import win32process


@dataclass
class WindowInfo:
    """窗口定位结果。"""

    handle: int
    title: str
    process_name: str
    rect: tuple[int, int, int, int]  # 客户区屏幕坐标 (left, top, right, bottom)


def is_minimized(hwnd: int) -> bool:
    """判断窗口是否处于最小化状态（最小化时无法捕获内容）。"""
    return bool(win32gui.IsIconic(hwnd))


class WindowLocator:
    """定位并锁定 EVE 游戏窗口。"""

    def __init__(self, title_keyword: str | None = None, process_name: str | None = None):
        self.title_keyword = title_keyword
        self.process_name = process_name

    def find(self) -> WindowInfo:
        """返回命中的可见窗口；未命中抛出 RuntimeError。

        优先返回进程名精确匹配的窗口（文档约定），无进程匹配时退回标题关键字匹配。
        """
        candidates: list[WindowInfo] = []

        def _callback(hwnd: int, _extra) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            pname = self._process_name(pid)
            if self._match(title, pname):
                candidates.append(WindowInfo(hwnd, title, pname, self._client_rect(hwnd)))

        win32gui.EnumWindows(_callback, None)
        if not candidates:
            raise RuntimeError("未找到匹配的 EVE 窗口，请检查 config.json 的 window 配置")
        win = self._pick(candidates)
        return win

    def _pick(self, candidates: list[WindowInfo]) -> WindowInfo:
        """选窗优先级：进程+标题都命中 > 仅进程命中 > 仅标题命中。"""
        if self.process_name:
            proc = self.process_name.lower()
            if self.title_keyword:
                for c in candidates:
                    if (
                        c.process_name.lower() == proc
                        and self.title_keyword.lower() in c.title.lower()
                    ):
                        return c
            for c in candidates:
                if c.process_name.lower() == proc:
                    return c
        return candidates[0]

    @staticmethod
    def list_all() -> list[WindowInfo]:
        """枚举所有可见顶层窗口（供目标程序选择器使用）。"""
        windows: list[WindowInfo] = []

        def _cb(hwnd: int, _extra) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            pname = WindowLocator._process_name(pid)
            windows.append(WindowInfo(hwnd, title, pname, WindowLocator._client_rect(hwnd)))

        win32gui.EnumWindows(_cb, None)
        return windows

    def _match(self, title: str, pname: str) -> bool:
        if self.process_name and pname.lower() == self.process_name.lower():
            return True
        if self.title_keyword and self.title_keyword.lower() in title.lower():
            return True
        return False

    @staticmethod
    def _process_name(pid: int) -> str:
        try:
            return psutil.Process(pid).name()
        except Exception:
            return ""

    @staticmethod
    def _client_rect(hwnd: int) -> tuple[int, int, int, int]:
        # GetClientRect 返回相对窗口客户区左上角 (0,0)，转换为屏幕坐标
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        p1 = win32gui.ClientToScreen(hwnd, (left, top))
        p2 = win32gui.ClientToScreen(hwnd, (right, bottom))
        return p1[0], p1[1], p2[0], p2[1]

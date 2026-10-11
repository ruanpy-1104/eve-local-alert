"""WindowLocator 匹配逻辑单元测试（不依赖真实窗口枚举）。"""
import pytest

import core.window_locator as wl
from core.window_locator import WindowInfo, WindowLocator


def _win(handle: int, title: str, pname: str) -> WindowInfo:
    return WindowInfo(handle=handle, title=title, process_name=pname, rect=(0, 0, 10, 10))


class TestMatch:
    def test_process_name_match_case_insensitive(self):
        loc = WindowLocator(process_name="exefile.exe")
        assert loc._match("任意标题", "Exefile.EXE")

    def test_title_keyword_match_case_insensitive(self):
        loc = WindowLocator(title_keyword="EVE")
        assert loc._match("EVE Online - Capsuleer", "chrome.exe")

    def test_no_match(self):
        loc = WindowLocator(title_keyword="EVE", process_name="exefile.exe")
        assert not loc._match("记事本", "notepad.exe")


class TestPick:
    def test_process_and_title_match_preferred(self):
        loc = WindowLocator(process_name="exefile.exe", title_keyword="EVE Online - Jita")
        chrome = _win(1, "EVE Online - Jita", "chrome.exe")   # 仅标题命中
        game = _win(2, "EVE Online - Jita", "exefile.exe")   # 进程+标题都命中
        other = _win(3, "EVE Online - Amarr", "exefile.exe")  # 仅进程命中
        assert loc._pick([chrome, game, other]).handle == 2

    def test_process_match_preferred_over_title(self):
        loc = WindowLocator(process_name="exefile.exe", title_keyword="EVE Online - Jita")
        chrome = _win(1, "EVE Online - Jita", "chrome.exe")   # 仅标题
        game = _win(2, "EVE Online - Amarr", "exefile.exe")   # 仅进程
        assert loc._pick([chrome, game]).handle == 2

    def test_title_only_fallback(self):
        loc = WindowLocator(title_keyword="EVE")
        chrome = _win(1, "EVE 相关页面", "chrome.exe")
        assert loc._pick([chrome]).handle == 1

    def test_no_process_configured_returns_first(self):
        loc = WindowLocator()
        a, b = _win(1, "A", "x.exe"), _win(2, "B", "y.exe")
        assert loc._pick([a, b]).handle == 1


class TestPreferHandle:
    """用户点选的句柄优先：只跟踪这一个窗口，失效即「目标已关闭」。"""

    def _patch_window(self, monkeypatch, *, alive=True, visible=True, title="T", pname="game.exe"):
        monkeypatch.setattr(wl.win32gui, "IsWindow", lambda _h: alive)
        monkeypatch.setattr(wl.win32gui, "IsWindowVisible", lambda _h: visible)
        monkeypatch.setattr(wl.win32gui, "GetWindowText", lambda _h: title)
        monkeypatch.setattr(
            wl.win32process, "GetWindowThreadProcessId", lambda _h: (0, 4242)
        )
        monkeypatch.setattr(WindowLocator, "_process_name", staticmethod(lambda _pid: pname))
        monkeypatch.setattr(
            WindowLocator, "_client_rect", staticmethod(lambda _h: (0, 0, 100, 50))
        )

    def test_alive_handle_returned_directly(self, monkeypatch):
        self._patch_window(monkeypatch, title="被跟踪窗口")
        monkeypatch.setattr(
            wl.win32gui, "EnumWindows", lambda *_: pytest.fail("不应回退到枚举匹配")
        )
        loc = WindowLocator(title_keyword="X", process_name="game.exe", prefer_handle=99)
        info = loc.find()
        assert info.handle == 99 and info.title == "被跟踪窗口"

    def test_dead_handle_raises_without_keyword_fallback(self, monkeypatch):
        # 同一进程的其它窗口仍存在（枚举能匹配到）：句柄失效时不得改认其它窗口
        self._patch_window(monkeypatch, alive=False)
        monkeypatch.setattr(
            wl.win32gui, "EnumWindows", lambda *_: pytest.fail("不应回退到枚举匹配")
        )
        loc = WindowLocator(process_name="game.exe", prefer_handle=99)
        with pytest.raises(RuntimeError):
            loc.find()

    def test_recycled_handle_of_other_process_raises(self, monkeypatch):
        # 句柄被系统复用给别的进程的窗口 → 视为原目标已关闭
        self._patch_window(monkeypatch, pname="other.exe")
        loc = WindowLocator(process_name="game.exe", prefer_handle=99)
        with pytest.raises(RuntimeError):
            loc.find()

    def test_handle_tracked_when_process_name_unknown(self, monkeypatch):
        # 进程名取不到（高权限进程）时不做进程校验，仍按句柄跟踪
        self._patch_window(monkeypatch, pname="")
        loc = WindowLocator(process_name="", prefer_handle=99)
        assert loc.find().handle == 99

    def test_no_handle_keeps_keyword_matching(self, monkeypatch):
        # 未设置句柄（CLI / 启动首次定位）时保持原有关键字匹配行为
        self._patch_window(monkeypatch)
        loc = WindowLocator(title_keyword="T", process_name="game.exe")
        assert loc.prefer_handle is None

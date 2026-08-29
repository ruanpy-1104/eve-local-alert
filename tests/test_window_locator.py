"""WindowLocator 匹配逻辑单元测试（不依赖真实窗口枚举）。"""
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

"""画面捕获模块。

优先使用 PrintWindow(PW_RENDERFULLCONTENT) 直接渲染目标窗口自身内容：
即使窗口被其他应用遮挡，也能获得该窗口的正确画面（预览与检测都只针对所选窗口）。
PrintWindow 失败或返回全黑（如独占全屏）时，回退到 mss 按屏幕区域捕获。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

import mss
import numpy as np
import win32gui

PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


# --- Win32 API 签名（64 位下句柄为指针，必须显式声明类型） ---
_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32

_user32.GetWindowDC.restype = wintypes.HDC
_user32.GetWindowDC.argtypes = [wintypes.HWND]
_user32.ReleaseDC.restype = ctypes.c_int
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.PrintWindow.restype = wintypes.BOOL
_user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]

_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteDC.restype = wintypes.BOOL
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.GetDIBits.restype = ctypes.c_int
_gdi32.GetDIBits.argtypes = [
    wintypes.HDC,
    wintypes.HBITMAP,
    wintypes.UINT,  # 起始扫描行
    wintypes.UINT,  # 行数
    ctypes.c_void_p,  # 像素缓冲
    ctypes.POINTER(_BITMAPINFOHEADER),
    wintypes.UINT,  # uUsage
]


def client_size(hwnd: int) -> tuple[int, int]:
    """返回窗口客户区尺寸 (width, height)。"""
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    return right - left, bottom - top


class Capture:
    """按目标窗口自身内容捕获画面。"""

    def __init__(self):
        self._sct = mss.mss()

    def grab_window(self, hwnd: int, region: dict[str, int]) -> np.ndarray:
        """捕获目标窗口客户区内 region 的内容，返回 BGR 帧。

        region 为窗口客户区相对坐标 {'left','top','width','height'}，
        与 ROI 归一化坐标使用的基准（客户区）一致。
        """
        frame = self._printwindow_capture(hwnd)
        if frame is not None:
            return self._crop(frame, region)
        # 回退：按屏幕坐标捕获（PrintWindow 失效的独占全屏等场景）。
        # 句柄可能已失效 / 区域尺寸非法（mss 抛 ScreenShotError），统一视为捕获失败返回 None，
        # 由上层跳过本帧或重新定位窗口。
        try:
            left, top, right, bottom = win32gui.GetClientRect(hwnd)
            ox, oy = win32gui.ClientToScreen(hwnd, (left, top))
            screen_region = {
                "left": ox + region["left"],
                "top": oy + region["top"],
                "width": region["width"],
                "height": region["height"],
            }
            shot = self._sct.grab(screen_region)
            return np.asarray(shot)[:, :, :3]
        except Exception:
            return None

    @staticmethod
    def _crop(frame: np.ndarray, region: dict[str, int]) -> np.ndarray:
        """按客户区相对 region 裁剪，越界自动钳制。"""
        h, w = frame.shape[:2]
        x = min(max(0, region["left"]), w - 1)
        y = min(max(0, region["top"]), h - 1)
        x2 = min(x + region["width"], w)
        y2 = min(y + region["height"], h)
        return frame[y:y2, x:x2]

    def _printwindow_capture(self, hwnd: int) -> np.ndarray | None:
        """用 PrintWindow 渲染窗口自身内容（客户区 BGR 帧）；失败返回 None。"""
        # 优先 PW_RENDERFULLCONTENT（可渲染 GPU 内容），失败再试普通模式
        for flags in (PW_RENDERFULLCONTENT, 0):
            frame = self._render_window(hwnd, flags)
            if frame is not None:
                return frame
        return None

    def _render_window(self, hwnd: int, flags: int) -> np.ndarray | None:
        """以指定 flags 渲染窗口；纯黑（渲染失败特征）返回 None。"""
        try:
            win_left, win_top, win_right, win_bottom = win32gui.GetWindowRect(hwnd)
            win_w = win_right - win_left
            win_h = win_bottom - win_top
            if win_w <= 0 or win_h <= 0:
                return None
            # 客户区在窗口图像内的偏移（含非客户区，如标题栏）
            cl, ct = win32gui.ClientToScreen(hwnd, (0, 0))
            off_x, off_y = cl - win_left, ct - win_top
            cl_w, cl_h = client_size(hwnd)
            if cl_w <= 0 or cl_h <= 0:
                return None

            hwnd_dc = _user32.GetWindowDC(hwnd)
            if not hwnd_dc:
                return None
            mem_dc = _gdi32.CreateCompatibleDC(hwnd_dc)
            bmp = _gdi32.CreateCompatibleBitmap(hwnd_dc, win_w, win_h)
            old = _gdi32.SelectObject(mem_dc, bmp)
            try:
                if not _user32.PrintWindow(hwnd, mem_dc, flags):
                    return None
                bmi = _BITMAPINFOHEADER()
                bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
                bmi.biWidth = win_w
                bmi.biHeight = -win_h  # 自顶向下
                bmi.biPlanes = 1
                bmi.biBitCount = 32
                bmi.biCompression = 0  # BI_RGB
                buf = ctypes.create_string_buffer(win_w * win_h * 4)
                if not _gdi32.GetDIBits(
                    mem_dc, bmp, 0, win_h, buf, ctypes.byref(bmi), DIB_RGB_COLORS
                ):
                    return None
                arr = np.frombuffer(buf, dtype=np.uint8).reshape(win_h, win_w, 4)
                bgr = arr[:, :, :3].copy()  # 32bpp DIB 内存序为 B,G,R,A
                client = bgr[off_y : off_y + cl_h, off_x : off_x + cl_w]
                if client.size == 0 or client.max() < 8:
                    return None  # 近乎纯黑视为渲染失败（暗色场景仍保留内容）
                return client
            finally:
                _gdi32.SelectObject(mem_dc, old)
                _gdi32.DeleteObject(bmp)
                _gdi32.DeleteDC(mem_dc)
                _user32.ReleaseDC(hwnd, hwnd_dc)
        except Exception:
            return None

    def close(self) -> None:
        """释放底层 mss 实例。"""
        self._sct.close()

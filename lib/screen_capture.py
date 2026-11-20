"""Chụp cửa sổ WebView2/exam qua PrintWindow mà KHÔNG cần tắt WDA.

`WDA_EXCLUDEFROMCAPTURE` chỉ chặn các đường capture đi qua DWM (BitBlt,
ImageGrab, DXGI...). PrintWindow gửi WM_PRINT trực tiếp cho chính cửa sổ con,
nên với WebView2 nó thường vẫn vẽ được nội dung. Nhờ đó tool không cần tạm gỡ
WDA của ExamLockdown -> không tạo ra tamper event và không làm hỏng submit.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import List, Optional, Tuple

if not sys.platform.startswith("win"):
    raise RuntimeError("screen_capture chỉ chạy trên Windows")

PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0
SRCCOPY = 0x00CC0020
BI_RGB = 0


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_int32),
        ("top", ctypes.c_int32),
        ("right", ctypes.c_int32),
        ("bottom", ctypes.c_int32),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _load_libs():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    user32.GetDC.restype = ctypes.c_void_p
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]

    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]

    user32.EnumChildWindows.restype = wintypes.BOOL
    user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]

    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [
        wintypes.HWND, wintypes.LPWSTR, ctypes.c_int
    ]

    user32.FillRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT), ctypes.c_void_p]

    user32.PrintWindow.restype = wintypes.BOOL
    user32.PrintWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.UINT]

    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]

    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_int
    ]

    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]

    gdi32.CreateSolidBrush.restype = ctypes.c_void_p
    gdi32.CreateSolidBrush.argtypes = [ctypes.c_uint32]

    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.GetDIBits.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(BITMAPINFO),
        ctypes.c_uint32,
    ]

    return user32, gdi32


USER32, GDI32 = _load_libs()


def _make_bitmap_info(width: int, height: int) -> BITMAPINFO:
    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height  # top-down
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = BI_RGB
    return info


def _window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    rect = RECT()
    if not USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        return None
    return int(rect.left), int(rect.top), width, height


def _window_class(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    USER32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def find_child_windows(parent_hwnd: int) -> List[int]:
    """Liệt kê đệ quy mọi cửa sổ con của parent."""
    result: List[int] = []
    visited = set()

    def _collect(root: int) -> None:
        children: List[int] = []

        @WNDENUMPROC
        def _enum(hwnd, _lparam):
            children.append(int(hwnd))
            return True

        USER32.EnumChildWindows(root, _enum, 0)
        for child in children:
            if child in visited:
                continue
            visited.add(child)
            result.append(child)
            _collect(child)

    _collect(parent_hwnd)
    return result


def find_exam_content_window(parent_hwnd: int) -> Optional[int]:
    """Chọn cửa sổ con có khả năng chứa nội dung bài thi nhất.

    Ưu tiên WebView2 render window (Chrome_RenderWidgetHostHWND); nếu không có
    thì chọn cửa sổ con có diện tích lớn nhất.
    """
    children = find_child_windows(parent_hwnd)
    if not children:
        return None

    parent_rect = _window_rect(parent_hwnd)
    parent_area = (parent_rect[2] * parent_rect[3]) if parent_rect else 1

    best_hwnd: Optional[int] = None
    best_score = -1

    for child in children:
        class_name = _window_class(child)
        rect = _window_rect(child)
        if rect is None:
            continue
        _, _, width, height = rect
        area = width * height

        # WebView2 render window rất đặc trưng.
        score = 0
        class_lower = class_name.lower()
        if "renderwidgethost" in class_lower or "chromes_renderwidgethost" in class_lower:
            score += 1000
        elif "webview" in class_lower or "edge" in class_lower:
            score += 300
        elif "d3d" in class_lower or "composition" in class_lower:
            score += 100

        # Cửa sổ con lớn gần bằng parent nhiều khả năng là viewport nội dung.
        score += min(200, int(area * 200 / max(1, parent_area)))

        if score > best_score:
            best_score = score
            best_hwnd = child

    return best_hwnd


def capture_hwnd_via_printwindow(hwnd: int):
    """Chụp một HWND bằng PrintWindow, trả về PIL.Image RGB.

    Trả về None nếu không chụp được.
    """
    try:
        from PIL import Image
    except ImportError:
        return None

    rect = _window_rect(hwnd)
    if rect is None:
        return None
    _, _, width, height = rect

    screen_dc = USER32.GetDC(None)
    if not screen_dc:
        return None

    mem_dc = GDI32.CreateCompatibleDC(screen_dc)
    bitmap = GDI32.CreateCompatibleBitmap(screen_dc, width, height)
    if not mem_dc or not bitmap:
        if screen_dc:
            USER32.ReleaseDC(None, screen_dc)
        return None

    try:
        old = GDI32.SelectObject(mem_dc, bitmap)

        brush = GDI32.CreateSolidBrush(0x00000000)
        rect_struct = RECT(0, 0, width, height)
        USER32.FillRect(mem_dc, ctypes.byref(rect_struct), brush)
        GDI32.DeleteObject(brush)

        ok = USER32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
        if not ok:
            return None

        info = _make_bitmap_info(width, height)
        buffer = ctypes.create_string_buffer(width * height * 4)
        got = GDI32.GetDIBits(
            mem_dc,
            bitmap,
            0,
            height,
            buffer,
            ctypes.byref(info),
            DIB_RGB_COLORS,
        )
        if got <= 0:
            return None

        # BGRA -> RGB.
        return Image.frombuffer("RGB", (width, height), buffer.raw, "raw", "BGRX", 0, 1)
    finally:
        GDI32.SelectObject(mem_dc, old)
        GDI32.DeleteObject(bitmap)
        GDI32.DeleteDC(mem_dc)
        USER32.ReleaseDC(None, screen_dc)

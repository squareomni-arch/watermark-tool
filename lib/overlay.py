"""Watermark overlay cho Windows - chỉ dùng ctypes + GDI, KHÔNG cần pywin32.

Vẽ chữ (mặc định "Hello") màu vàng, mờ nhẹ như watermark, ở góc trên bên phải
màn hình. Cửa sổ là layered window với alpha từng điểm ảnh nên nền hoàn toàn
trong suốt, chỉ có chữ hiện lên; chuột click xuyên qua, không cướp focus.

Kỹ thuật: GDI vẽ chữ trắng lên nền đen trong DIB 32-bit, sau đó tính alpha
từ độ sáng từng pixel và tô lại theo màu mong muốn (premultiplied ARGB) rồi
đẩy lên cửa sổ bằng UpdateLayeredWindow. Nhờ vậy chữ có viền mượt, không bị
quầng đen như cách color-key.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Dict, Optional, Tuple

_IS_WINDOWS = sys.platform.startswith("win")

# ----------------------------------------------------------------- Win32 hằng số
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_POPUP = 0x80000000
WS_CHILD = 0x40000000

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4

HWND_TOP = 0
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_FRAMECHANGED = 0x0020

GWL_STYLE = -16
GWL_EXSTYLE = -20

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01

DIB_RGB_COLORS = 0
BI_RGB = 0

SPI_GETWORKAREA = 0x0030

DT_LEFT = 0x0000
DT_TOP = 0x0000
DT_SINGLELINE = 0x0020
DT_NOPREFIX = 0x0800
DT_WORDBREAK = 0x0010
DT_CALCRECT = 0x0400

TRANSPARENT = 1
FW_NORMAL = 400
FW_BOLD = 700
DEFAULT_CHARSET = 1
ANTIALIASED_QUALITY = 4
DEFAULT_PITCH_AND_FAMILY = 0

WM_PAINT = 0x000F
WM_DESTROY = 0x0002

DEFAULT_TEXT = "Hello"
DEFAULT_COLOR = (255, 215, 0)  # vàng gold
DEFAULT_OPACITY = 0.70
DEFAULT_FONT = "Segoe UI"
DEFAULT_FONT_SIZE = 16
DEFAULT_MARGIN = (28, 20)
DEFAULT_PADDING = 4
DEFAULT_BOX_COLOR = (18, 18, 18)
DEFAULT_BOX_OPACITY = 0.82


# --------------------------------------------------------------- struct Win32
class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_int32),
        ("top", ctypes.c_int32),
        ("right", ctypes.c_int32),
        ("bottom", ctypes.c_int32),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_int32), ("cy", ctypes.c_int32)]


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


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


WNDPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_ssize_t
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint32),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int32),
        ("cbWndExtra", ctypes.c_int32),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
    ]


# ------------------------------------------------------------------- tiện ích
def parse_color(value) -> Tuple[int, int, int]:
    """Nhận '#RRGGBB', 'RRGGBB', [r,g,b] hoặc tên màu đơn giản."""
    named = {
        "yellow": (255, 255, 0),
        "gold": (255, 215, 0),
        "orange": (255, 165, 0),
        "red": (255, 60, 60),
        "green": (80, 220, 100),
        "cyan": (0, 230, 230),
        "white": (255, 255, 255),
    }

    if isinstance(value, str):
        key = value.strip().lower()
        if key in named:
            return named[key]
        key = key.lstrip("#")
        if len(key) == 6:
            try:
                return tuple(int(key[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
            except ValueError:
                pass

    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            return tuple(max(0, min(255, int(c))) for c in value)  # type: ignore[return-value]
        except (TypeError, ValueError):
            pass

    return DEFAULT_COLOR


def apply_alpha(buffer: bytearray, rgb: Tuple[int, int, int], opacity: float) -> None:
    """Chuyển ảnh xám (chữ trắng trên nền đen, BGRA) thành ARGB premultiplied.

    `buffer` được sửa tại chỗ. Alpha lấy từ độ sáng của pixel nên chữ giữ được
    viền mượt; màu cuối cùng là `rgb` nhân với alpha (premultiplied).
    """
    opacity = max(0.0, min(1.0, float(opacity)))
    target_r, target_g, target_b = rgb
    total = len(buffer)

    for i in range(0, total - 3, 4):
        lum = buffer[i]
        g = buffer[i + 1]
        if g > lum:
            lum = g
        r = buffer[i + 2]
        if r > lum:
            lum = r

        alpha = int(lum * opacity)
        if alpha <= 0:
            buffer[i] = buffer[i + 1] = buffer[i + 2] = buffer[i + 3] = 0
            continue

        buffer[i] = (target_b * alpha) // 255
        buffer[i + 1] = (target_g * alpha) // 255
        buffer[i + 2] = (target_r * alpha) // 255
        buffer[i + 3] = alpha


def enable_dpi_awareness() -> None:
    """Giúp chữ không bị nhoè khi Windows scale > 100%."""
    if not _IS_WINDOWS:
        return
    try:  # Windows 10 1703+
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:  # Windows 8.1+
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:  # Windows Vista+
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# --------------------------------------------------------------------- overlay
class WatermarkOverlay:
    """Chữ watermark mờ, luôn nổi trên cùng, click xuyên qua."""

    def __init__(self, settings: Optional[Dict] = None):
        settings = dict(settings or {})

        self.text = str(settings.get("text") or DEFAULT_TEXT)
        self.color = parse_color(settings.get("color", DEFAULT_COLOR))
        try:
            self.opacity = float(settings.get("opacity", DEFAULT_OPACITY))
        except (TypeError, ValueError):
            self.opacity = DEFAULT_OPACITY
        self.opacity = max(0.05, min(1.0, self.opacity))

        self.font_family = str(settings.get("font_family") or DEFAULT_FONT)
        try:
            self.font_size = max(6, int(settings.get("font_size", DEFAULT_FONT_SIZE)))
        except (TypeError, ValueError):
            self.font_size = DEFAULT_FONT_SIZE
        self.bold = bool(settings.get("bold", True))
        self.corner = str(settings.get("corner") or "top_right").lower()

        self.margin_x = int(settings.get("margin_x", DEFAULT_MARGIN[0]))
        self.margin_y = int(settings.get("margin_y", DEFAULT_MARGIN[1]))
        self.padding = int(settings.get("padding", DEFAULT_PADDING))
        self.box_color = parse_color(settings.get("box_color", DEFAULT_BOX_COLOR))
        try:
            self.box_opacity = float(settings.get("box_opacity", DEFAULT_BOX_OPACITY))
        except (TypeError, ValueError):
            self.box_opacity = DEFAULT_BOX_OPACITY
        self.box_opacity = max(0.0, min(1.0, self.box_opacity))

        self.hwnd: Optional[int] = None
        self.active = False
        self._parent_hwnd: Optional[int] = None

        self._user32 = None
        self._gdi32 = None
        self._kernel32 = None
        self._wndproc_ref = None
        self._font = None
        self._screen_dc = None
        self._mem_dc = None
        self._dib = None
        self._bits = None
        self._layer_size = (0, 0)
        self._multiline = False
        self._max_text_width = int(settings.get("max_width", 620))

        if not _IS_WINDOWS:
            return

        try:
            self._init_win32()
            self._create_window()
            self._resize_and_paint()
        except Exception:
            self._release_gdi()
            self.hwnd = None

    # ------------------------------------------------------------------ khởi tạo
    @property
    def available(self) -> bool:
        return self.hwnd is not None

    @property
    def is_child(self) -> bool:
        return self._parent_hwnd is not None and self.hwnd is not None

    def reposition(self) -> None:
        """Đặt lại vị trí watermark theo kích thước hiện tại của màn hình/parent."""
        if not self.hwnd or not self.active:
            return
        x, y = self._position()
        insert_after = HWND_TOP if self._parent_hwnd else HWND_TOPMOST
        self._user32.SetWindowPos(
            self.hwnd, insert_after, x, y, 0, 0,
            SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    # ------------------------------------------------------------- ẩn khỏi EnumWindows
    def attach_to(self, parent_hwnd: int) -> bool:
        """Gắn watermark vào cửa sổ app thi để nó không còn là top-level window.

        ExamLockdown quét mọi cửa sổ top-level đang hiện (kể cả toolwindow/layered)
        rồi kill process chủ. Cửa sổ con (WS_CHILD) không được EnumWindows liệt kê,
        nên watermark vẫn hiển thị mà process của tool không bị diệt.
        """
        if not self.hwnd or not parent_hwnd:
            return False
        if not self._user32.IsWindow(parent_hwnd):
            return False
        if parent_hwnd == self._parent_hwnd:
            self.reposition()
            return True

        try:
            # Theo tài liệu SetParent: khi chuyển một cửa sổ top-level thành
            # cửa sổ con, phải clear WS_POPUP và set WS_CHILD TRƯỚC khi gọi
            # SetParent. Làm ngược lại khiến cửa sổ vẫn bị EnumWindows liệt kê.
            style = self._user32.GetWindowLongPtrW(self.hwnd, GWL_STYLE)
            style = (style & ~WS_POPUP) | WS_CHILD
            self._user32.SetWindowLongPtrW(self.hwnd, GWL_STYLE, style)

            exstyle = self._user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
            exstyle &= ~WS_EX_TOPMOST
            self._user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, exstyle)

            # KHÔNG kiểm tra giá trị trả về của SetParent: với cửa sổ top-level,
            # SetParent thành công trả về parent CŨ là NULL, giống hệt lỗi.
            # Phải kiểm tra parent thực tế sau khi gọi.
            self._user32.SetParent(self.hwnd, parent_hwnd)
            if int(self._user32.GetParent(self.hwnd) or 0) != int(parent_hwnd):
                return False

            self._parent_hwnd = int(parent_hwnd)

            # Việc gắn child window đã thành công (GetParent khớp). Các lỗi vẽ
            # lại layer sau đó không được coi là attach thất bại, nếu không
            # watcher sẽ gọi lại mãi và bỏ lỡ cơ hội ẩn overlay.
            try:
                self._commit_layer()
                if self.active:
                    self._show(None, None, self._multiline)
            except Exception:
                pass
            return True
        except Exception:
            return False

    def detach_from_parent(self) -> bool:
        """Đưa watermark trở lại thành top-level khi app thi đã thoát."""
        if not self.hwnd or self._parent_hwnd is None:
            return False

        try:
            self._user32.SetParent(self.hwnd, None)

            style = self._user32.GetWindowLongPtrW(self.hwnd, GWL_STYLE)
            style = (style & ~WS_CHILD) | WS_POPUP
            self._user32.SetWindowLongPtrW(self.hwnd, GWL_STYLE, style)

            exstyle = self._user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
            exstyle |= WS_EX_TOPMOST
            self._user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, exstyle)

            self._parent_hwnd = None
            self._commit_layer()
            if self.active:
                self._show(None, None, self._multiline)
            return True
        except Exception:
            return False

    def _init_win32(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32")
        kernel32 = ctypes.WinDLL("kernel32")

        user32.GetDC.restype = ctypes.c_void_p
        user32.GetDC.argtypes = [ctypes.c_void_p]
        user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        user32.RegisterClassW.restype = ctypes.c_uint16
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]

        user32.CreateWindowExW.restype = ctypes.c_void_p
        user32.CreateWindowExW.argtypes = [
            ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
            ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]

        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.DefWindowProcW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_ssize_t
        ]

        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.DestroyWindow.argtypes = [ctypes.c_void_p]
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32,
            ctypes.c_int32, ctypes.c_int32, ctypes.c_uint32,
        ]
        user32.SetParent.restype = ctypes.c_void_p
        user32.SetParent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.IsWindow.restype = wintypes.BOOL
        user32.IsWindow.argtypes = [ctypes.c_void_p]
        user32.GetClientRect.restype = wintypes.BOOL
        user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        user32.SystemParametersInfoW.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32
        ]
        user32.UpdateLayeredWindow.restype = ctypes.c_int
        user32.UpdateLayeredWindow.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(POINT), ctypes.POINTER(SIZE),
            ctypes.c_void_p, ctypes.POINTER(POINT),
            ctypes.c_uint32, ctypes.POINTER(BLENDFUNCTION), ctypes.c_uint32,
        ]
        user32.FillRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT), ctypes.c_void_p]
        user32.DrawTextW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int, ctypes.POINTER(RECT), ctypes.c_uint32
        ]

        gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
        gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
        gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        gdi32.SelectObject.restype = ctypes.c_void_p
        gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi32.CreateSolidBrush.restype = ctypes.c_void_p
        gdi32.CreateSolidBrush.argtypes = [ctypes.c_uint32]
        gdi32.SetTextColor.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        gdi32.SetBkMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
        gdi32.GetTextExtentPoint32W.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int, ctypes.POINTER(SIZE)
        ]
        gdi32.CreateDIBSection.restype = ctypes.c_void_p
        gdi32.CreateDIBSection.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_uint32,
        ]

        gdi32.CreateFontW.restype = ctypes.c_void_p
        gdi32.CreateFontW.argtypes = [
            ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32,
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_uint32, ctypes.c_wchar_p,
        ]

        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]

        self._user32 = user32
        self._gdi32 = gdi32
        self._kernel32 = kernel32

    def _create_window(self) -> None:
        hinst = self._kernel32.GetModuleHandleW(None)

        self._wndproc_ref = WNDPROC(self._wnd_proc)
        wndclass = WNDCLASSW()
        wndclass.hInstance = hinst
        wndclass.lpszClassName = "StealthHelperWatermark"
        wndclass.lpfnWndProc = self._wndproc_ref
        self._user32.RegisterClassW(ctypes.byref(wndclass))

        self.hwnd = self._user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
            | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            "StealthHelperWatermark",
            "Stealth Helper Watermark",
            WS_POPUP,
            0, 0, 1, 1,
            None, None, hinst, None,
        )
        if not self.hwnd:
            raise OSError("CreateWindowExW thất bại")

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        # Nội dung cửa sổ do UpdateLayeredWindow quản lý nên không cần vẽ lại.
        return self._user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ----------------------------------------------------------------- vẽ chữ
    def _create_font(self) -> int:
        height = -self.font_size  # số âm = chiều cao ký tự (không phải cell)
        weight = FW_BOLD if self.bold else FW_NORMAL
        font = self._gdi32.CreateFontW(
            height, 0, 0, 0, weight,
            0, 0, 0,
            DEFAULT_CHARSET, 0, 0, ANTIALIASED_QUALITY,
            DEFAULT_PITCH_AND_FAMILY, self.font_family,
        )
        return font

    def _text_size(self, hdc: int) -> Tuple[int, int]:
        """Đo chữ trên DC đã chọn sẵn font (bắt buộc, nếu không sẽ đo sai)."""
        size = SIZE()
        self._gdi32.GetTextExtentPoint32W(
            hdc, self.text, len(self.text), ctypes.byref(size)
        )
        return int(size.cx), int(size.cy)

    def _measure_multiline(self, hdc: int) -> Tuple[int, int]:
        """Đo kích thước khối nhiều dòng, giới hạn bề rộng để tự xuống dòng."""
        rect = RECT(0, 0, self._max_text_width, 0)
        self._user32.DrawTextW(
            hdc, self.text, len(self.text),
            ctypes.byref(rect),
            DT_LEFT | DT_TOP | DT_WORDBREAK | DT_NOPREFIX | DT_CALCRECT,
        )
        return max(1, int(rect.right)), max(1, int(rect.bottom))

    def _resize_and_paint(self) -> None:
        self._release_gdi()

        self._screen_dc = self._user32.GetDC(None)
        if not self._screen_dc:
            raise OSError("GetDC thất bại")

        self._mem_dc = self._gdi32.CreateCompatibleDC(self._screen_dc)
        if not self._mem_dc:
            raise OSError("CreateCompatibleDC thất bại")

        self._font = self._create_font()
        if not self._font:
            raise OSError("CreateFontW thất bại")

        # Chọn font vào DC trước khi đo để kích thước khớp với lúc vẽ.
        self._gdi32.SelectObject(self._mem_dc, self._font)
        multiline = self._multiline and "\n" in self.text
        if multiline:
            text_w, text_h = self._measure_multiline(self._mem_dc)
        else:
            text_w, text_h = self._text_size(self._mem_dc)
        width = max(1, text_w + self.padding * 2)
        height = max(1, text_h + self.padding * 2)

        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # top-down
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB

        bits = ctypes.c_void_p()
        self._dib = self._gdi32.CreateDIBSection(
            self._screen_dc, ctypes.byref(info), DIB_RGB_COLORS,
            ctypes.byref(bits), None, 0,
        )
        if not self._dib or not bits:
            raise OSError("CreateDIBSection thất bại")
        self._bits = bits

        self._gdi32.SelectObject(self._mem_dc, self._dib)
        self._gdi32.SelectObject(self._mem_dc, self._font)
        self._gdi32.SetBkMode(self._mem_dc, TRANSPARENT)
        self._gdi32.SetTextColor(self._mem_dc, 0x00FFFFFF)  # trắng -> dùng làm alpha

        box_visible = multiline and self.box_opacity > 0
        if box_visible:
            # Nền tối mờ để chữ đọc rõ trên mọi loại nền (đề thi, web, PDF...).
            r, g, b = self.box_color
            brush = self._gdi32.CreateSolidBrush((r << 16) | (g << 8) | b)
        else:
            brush = self._gdi32.CreateSolidBrush(0x00000000)
        rect = RECT(0, 0, width, height)
        self._user32.FillRect(self._mem_dc, ctypes.byref(rect), brush)
        self._gdi32.DeleteObject(brush)

        draw_rect = RECT(self.padding, self.padding, self.padding + text_w, self.padding + text_h)
        draw_flags = DT_LEFT | DT_TOP | DT_NOPREFIX
        draw_flags |= DT_WORDBREAK if multiline else DT_SINGLELINE
        self._user32.DrawTextW(
            self._mem_dc, self.text, len(self.text),
            ctypes.byref(draw_rect),
            draw_flags,
        )

        self._apply_alpha(width, height, keep_background=box_visible)
        self._layer_size = (width, height)
        self._commit_layer()

    def _apply_alpha(self, width: int, height: int, keep_background: bool = False) -> None:
        length = width * height * 4
        raw = bytearray(ctypes.string_at(self._bits, length))

        if not keep_background:
            apply_alpha(raw, self.color, self.opacity)
            ctypes.memmove(self._bits, bytes(raw), length)
            return

        # Nền tối mờ cho ô nhiều dòng: giữ độ sáng của nền, chữ vẽ trắng nên
        # alpha = độ sáng * opacity, màu = nền hoặc màu chữ tuỳ pixel.
        solid = int(255 * self.box_opacity)
        box_r, box_g, box_b = self.box_color
        background = bytearray()
        for i in range(0, len(raw), 4):
            background += bytes((
                (box_b * solid) // 255,
                (box_g * solid) // 255,
                (box_r * solid) // 255,
                solid,
            ))

        apply_alpha(raw, self.color, self.opacity)
        merged = bytearray(len(raw))
        for i in range(0, len(raw), 4):
            alpha = raw[i + 3]
            inv = 255 - alpha
            merged[i] = raw[i] + (background[i] * inv) // 255
            merged[i + 1] = raw[i + 1] + (background[i + 1] * inv) // 255
            merged[i + 2] = raw[i + 2] + (background[i + 2] * inv) // 255
            merged[i + 3] = alpha + (background[i + 3] * inv) // 255

        ctypes.memmove(self._bits, bytes(merged), length)

    def _position(self) -> Tuple[int, int]:
        width, height = self._layer_size

        if self._parent_hwnd and self._user32.IsWindow(self._parent_hwnd):
            # Cửa sổ overlay là cửa sổ con của app thi: tọa độ tính theo client
            # của parent. Nhờ vậy watermark không nằm trong danh sách top-level
            # mà ExamLockdown dùng để diệt overlay.
            client = RECT()
            if not self._user32.GetClientRect(self._parent_hwnd, ctypes.byref(client)):
                client = RECT(0, 0, 1920, 1080)

            left = client.left + self.margin_x
            top = client.top + self.margin_y
            right = max(left, int(client.right) - width - self.margin_x)
            bottom = max(top, int(client.bottom) - height - self.margin_y)

            if "left" in self.corner and "bottom" in self.corner:
                return left, bottom
            if "left" in self.corner:
                return left, top
            if "bottom" in self.corner:
                return right, bottom
            return right, top  # mặc định: góc trên bên phải

        work = RECT()
        if not self._user32.SystemParametersInfoW(
            SPI_GETWORKAREA, 0, ctypes.byref(work), 0
        ):
            work = RECT(0, 0, 1920, 1080)

        left = work.left + self.margin_x
        top = work.top + self.margin_y
        right = work.right - width - self.margin_x
        bottom = work.bottom - height - self.margin_y

        if "left" in self.corner and "bottom" in self.corner:
            return left, bottom
        if "left" in self.corner:
            return left, top
        if "bottom" in self.corner:
            return right, bottom
        return right, top  # mặc định: góc trên bên phải

    def _commit_layer(self) -> None:
        width, height = self._layer_size

        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        size = SIZE(width, height)
        src = POINT(0, 0)

        if self._parent_hwnd and self._user32.IsWindow(self._parent_hwnd):
            # Cửa sổ con: giữ nguyên kích thước, chỉ cập nhật nội dung + vị trí
            # tương đối so với client của parent.
            self._user32.UpdateLayeredWindow(
                self.hwnd, self._screen_dc,
                None, ctypes.byref(size),
                self._mem_dc, ctypes.byref(src),
                0, ctypes.byref(blend), ULW_ALPHA,
            )
            x, y = self._position()
            self._user32.SetWindowPos(
                self.hwnd, HWND_TOP, x, y, 0, 0,
                SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
            )
            return

        x, y = self._position()
        dst = POINT(x, y)
        self._user32.UpdateLayeredWindow(
            self.hwnd, self._screen_dc,
            ctypes.byref(dst), ctypes.byref(size),
            self._mem_dc, ctypes.byref(src),
            0, ctypes.byref(blend), ULW_ALPHA,
        )

    # ------------------------------------------------------------- hiện / ẩn
    def show(self, text: Optional[str] = None, position: Optional[Tuple[int, int]] = None) -> bool:
        """Hiện watermark. Trả về False nếu nền tảng/thư viện không hỗ trợ."""
        return self._show(text, position, multiline=False)

    def show_lines(
        self,
        text: str,
        max_lines: int = 20,
        max_chars: int = 800,
        position: Optional[Tuple[int, int]] = None,
    ) -> bool:
        """Hiện khối chữ nhiều dòng (dùng để hiển thị danh sách đáp án)."""
        if self.hwnd:
            lines = [line.rstrip() for line in str(text).splitlines()]
            lines = [line for line in lines if line.strip()][:max_lines]
            clipped = []
            for line in lines:
                if len(line) > 120:
                    line = line[:117] + "..."
                clipped.append(line)
            body = "\n".join(clipped)
            if len(body) > max_chars:
                body = body[: max_chars - 3] + "..."

            try:
                self.font_size = max(8, min(self.font_size, 16))
                self._multiline = True
                self._resize_and_paint()
            except Exception:
                return False

            return self._show(body, position, multiline=True)

        return self._show(str(text)[:4000], position, multiline=True)

    def _show(
        self,
        text: Optional[str],
        position: Optional[Tuple[int, int]],
        multiline: bool,
    ) -> bool:
        if not self.hwnd:
            return False

        self._multiline = multiline

        if text is not None and str(text) != self.text:
            self.text = str(text)
            try:
                self._resize_and_paint()
            except Exception:
                return False

        if position:
            x, y = position
        else:
            x, y = self._position()

        insert_after = HWND_TOP if self._parent_hwnd else HWND_TOPMOST
        self._user32.SetWindowPos(
            self.hwnd, insert_after, x, y, 0, 0,
            SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
        self._user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        self.active = True
        return True

    def hide(self) -> None:
        """Ẩn watermark."""
        if self.hwnd and self.active:
            self._user32.ShowWindow(self.hwnd, SW_HIDE)
            self.active = False

    # --------------------------------------------------------------- dọn dẹp
    def _release_gdi(self) -> None:
        if self._gdi32 is None:
            return

        # Xoá DC trước để GDI trả các object về trạng thái không bị chọn,
        # sau đó mới xoá object (DeleteObject sẽ thất bại nếu còn được chọn).
        if self._mem_dc:
            try:
                self._gdi32.DeleteDC(self._mem_dc)
            except Exception:
                pass
            self._mem_dc = None

        for handle in (self._dib, self._font):
            if handle:
                try:
                    self._gdi32.DeleteObject(handle)
                except Exception:
                    pass
        self._dib = None
        self._font = None

        if self._screen_dc and self._user32 is not None:
            try:
                self._user32.ReleaseDC(None, self._screen_dc)
            except Exception:
                pass
            self._screen_dc = None

    def destroy(self) -> None:
        """Giải phóng cửa sổ và tài nguyên GDI."""
        if self.hwnd and self._user32 is not None:
            try:
                self._user32.DestroyWindow(self.hwnd)
            except Exception:
                pass
        self.hwnd = None
        self.active = False
        self._release_gdi()

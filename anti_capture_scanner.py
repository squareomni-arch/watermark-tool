#!/usr/bin/env python3
"""Quét khả năng chống chụp màn hình của app thi trên Windows.

Mục đích:
    Kiểm tra app thi (cửa sổ Windows) đang bị lộ nội dung qua những kênh nào
    để bạn VÁ LẠI. Script này KHÔNG dùng để lách chống chụp màn hình.

Các hạng mục quét:
    1. Trạng thái SetWindowDisplayAffinity (WDA_EXCLUDEFROMCAPTURE).
    2. Chụp màn hình thường bằng GDI BitBlt.
    3. PrintWindow + PW_RENDERFULLCONTENT.
    4. UI Automation đọc text (tùy chọn, vì có thể chậm).

Cách dùng:
    python anti_capture_scanner.py --list
    python anti_capture_scanner.py --title "Tên cửa sổ"
    python anti_capture_scanner.py --process "app.exe" --uia

Chỉ chạy trên Windows.
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Dict, List, Optional, Tuple

if not sys.platform.startswith("win"):
    print("[ERROR] Script chỉ chạy trên Windows.")
    raise SystemExit(1)

# ---------------------------------------------------------------- Win32 constants
WDA_NONE = 0
WDA_MONITOR = 1
WDA_EXCLUDEFROMCAPTURE = 0x00000011

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


# ---------------------------------------------------------------- Win32 structs
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


# ---------------------------------------------------------------- Win32 setup
def _load_user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]

    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]

    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    ]

    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    ]

    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]

    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]

    user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.GetWindowDisplayAffinity.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]

    user32.GetWindowDC.restype = ctypes.c_void_p
    user32.GetWindowDC.argtypes = [wintypes.HWND]

    user32.GetDC.restype = ctypes.c_void_p
    user32.GetDC.argtypes = [wintypes.HWND]

    user32.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]

    user32.FillRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT), ctypes.c_void_p]

    user32.PrintWindow.restype = wintypes.BOOL
    user32.PrintWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.UINT]

    return user32


def _load_gdi32():
    gdi32 = ctypes.WinDLL("gdi32")

    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]

    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
    ]

    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]

    gdi32.CreateSolidBrush.restype = ctypes.c_void_p
    gdi32.CreateSolidBrush.argtypes = [ctypes.c_uint32]

    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.BitBlt.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]

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

    return gdi32


def _load_kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]

    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]

    return kernel32


USER32 = _load_user32()
GDI32 = _load_gdi32()
KERNEL32 = _load_kernel32()


# ---------------------------------------------------------------- window utils
def _process_name_from_pid(pid: int) -> str:
    handle = KERNEL32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return f"pid:{pid}"

    try:
        length = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(length.value)
        ok = KERNEL32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(length)
        )
        if not ok:
            return f"pid:{pid}"
        return Path(buffer.value).name
    finally:
        KERNEL32.CloseHandle(handle)


def list_windows() -> List[Dict]:
    windows: List[Dict] = []

    @WNDENUMPROC
    def _enum(hwnd, _lparam):
        if not USER32.IsWindowVisible(hwnd):
            return True

        title_buffer = ctypes.create_unicode_buffer(512)
        USER32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
        title = title_buffer.value.strip()

        class_buffer = ctypes.create_unicode_buffer(256)
        USER32.GetClassNameW(hwnd, class_buffer, len(class_buffer))

        pid = wintypes.DWORD()
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        if title:
            windows.append(
                {
                    "hwnd": int(hwnd),
                    "title": title,
                    "class": class_buffer.value,
                    "pid": pid.value,
                    "process": _process_name_from_pid(pid.value),
                }
            )
        return True

    USER32.EnumWindows(_enum, 0)
    return windows


def find_window(
    windows: List[Dict],
    title_substring: Optional[str] = None,
    process_substring: Optional[str] = None,
) -> Optional[Dict]:
    title_substring = (title_substring or "").strip().lower()
    process_substring = (process_substring or "").strip().lower()

    for window in windows:
        if title_substring and title_substring in window["title"].lower():
            return window
        if process_substring and process_substring in window["process"].lower():
            return window
    return None


# ---------------------------------------------------------------- capture tests
def _get_window_size(hwnd: int) -> Tuple[int, int]:
    rect = RECT()
    USER32.GetWindowRect(hwnd, ctypes.byref(rect))
    width = max(1, int(rect.right - rect.left))
    height = max(1, int(rect.bottom - rect.top))
    return width, height


def _make_bitmap_info(width: int, height: int) -> BITMAPINFO:
    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height  # top-down
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0  # BI_RGB
    return info


def _bits_from_hbitmap(mem_dc: int, hbitmap: int, width: int, height: int) -> bytes:
    info = _make_bitmap_info(width, height)
    buffer = ctypes.create_string_buffer(width * height * 4)
    GDI32.GetDIBits(
        mem_dc,
        hbitmap,
        0,
        height,
        buffer,
        ctypes.byref(info),
        DIB_RGB_COLORS,
    )
    return buffer.raw


def _analyze_bgra(raw: bytes) -> Dict[str, float]:
    total = len(raw) // 4
    if total == 0:
        return {"mean_luma": 0.0, "black_ratio": 1.0, "non_black_ratio": 0.0}

    black = 0
    luma_sum = 0

    for i in range(0, len(raw) - 3, 4):
        b, g, r = raw[i], raw[i + 1], raw[i + 2]
        luma = (r * 299 + g * 587 + b * 114) // 1000
        luma_sum += luma
        if r + g + b < 30:
            black += 1

    return {
        "mean_luma": luma_sum / total,
        "black_ratio": black / total,
        "non_black_ratio": (total - black) / total,
    }


def capture_via_bitblt(hwnd: int) -> Dict:
    width, height = _get_window_size(hwnd)

    window_dc = USER32.GetWindowDC(hwnd)
    if not window_dc:
        return {"ok": False, "error": "GetWindowDC thất bại"}

    mem_dc = GDI32.CreateCompatibleDC(window_dc)
    hbitmap = GDI32.CreateCompatibleBitmap(window_dc, width, height)
    if not mem_dc or not hbitmap:
        if window_dc:
            USER32.ReleaseDC(hwnd, window_dc)
        return {"ok": False, "error": "Không tạo được DC/bitmap"}

    old = GDI32.SelectObject(mem_dc, hbitmap)
    ok = GDI32.BitBlt(
        mem_dc,
        0,
        0,
        width,
        height,
        window_dc,
        0,
        0,
        SRCCOPY | CAPTUREBLT,
    )

    raw = _bits_from_hbitmap(mem_dc, hbitmap, width, height) if ok else b""
    GDI32.SelectObject(mem_dc, old)
    GDI32.DeleteObject(hbitmap)
    GDI32.DeleteDC(mem_dc)
    USER32.ReleaseDC(hwnd, window_dc)

    if not ok:
        return {"ok": False, "error": "BitBlt thất bại"}

    stats = _analyze_bgra(raw)
    return {"ok": True, "width": width, "height": height, "stats": stats}


def capture_via_printwindow(hwnd: int) -> Dict:
    width, height = _get_window_size(hwnd)

    screen_dc = USER32.GetDC(None)
    if not screen_dc:
        return {"ok": False, "error": "GetDC screen thất bại"}

    mem_dc = GDI32.CreateCompatibleDC(screen_dc)
    hbitmap = GDI32.CreateCompatibleBitmap(screen_dc, width, height)
    if not mem_dc or not hbitmap:
        USER32.ReleaseDC(None, screen_dc)
        return {"ok": False, "error": "Không tạo được DC/bitmap"}

    old = GDI32.SelectObject(mem_dc, hbitmap)

    brush = GDI32.CreateSolidBrush(0x00000000)
    rect = RECT(0, 0, width, height)
    USER32.FillRect(mem_dc, ctypes.byref(rect), brush)
    GDI32.DeleteObject(brush)

    ok = USER32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)

    raw = _bits_from_hbitmap(mem_dc, hbitmap, width, height) if ok else b""
    GDI32.SelectObject(mem_dc, old)
    GDI32.DeleteObject(hbitmap)
    GDI32.DeleteDC(mem_dc)
    USER32.ReleaseDC(None, screen_dc)

    if not ok:
        return {"ok": False, "error": "PrintWindow thất bại"}

    stats = _analyze_bgra(raw)
    return {"ok": True, "width": width, "height": height, "stats": stats}


def get_window_affinity(hwnd: int) -> Tuple[Optional[int], Optional[str]]:
    affinity = wintypes.DWORD()
    ok = USER32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity))
    if not ok:
        return None, "GetWindowDisplayAffinity không hoạt động trên Windows này"

    value = int(affinity.value)
    if value == WDA_EXCLUDEFROMCAPTURE:
        return value, "Đang chặn chụp màn hình (WDA_EXCLUDEFROMCAPTURE)"
    if value == WDA_MONITOR:
        return value, "WDA_MONITOR (không bảo vệ chống chụp)"
    return value, "WDA_NONE (KHÔNG bật chống chụp)"


def read_uia_text(hwnd: int) -> List[str]:
    """Đọc text qua UI Automation. Trả về danh sách text lấy được."""
    ps1_path = None
    try:
        script = r"""
param([IntPtr]$Handle)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$el = [System.Windows.Automation.AutomationElement]::FromHandle($Handle)
if ($null -eq $el) { exit 0 }

$all = New-Object System.Collections.Generic.List[string]

function Walk($node) {
    if ($null -eq $node) { return }
    $name = $node.Current.Name
    if ($name) { $all.Add($name) }

    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $child = $walker.GetFirstChild($node)
    while ($child -ne $null) {
        Walk $child
        $child = $walker.GetNextSibling($child)
    }
}

Walk $el
$all | Where-Object { $_ -ne '' } | Select-Object -Unique
"""

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".ps1",
            delete=False,
            encoding="utf-8",
        ) as handle:
            handle.write(script)
            ps1_path = Path(handle.name)

        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ps1_path),
                "-Handle",
                str(hwnd),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return lines
    except Exception as exc:
        print(f"[WARNING] UI Automation không chạy được: {exc}")
        return []
    finally:
        if ps1_path and ps1_path.exists():
            ps1_path.unlink(missing_ok=True)


# ---------------------------------------------------------------- report
def _print_section(title: str) -> None:
    print("\n" + "=" * 62)
    print(title)
    print("=" * 62)


def _print_capture_result(label: str, result: Dict) -> None:
    print(f"\n[{label}]")
    if not result.get("ok"):
        print(f"  Kết quả: KHÔNG lấy được ({result.get('error')})")
        return

    stats = result["stats"]
    visible = stats["non_black_ratio"] > 0.005 and stats["mean_luma"] > 3.0
    print(f"  Kích thước: {result.get('width')}x{result.get('height')}")
    print(f"  Độ sáng trung bình: {stats['mean_luma']:.2f}/255")
    print(f"  Tỉ lệ pixel không đen: {stats['non_black_ratio'] * 100:.2f}%")
    if visible:
        print("  => NGUY CƠ: NỘI DUNG ĐANG BỊ LỘ")
    else:
        print("  => OK: chỉ thấy màn đen / không lộ nội dung")


def run_scan(title: str, process: str, use_uia: bool) -> int:
    windows = list_windows()

    if not windows:
        print("[ERROR] Không tìm thấy cửa sổ nào đang mở.")
        return 1

    target = find_window(windows, title, process)
    if target is None:
        print("[ERROR] Không tìm thấy cửa sổ khớp với tiêu chí.")
        print("\nDanh sách cửa sổ đang mở:")
        for window in windows:
            print(
                f"  hwnd={window['hwnd']} | {window['process']} | "
                f"class={window['class']} | {window['title']}"
            )
        return 2

    print("Đang quét cửa sổ:")
    print(f"  Tiêu đề: {target['title']}")
    print(f"  Class:   {target['class']}")
    print(f"  Process: {target['process']}")
    print(f"  HWND:    {target['hwnd']}")

    hwnd = target["hwnd"]

    _print_section("1. Trạng thái SetWindowDisplayAffinity")
    affinity_value, affinity_text = get_window_affinity(hwnd)
    print(f"  Giá trị: {affinity_value}")
    print(f"  Trạng thái: {affinity_text}")

    _print_section("2. Chụp màn hình thường (GDI BitBlt)")
    result = capture_via_bitblt(hwnd)
    _print_capture_result("BitBlt", result)

    _print_section("3. PrintWindow + PW_RENDERFULLCONTENT")
    result = capture_via_printwindow(hwnd)
    _print_capture_result("PrintWindow", result)

    if use_uia:
        _print_section("4. UI Automation")
        texts = read_uia_text(hwnd)
        if texts:
            print(f"  Lấy được {len(texts)} chuỗi text.")
            print("  => NGUY CƠ: app đang lộ nội dung text qua UI Automation.")
            print("     Bạn nên render đề thi lên canvas/WebGL hoặc chặn UIA.")
            preview = texts[:10]
            for text in preview:
                print(f"    - {text[:100]}")
        else:
            print("  Không lấy được text qua UIA (hoặc PowerShell bị chặn).")

    _print_section("Kết luận nhanh")
    if affinity_value == WDA_EXCLUDEFROMCAPTURE:
        print("  - WDA: OK, đang chặn capture thông thường.")
    else:
        print("  - WDA: CẦN FIX, app chưa bật WDA_EXCLUDEFROMCAPTURE hoặc đã bị tắt.")
    print("  - Xem chi tiết từng mục bên trên để biết cần vá hướng nào.")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Quét lỗ hổng chống chụp màn hình của app thi")
    parser.add_argument("--list", action="store_true", help="Liệt kê cửa sổ đang mở")
    parser.add_argument("--title", default="", help="Một phần tiêu đề cửa sổ app thi")
    parser.add_argument("--process", default="", help="Một phần tên process, ví dụ exam.exe")
    parser.add_argument("--uia", action="store_true", help="Quét thêm bằng UI Automation")
    args = parser.parse_args()

    if args.list:
        windows = list_windows()
        print("Danh sách cửa sổ đang mở:\n")
        for window in windows:
            print(
                f"  hwnd={window['hwnd']} | {window['process']} | "
                f"class={window['class']} | {window['title']}"
            )
        return 0

    return run_scan(args.title, args.process, args.uia)


if __name__ == "__main__":
    raise SystemExit(main())

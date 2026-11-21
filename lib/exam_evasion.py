"""Giúp Stealth Helper không bị ExamLockdown quét/diệt.

ExamLockdown (https://github.com/squareomni-arch/watermark-tool) có hai cơ chế
gây ra hiện tượng "mở app thi là watermark biến mất":

1. WindowedAppScanner trong giờ thi gọi EnumWindows và diệt process sở hữu MỌI
   cửa sổ top-level đang hiện, kể cả WS_EX_TOOLWINDOW / layered overlay.
2. ProcessScanner cũng quét process con đang mở cửa sổ top-level (RemoveWindowedChildExemptions).

Module này:
  - Tìm process/cửa sổ của ExamLockdown.App.exe.
  - Khởi động lại Python hiện tại thành process con của ExamLockdown.App.exe
    bằng PROC_THREAD_ATTRIBUTE_PARENT_PROCESS. Khi đã là process con và overlay
    được gắn dưới dạng cửa sổ con (WatermarkOverlay.attach_to), ExamLockdown sẽ
    bỏ qua process này.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Dict, List, Optional, Tuple

if not sys.platform.startswith("win"):
    raise RuntimeError("exam_evasion chỉ chạy trên Windows")

PROCESS_CREATE_PROCESS = 0x0080
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

TH32CS_SNAPPROCESS = 0x00000002
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_NO_WINDOW = 0x08000000
PROC_THREAD_ATTRIBUTE_PARENT_PROCESS = 0x00020000

WDA_EXCLUDEFROMCAPTURE = 0x00000011

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_POPUP = 0x80000000
WS_CHILD = 0x40000000
WS_EX_TOPMOST = 0x00000008

SM_CXSCREEN = 0
SM_CYSCREEN = 1

MAX_PATH = 260


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", ctypes.c_wchar_p),
        ("lpDesktop", ctypes.c_wchar_p),
        ("lpTitle", ctypes.c_wchar_p),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", ctypes.c_uint16),
        ("cbReserved2", ctypes.c_uint16),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", ctypes.c_void_p),
        ("hStdOutput", ctypes.c_void_p),
        ("hStdError", ctypes.c_void_p),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", ctypes.c_void_p),
        ("hThread", ctypes.c_void_p),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_int32),
        ("top", ctypes.c_int32),
        ("right", ctypes.c_int32),
        ("bottom", ctypes.c_int32),
    ]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _load_libs():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]

    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]

    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]

    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)
    ]

    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t,
        ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p,
    ]

    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]

    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.CreateProcessW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, ctypes.c_wchar_p,
        ctypes.POINTER(STARTUPINFOEXW), ctypes.POINTER(PROCESS_INFORMATION),
    ]

    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]

    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]

    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
    ]

    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]

    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [
        wintypes.HWND, wintypes.LPWSTR, ctypes.c_int
    ]

    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [
        wintypes.HWND, wintypes.LPWSTR, ctypes.c_int
    ]

    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]

    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]

    user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.GetWindowDisplayAffinity.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
    ]

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetForegroundWindow.argtypes = []

    return kernel32, user32


KERNEL32, USER32 = _load_libs()


def find_process_by_name(process_name: str) -> Optional[int]:
    """Trả về PID đầu tiên khớp tên exe (không phân biệt hoa thường)."""
    process_name = process_name.lower()
    snapshot = KERNEL32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot:
        return None

    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not KERNEL32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None
        while True:
            if entry.szExeFile.lower() == process_name:
                return int(entry.th32ProcessID)
            if not KERNEL32.Process32NextW(snapshot, ctypes.byref(entry)):
                return None
    finally:
        KERNEL32.CloseHandle(snapshot)


def list_all_pids() -> List[int]:
    """Liệt kê mọi PID hiện tại bằng Toolhelp snapshot."""
    snapshot = KERNEL32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot:
        return []

    pids: List[int] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not KERNEL32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return pids
        while True:
            pids.append(int(entry.th32ProcessID))
            if not KERNEL32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        return pids
    finally:
        KERNEL32.CloseHandle(snapshot)


def get_process_name(pid: int) -> str:
    """Đọc tên exe của process theo PID. Không mở handle nếu không cần."""
    if pid <= 0:
        return ""

    snapshot = KERNEL32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot:
        return ""

    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not KERNEL32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return ""
        while True:
            if int(entry.th32ProcessID) == pid:
                return entry.szExeFile
            if not KERNEL32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        return ""
    finally:
        KERNEL32.CloseHandle(snapshot)


def find_processes_by_names(names) -> Dict[str, int]:
    """Trả về {tên exe thường: pid} của các process khớp danh sách cần tìm."""
    wanted = {str(n).lower() for n in names}
    result: Dict[str, int] = {}
    if not wanted:
        return result

    snapshot = KERNEL32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot:
        return result

    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not KERNEL32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return result
        while True:
            name = entry.szExeFile.lower()
            if name in wanted and name not in result:
                result[name] = int(entry.th32ProcessID)
            if not KERNEL32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        KERNEL32.CloseHandle(snapshot)

    return result


def describe_visible_window(hwnd: int) -> Optional[Dict]:
    """Đọc thông tin một cửa sổ top-level đang hiển thị."""
    if not USER32.IsWindowVisible(hwnd):
        return None

    rect = RECT()
    if not USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    width = max(0, int(rect.right - rect.left))
    height = max(0, int(rect.bottom - rect.top))
    if width <= 0 or height <= 0:
        return None

    screen_w = max(1, int(USER32.GetSystemMetrics(SM_CXSCREEN)))
    screen_h = max(1, int(USER32.GetSystemMetrics(SM_CYSCREEN)))
    screen_area = screen_w * screen_h

    pid = wintypes.DWORD()
    USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    title_buf = ctypes.create_unicode_buffer(256)
    USER32.GetWindowTextW(hwnd, title_buf, len(title_buf))
    class_buf = ctypes.create_unicode_buffer(256)
    USER32.GetClassNameW(hwnd, class_buf, len(class_buf))

    style = int(USER32.GetWindowLongPtrW(hwnd, GWL_STYLE))
    exstyle = int(USER32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))

    affinity = wintypes.DWORD()
    wda = (
        int(affinity.value)
        if USER32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity))
        else None
    )

    coverage = (width * height) / screen_area
    score = 0
    if wda == WDA_EXCLUDEFROMCAPTURE:
        score += 100
    if coverage >= 0.80:
        score += 45
    elif coverage >= 0.60:
        score += 25
    if exstyle & WS_EX_TOPMOST:
        score += 20
    if style & WS_POPUP:
        score += 15
    if "hwndwrapper" in class_buf.value.lower():
        score += 20
    title_lower = title_buf.value.lower()
    class_lower = class_buf.value.lower()
    if "examlockdown" in title_lower or "examlockdown" in class_lower:
        score += 120

    return {
        "hwnd": int(hwnd),
        "pid": int(pid.value),
        "title": title_buf.value.strip(),
        "class": class_buf.value.strip(),
        "w": width,
        "h": height,
        "coverage": round(coverage, 2),
        "topmost": bool(exstyle & WS_EX_TOPMOST),
        "popup": bool(style & WS_POPUP),
        "wda": wda,
        "score": score,
    }


def snapshot_visible_windows() -> List[Tuple[int, int]]:
    """Trả về (hwnd, pid) của mọi cửa sổ top-level đang hiển thị."""
    rows: List[Tuple[int, int]] = []

    @WNDENUMPROC
    def _enum(hwnd, _lparam):
        if not USER32.IsWindowVisible(hwnd):
            return True

        rect = RECT()
        if not USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:
            return True

        pid = wintypes.DWORD()
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rows.append((int(hwnd), int(pid.value)))
        return True

    USER32.EnumWindows(_enum, 0)
    return rows


def list_visible_window_scores(limit: int = 12) -> List[Dict]:
    """Liệt kê các cửa sổ top-level đang hiện, sắp xếp theo điểm nhận diện.

    Dùng để chẩn đoán khi `find_exam_window()` không thấy app thi.
    """
    rows: List[Dict] = []

    @WNDENUMPROC
    def _enum(hwnd, _lparam):
        info = describe_visible_window(hwnd)
        if info is not None:
            rows.append(info)
        return True

    USER32.EnumWindows(_enum, 0)
    rows.sort(key=lambda r: r.get("score", 0), reverse=True)
    return rows[: max(1, limit)]


def find_fullscreen_candidate_window() -> Optional[Tuple[int, int, str]]:
    """Tìm cửa sổ fullscreen top-level KHÔNG dựa vào tên/title/class/WDA.

    Dùng làm lớp phòng thủ: nếu app thi đổi tên và chưa bật WDA, chỉ cần nó
    chuyển sang fullscreen borderless/topmost là watermark phải ẩn ngay.
    Không dùng để attach/spawn process con vì có thể nhầm game/video fullscreen.
    """
    screen_w = max(1, int(USER32.GetSystemMetrics(SM_CXSCREEN)))
    screen_h = max(1, int(USER32.GetSystemMetrics(SM_CYSCREEN)))
    screen_area = screen_w * screen_h
    best: Optional[Tuple[int, int, str]] = None
    best_area = 0

    # Chỉ coi là fullscreen candidate nếu rất giống kiosk WPF:
    #  - class HwndWrapper[...] (WPF), hoặc
    #  - cửa sổ đang bật WDA_EXCLUDEFROMCAPTURE.
    # Không dùng generic fullscreen vì Settings, Windows Input Experience,
    # browser/video fullscreen đều phủ 100% và gây false-positive.
    ignored_classes = {
        "progman", "workerw", "shell_traywnd", "dv2controlhost",
        "edgeuiinputtopwndclass", "windows.ui.core.corewindow",
        "applicationframeWindow", "chrome_widgetwin_1",
    }

    @WNDENUMPROC
    def _enum(hwnd, _lparam):
        nonlocal best, best_area
        if not USER32.IsWindowVisible(hwnd):
            return True

        rect = RECT()
        if not USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = max(0, int(rect.right - rect.left))
        height = max(0, int(rect.bottom - rect.top))
        if width <= 0 or height <= 0:
            return True

        class_buf = ctypes.create_unicode_buffer(256)
        USER32.GetClassNameW(hwnd, class_buf, len(class_buf))
        class_name = class_buf.value.lower()
        if class_name in ignored_classes:
            return True

        style = int(USER32.GetWindowLongPtrW(hwnd, GWL_STYLE))
        exstyle = int(USER32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
        if style & WS_CHILD:
            return True

        coverage = (width * height) / screen_area
        if coverage < 0.80:
            return True

        affinity = wintypes.DWORD()
        has_wda = (
            USER32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity))
            and int(affinity.value) == WDA_EXCLUDEFROMCAPTURE
        )
        is_wpf = class_name.startswith("hwndwrapper")

        if not has_wda and not is_wpf:
            return True

        pid = wintypes.DWORD()
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        area = width * height
        if area > best_area:
            best_area = area
            best = (
                int(hwnd),
                int(pid.value),
                f"fullscreen:{coverage:.0%},wda={has_wda},wpf={is_wpf}",
            )
        return True

    USER32.EnumWindows(_enum, 0)
    return best


def get_foreground_window() -> Optional[Tuple[int, int, str]]:
    """Đọc cửa sổ foreground hiện tại và mô tả ngắn.

    Dùng làm tín hiệu tức thời: app thi vào bài là cửa sổ fullscreen được
    đưa lên foreground; bắt foreground nhanh hơn EnumWindows một chút trong
    các tình huống đua với strict scan.
    """
    hwnd = int(USER32.GetForegroundWindow() or 0)
    if not hwnd:
        return None
    info = describe_visible_window(hwnd)
    if info is None:
        return None
    return hwnd, int(info["pid"]), (
        f"foreground,title={info['title']!r},class={info['class']!r},"
        f"coverage={info['coverage']},topmost={info['topmost']},popup={info['popup']}"
    )


def find_window_for_process(pid: int) -> Optional[int]:
    """Tìm top-level window đang hiện, diện tích lớn nhất của process."""
    best_hwnd: Optional[int] = None
    best_area = 0

    @WNDENUMPROC
    def _enum(hwnd, _lparam):
        nonlocal best_hwnd, best_area
        if not USER32.IsWindowVisible(hwnd):
            return True

        owner = wintypes.DWORD()
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if int(owner.value) != pid:
            return True

        # Không import RECT từ overlay để tránh lệ thuộc; lấy kích thước qua
        # GetWindowRect bằng ctypes.
        rect = RECT()
        if not USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        area = max(0, int(rect.right - rect.left)) * max(0, int(rect.bottom - rect.top))
        if area > best_area:
            best_area = area
            best_hwnd = int(hwnd)
        return True

    USER32.EnumWindows(_enum, 0)
    return best_hwnd


def find_exam_window() -> Optional[Tuple[int, int, str]]:
    """Tìm cửa sổ app thi đang chạy kiosk mà KHÔNG phụ thuộc vào tên exe.

    Trả về (hwnd, pid, reason) nếu tìm thấy.

    Heuristic:
      - WDA_EXCLUDEFROMCAPTURE: ExamLockdown (và nhiều app thi khác) bật flag này
        ngay khi vào kiosk. Đây là tín hiệu mạnh nhất, độc lập với tên exe.
      - Cửa sổ phủ gần toàn màn hình + WS_EX_TOPMOST + WS_POPUP (borderless kiosk):
        vẫn nhận diện được sau khi tool tắt WDA để chụp màn hình.
      - Title/class chứa "ExamLockdown" hoặc class WPF "HwndWrapper": tăng độ tin cậy.
    """
    screen_w = max(1, int(USER32.GetSystemMetrics(SM_CXSCREEN)))
    screen_h = max(1, int(USER32.GetSystemMetrics(SM_CYSCREEN)))
    screen_area = screen_w * screen_h

    best: Optional[Tuple[int, int, str]] = None
    best_score = 0

    @WNDENUMPROC
    def _enum(hwnd, _lparam):
        nonlocal best, best_score
        if not USER32.IsWindowVisible(hwnd):
            return True

        rect = RECT()
        if not USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:
            return True

        pid = wintypes.DWORD()
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) <= 0:
            return True

        title_buf = ctypes.create_unicode_buffer(512)
        USER32.GetWindowTextW(hwnd, title_buf, len(title_buf))
        title = title_buf.value.strip().lower()

        class_buf = ctypes.create_unicode_buffer(256)
        USER32.GetClassNameW(hwnd, class_buf, len(class_buf))
        class_name = class_buf.value.lower()

        style = int(USER32.GetWindowLongPtrW(hwnd, GWL_STYLE))
        exstyle = int(USER32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))

        # EnumWindows đã loại child window; nhưng giữ guard phòng thân.
        if style & WS_CHILD:
            return True

        score = 0
        reasons = []

        affinity = wintypes.DWORD()
        if USER32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity)):
            if int(affinity.value) == WDA_EXCLUDEFROMCAPTURE:
                score += 100
                reasons.append("WDA_EXCLUDEFROMCAPTURE")

        area = width * height
        coverage = area / screen_area
        if coverage >= 0.80:
            score += 45
            reasons.append(f"fullscreen:{coverage:.0%}")
        elif coverage >= 0.60:
            score += 25

        if exstyle & WS_EX_TOPMOST:
            score += 20
            reasons.append("topmost")

        if style & WS_POPUP:
            score += 15
            reasons.append("popup")

        # WPF cửa sổ kiosk thường có class dạng HwndWrapper[...].
        if "hwndwrapper" in class_name:
            score += 20
            reasons.append("wpf")

        if "examlockdown" in title or "examlockdown" in class_name:
            score += 120
            reasons.append("examlockdown")

        # WDA_EXCLUDEFROMCAPTURE hoặc marker ExamLockdown => chắc chắn là app thi.
        # Không có hai tín hiệu đó thì bắt buộc phải là fullscreen borderless
        # topmost WPF để tránh nhầm trình duyệt/video fullscreen.
        if score < 85:
            return True

        if score > best_score:
            best_score = score
            best = (int(hwnd), int(pid.value), ", ".join(reasons) or f"score:{score}")

        return True

    USER32.EnumWindows(_enum, 0)
    return best


def get_parent_pid(pid: int) -> Optional[int]:
    """Đọc PID cha thực tế từ Toolhelp32 snapshot."""
    snapshot = KERNEL32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot:
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not KERNEL32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None
        while True:
            if int(entry.th32ProcessID) == pid:
                parent = int(entry.th32ParentProcessID)
                return parent if parent != pid else None
            if not KERNEL32.Process32NextW(snapshot, ctypes.byref(entry)):
                return None
    finally:
        KERNEL32.CloseHandle(snapshot)


def spawn_as_child_of(
    parent_pid: int,
    extra_argv: Optional[List[str]] = None,
    argv: Optional[List[str]] = None,
) -> int:
    """Chạy một process con của parent_pid.

    Mặc định chạy lại main.py của Stealth Helper. Nếu `argv` được truyền thì
    dùng đúng argv đó (dùng cho chẩn đoán).

    Trả về PID của process mới. Process hiện tại nên thoát ngay sau đó.
    """
    parent = KERNEL32.OpenProcess(PROCESS_CREATE_PROCESS, False, parent_pid)
    if not parent:
        raise OSError(f"OpenProcess(parent={parent_pid}) thất bại (code {ctypes.get_last_error()})")

    attrib_list = None
    proc_info = PROCESS_INFORMATION()
    try:
        startup = STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)

        size = ctypes.c_size_t()
        KERNEL32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))

        attrib_list = ctypes.create_string_buffer(size.value)
        startup.lpAttributeList = ctypes.cast(attrib_list, ctypes.c_void_p)
        if not KERNEL32.InitializeProcThreadAttributeList(
            startup.lpAttributeList, 1, 0, ctypes.byref(size)
        ):
            raise OSError(f"InitializeProcThreadAttributeList thất bại (code {ctypes.get_last_error()})")

        handle_value = ctypes.c_void_p(parent)
        if not KERNEL32.UpdateProcThreadAttribute(
            startup.lpAttributeList,
            0,
            PROC_THREAD_ATTRIBUTE_PARENT_PROCESS,
            ctypes.byref(handle_value),
            ctypes.sizeof(handle_value),
            None,
            None,
        ):
            raise OSError(f"UpdateProcThreadAttribute thất bại (code {ctypes.get_last_error()})")

        base_dir = Path(__file__).resolve().parent.parent
        if argv is None:
            argv = [sys.executable, str(base_dir / "main.py"), "--exam-child"] + list(extra_argv or [])
        command_line = subprocess.list2cmdline(argv)

        if not KERNEL32.CreateProcessW(
            None,
            command_line,
            None,
            None,
            False,
            EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW,
            None,
            str(base_dir),
            ctypes.byref(startup),
            ctypes.byref(proc_info),
        ):
            raise OSError(f"CreateProcessW thất bại (code {ctypes.get_last_error()})")

        pid = int(proc_info.dwProcessId)
        return pid
    finally:
        if proc_info.hThread:
            KERNEL32.CloseHandle(proc_info.hThread)
        if proc_info.hProcess:
            KERNEL32.CloseHandle(proc_info.hProcess)
        if startup.lpAttributeList:
            KERNEL32.DeleteProcThreadAttributeList(startup.lpAttributeList)
        if attrib_list is not None:
            del attrib_list
        KERNEL32.CloseHandle(parent)

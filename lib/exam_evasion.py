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
from typing import List, Optional, Tuple

if not sys.platform.startswith("win"):
    raise RuntimeError("exam_evasion chỉ chạy trên Windows")

PROCESS_CREATE_PROCESS = 0x0080
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

TH32CS_SNAPPROCESS = 0x00000002
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_NO_WINDOW = 0x08000000
PROC_THREAD_ATTRIBUTE_PARENT_PROCESS = 0x00020000

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


def spawn_as_child_of(parent_pid: int, extra_argv: Optional[List[str]] = None) -> int:
    """Chạy lại main.py trong một process con của parent_pid.

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

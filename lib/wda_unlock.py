"""Tắt WDA_EXCLUDEFROMCAPTURE của cửa sổ thuộc process khác (Windows).

Vì `SetWindowDisplayAffinity` bắt buộc cửa sổ phải thuộc chính process đang
gọi, cho nên không thể gọi chéo từ process của tool. Module này tiêm một đoạn
shellcode nhỏ vào process sở hữu cửa sổ, rồi chạy trong process đó lệnh:

    SetWindowDisplayAffinity(hwnd, WDA_NONE);

Sau khi lệnh này chạy xong, GDI BitBlt, PrintWindow, DXGI Desktop Duplication,
Windows.Graphics.Capture và PIL ImageGrab đều thấy cửa sổ bình thường -- cho
tới khi app target gọi lại `SetWindowDisplayAffinity(...)` lần nữa.

Cách dùng:
    python lib/wda_unlock.py --list
    python lib/wda_unlock.py --title "Tên cửa sổ"
    python lib/wda_unlock.py --process "exam.exe"
    python lib/wda_unlock.py --all

Chỉ chạy trên Windows. Tool cần chạy với quyền đủ để mở process target
(thường là chạy Administrator nếu app thi cũng chạy elevated).
"""

from __future__ import annotations

import argparse
import ctypes
import struct
import sys
from ctypes import wintypes
from typing import Dict, List, Optional, Tuple

if not sys.platform.startswith("win"):
    print("[ERROR] Module chỉ chạy trên Windows.")
    raise SystemExit(1)

# ---------------------------------------------------------------- constants
WDA_NONE = 0
WDA_EXCLUDEFROMCAPTURE = 0x00000011

PROCESS_CREATE_THREAD = 0x0002
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020

MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_EXECUTE_READWRITE = 0x40

TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010

MAX_MODULE_NAME32 = 255
MAX_PATH = 260
WAIT_TIME_MS = 8000

IS_64BIT_PYTHON = ctypes.sizeof(ctypes.c_void_p) == 8


# ---------------------------------------------------------------- Win32 types
class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_int32),
        ("top", ctypes.c_int32),
        ("right", ctypes.c_int32),
        ("bottom", ctypes.c_int32),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", ctypes.c_void_p),
        ("szModule", ctypes.c_wchar * (MAX_MODULE_NAME32 + 1)),
        ("szExePath", ctypes.c_wchar * MAX_PATH),
    ]


class IMAGE_DOS_HEADER(ctypes.Structure):
    _fields_ = [
        ("e_magic", ctypes.c_uint16),
        ("e_cblp", ctypes.c_uint16),
        ("e_cp", ctypes.c_uint16),
        ("e_crlc", ctypes.c_uint16),
        ("e_cparhdr", ctypes.c_uint16),
        ("e_minalloc", ctypes.c_uint16),
        ("e_maxalloc", ctypes.c_uint16),
        ("e_ss", ctypes.c_uint16),
        ("e_sp", ctypes.c_uint16),
        ("e_csum", ctypes.c_uint16),
        ("e_ip", ctypes.c_uint16),
        ("e_cs", ctypes.c_uint16),
        ("e_lfarlc", ctypes.c_uint16),
        ("e_ovno", ctypes.c_uint16),
        ("e_res", ctypes.c_uint16 * 4),
        ("e_oemid", ctypes.c_uint16),
        ("e_oeminfo", ctypes.c_uint16),
        ("e_res2", ctypes.c_uint16 * 10),
        ("e_lfanew", ctypes.c_int32),
    ]


class IMAGE_EXPORT_DIRECTORY(ctypes.Structure):
    _fields_ = [
        ("Characteristics", wintypes.DWORD),
        ("TimeDateStamp", wintypes.DWORD),
        ("MajorVersion", ctypes.c_uint16),
        ("MinorVersion", ctypes.c_uint16),
        ("Name", wintypes.DWORD),
        ("Base", wintypes.DWORD),
        ("NumberOfFunctions", wintypes.DWORD),
        ("NumberOfNames", wintypes.DWORD),
        ("AddressOfFunctions", wintypes.DWORD),
        ("AddressOfNames", wintypes.DWORD),
        ("AddressOfNameOrdinals", wintypes.DWORD),
    ]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


# ---------------------------------------------------------------- Win32 setup
def _load_libs():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]

    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]

    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [
        wintypes.HWND, wintypes.LPWSTR, ctypes.c_int
    ]

    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [
        wintypes.HWND, wintypes.LPWSTR, ctypes.c_int
    ]

    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
    ]

    user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.GetWindowDisplayAffinity.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
    ]

    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]

    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD
    ]

    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    kernel32.VirtualAllocEx.restype = ctypes.c_void_p
    kernel32.VirtualAllocEx.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
        wintypes.DWORD, wintypes.DWORD,
    ]

    kernel32.VirtualFreeEx.restype = wintypes.BOOL
    kernel32.VirtualFreeEx.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD,
    ]

    kernel32.WriteProcessMemory.restype = wintypes.BOOL
    kernel32.WriteProcessMemory.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
    ]

    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    kernel32.ReadProcessMemory.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
    ]

    kernel32.CreateRemoteThread.restype = ctypes.c_void_p
    kernel32.CreateRemoteThread.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]

    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]

    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]

    kernel32.Module32FirstW.restype = wintypes.BOOL
    kernel32.Module32FirstW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(MODULEENTRY32W)
    ]

    kernel32.Module32NextW.restype = wintypes.BOOL
    kernel32.Module32NextW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(MODULEENTRY32W)
    ]

    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]

    kernel32.GetProcAddress.restype = ctypes.c_void_p
    kernel32.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]

    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]

    if hasattr(kernel32, "IsWow64Process"):
        kernel32.IsWow64Process.restype = wintypes.BOOL
        kernel32.IsWow64Process.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL)
        ]

    return user32, kernel32


USER32, KERNEL32 = _load_libs()


# ---------------------------------------------------------------- shellcode
def _build_shellcode(hwnd: int, func_addr: int, target_64: bool) -> bytes:
    """Tạo shellcode chạy `SetWindowDisplayAffinity(hwnd, WDA_NONE)` rồi thoát."""
    if target_64:
        # sub rsp,0x28; mov rcx,hwnd; xor edx,edx; mov rax,func; call rax;
        # add rsp,0x28; xor eax,eax; ret
        return (
            b"\x48\x83\xEC\x28"
            b"\x48\xB9" + struct.pack("<Q", hwnd) +
            b"\xBA\x00\x00\x00\x00"
            b"\x48\xB8" + struct.pack("<Q", func_addr) +
            b"\xFF\xD0"
            b"\x48\x83\xC4\x28"
            b"\x33\xC0"
            b"\xC3"
        )

    if hwnd > 0xFFFFFFFF:
        raise ValueError("HWND 64-bit không thể dùng cho shellcode 32-bit")
    if func_addr > 0xFFFFFFFF:
        raise ValueError("Địa chỉ hàm 64-bit không thể dùng cho shellcode 32-bit")

    # push 0; push hwnd; mov eax,func; call eax; xor eax,eax; ret 4
    return (
        b"\x6A\x00"
        b"\x68" + struct.pack("<I", hwnd) +
        b"\xB8" + struct.pack("<I", func_addr) +
        b"\xFF\xD0"
        b"\x33\xC0"
        b"\xC2\x04\x00"
    )


def _local_set_window_display_affinity_addr() -> int:
    return ctypes.cast(USER32.SetWindowDisplayAffinity, ctypes.c_void_p).value


# ---------------------------------------------------------------- remote PE export
def _open_process(pid: int) -> int:
    access = (
        PROCESS_CREATE_THREAD
        | PROCESS_QUERY_INFORMATION
        | PROCESS_VM_OPERATION
        | PROCESS_VM_READ
        | PROCESS_VM_WRITE
    )
    handle = KERNEL32.OpenProcess(access, False, pid)
    if not handle:
        raise OSError(f"OpenProcess pid={pid} thất bại (GetLastError={ctypes.get_last_error()})")
    return int(handle)


def _read_remote(hproc: int, address: int, size: int) -> bytes:
    buffer = ctypes.create_string_buffer(max(1, size))
    read = ctypes.c_size_t()
    ok = KERNEL32.ReadProcessMemory(
        hproc, ctypes.c_void_p(address), buffer, size, ctypes.byref(read)
    )
    if not ok or read.value != size:
        raise OSError(
            f"ReadProcessMemory 0x{address:X} size={size} thất bại "
            f"(GetLastError={ctypes.get_last_error()})"
        )
    return buffer.raw


def _read_u16(hproc: int, address: int) -> int:
    return struct.unpack("<H", _read_remote(hproc, address, 2))[0]


def _read_u32(hproc: int, address: int) -> int:
    return struct.unpack("<I", _read_remote(hproc, address, 4))[0]


def _read_cstring(hproc: int, address: int, max_chars: int = 160) -> str:
    raw = bytearray()
    for offset in range(0, max_chars, 32):
        chunk = _read_remote(hproc, address + offset, 32)
        raw.extend(chunk)
        if b"\x00" in chunk:
            break
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore")


def _find_module_base(pid: int, module_name: str) -> int:
    flags = TH32CS_SNAPMODULE
    # Nếu tool 64-bit mà target 32-bit, cần flag SNAPMODULE32 để thấy module.
    if IS_64BIT_PYTHON:
        flags |= TH32CS_SNAPMODULE32

    snapshot = KERNEL32.CreateToolhelp32Snapshot(flags, pid)
    if not snapshot:
        raise OSError(
            f"CreateToolhelp32Snapshot pid={pid} thất bại "
            f"(GetLastError={ctypes.get_last_error()})"
        )

    try:
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(MODULEENTRY32W)
        found = False
        if KERNEL32.Module32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                if entry.szModule.lower() == module_name.lower():
                    found = True
                    break
                if not KERNEL32.Module32NextW(snapshot, ctypes.byref(entry)):
                    break
        if not found:
            raise RuntimeError(f"Không tìm thấy module {module_name} trong pid={pid}")
        return int(entry.modBaseAddr)
    finally:
        KERNEL32.CloseHandle(snapshot)


def _resolve_remote_export(
    hproc: int, pid: int, target_64: bool, module_name: str, func_name: str
) -> int:
    base = _find_module_base(pid, module_name)

    dos = IMAGE_DOS_HEADER()
    raw_dos = _read_remote(hproc, base, ctypes.sizeof(dos))
    ctypes.memmove(ctypes.byref(dos), raw_dos, len(raw_dos))
    if dos.e_magic != 0x5A4D:
        raise RuntimeError(f"Module {module_name} không có MZ header")

    nt_sig_off = base + int(dos.e_lfanew)
    if _read_u32(hproc, nt_sig_off) != 0x00004550:
        raise RuntimeError(f"Module {module_name} không có PE signature")

    opt_off = nt_sig_off + 4 + 20  # Signature + IMAGE_FILE_HEADER
    magic = _read_u16(hproc, opt_off)
    expected_magic = 0x20B if target_64 else 0x10B
    if magic != expected_magic:
        raise RuntimeError(
            f"Module {module_name} PE magic không khớp target_64={target_64}"
        )

    # DataDirectory[0] = Export Table. Offset trong optional header:
    # PE32 = 96, PE32+ = 112.
    export_rva_off = opt_off + (112 if target_64 else 96)
    export_rva = _read_u32(hproc, export_rva_off)
    if not export_rva:
        raise RuntimeError(f"Module {module_name} không có export table")

    export_va = base + export_rva
    ed = IMAGE_EXPORT_DIRECTORY()
    raw_ed = _read_remote(hproc, export_va, ctypes.sizeof(ed))
    ctypes.memmove(ctypes.byref(ed), raw_ed, len(raw_ed))

    names_va = base + ed.AddressOfNames
    funcs_va = base + ed.AddressOfFunctions
    ords_va = base + ed.AddressOfNameOrdinals

    for i in range(ed.NumberOfNames):
        name_rva = _read_u32(hproc, names_va + i * 4)
        name_va = base + name_rva
        name = _read_cstring(hproc, name_va, 160)
        if name == func_name:
            ordinal = _read_u16(hproc, ords_va + i * 2)
            func_rva = _read_u32(hproc, funcs_va + ordinal * 4)
            return base + func_rva

    raise RuntimeError(f"Không tìm thấy export {module_name}!{func_name}")


# ---------------------------------------------------------------- process utils
def _pid_from_hwnd(hwnd: int) -> int:
    pid = wintypes.DWORD()
    USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _is_64bit_process(pid: int) -> bool:
    """Trả về True nếu process target là 64-bit."""
    if not hasattr(KERNEL32, "IsWow64Process"):
        # Python 32-bit trên Windows 32-bit: không có process 64-bit.
        return False

    hproc = KERNEL32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
    if not hproc:
        raise OSError(f"Không mở được process pid={pid} để kiểm tra kiến trúc")
    try:
        wow64 = wintypes.BOOL()
        if not KERNEL32.IsWow64Process(hproc, ctypes.byref(wow64)):
            raise OSError(f"IsWow64Process thất bại (GetLastError={ctypes.get_last_error()})")
        return not bool(wow64.value)
    finally:
        KERNEL32.CloseHandle(hproc)


def _run_shellcode(pid: int, code: bytes) -> None:
    hproc = _open_process(pid)
    remote = None
    try:
        remote = KERNEL32.VirtualAllocEx(
            hproc, None, len(code), MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE
        )
        if not remote:
            raise OSError(
                f"VirtualAllocEx thất bại (GetLastError={ctypes.get_last_error()})"
            )

        written = ctypes.c_size_t()
        if not KERNEL32.WriteProcessMemory(
            hproc, remote, code, len(code), ctypes.byref(written)
        ) or written.value != len(code):
            raise OSError(
                f"WriteProcessMemory thất bại (GetLastError={ctypes.get_last_error()})"
            )

        thread_id = wintypes.DWORD()
        hthread = KERNEL32.CreateRemoteThread(
            hproc, None, 0, remote, None, 0, ctypes.byref(thread_id)
        )
        if not hthread:
            raise OSError(
                f"CreateRemoteThread thất bại (GetLastError={ctypes.get_last_error()})"
            )

        KERNEL32.WaitForSingleObject(hthread, WAIT_TIME_MS)
        KERNEL32.CloseHandle(hthread)
    finally:
        if remote:
            KERNEL32.VirtualFreeEx(hproc, remote, 0, MEM_RELEASE)
        KERNEL32.CloseHandle(hproc)


def unlock_window(hwnd: int) -> None:
    """Tắt WDA_EXCLUDEFROMCAPTURE cho một cửa sổ cụ thể."""
    pid = _pid_from_hwnd(hwnd)
    if pid <= 0:
        raise RuntimeError("Không lấy được PID của cửa sổ")

    target_64 = _is_64bit_process(pid)

    if not IS_64BIT_PYTHON and target_64:
        # Tool 32-bit không thể tiêm shellcode 64-bit bằng CreateRemoteThread.
        raise RuntimeError(
            "Tool đang chạy Python 32-bit, không thể tiêm vào process 64-bit. "
            "Hãy chạy lại bằng Python 64-bit."
        )

    hproc = _open_process(pid)
    try:
        try:
            func_addr = _resolve_remote_export(
                hproc, pid, target_64, "user32.dll", "SetWindowDisplayAffinity"
            )
        except Exception:
            # Fallback cho trường hợp cùng kiến trúc: system DLL thường map
            # chung base address trong cùng một phiên Windows.
            if target_64 != IS_64BIT_PYTHON:
                raise
            func_addr = _local_set_window_display_affinity_addr()
    finally:
        KERNEL32.CloseHandle(hproc)

    code = _build_shellcode(hwnd, func_addr, target_64)
    _run_shellcode(pid, code)


def get_window_affinity(hwnd: int) -> Optional[int]:
    affinity = wintypes.DWORD()
    if not USER32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity)):
        return None
    return int(affinity.value)


def _process_name_from_pid(pid: int) -> str:
    if pid <= 0:
        return ""

    hproc = KERNEL32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
    if not hproc:
        return ""

    try:
        length = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(length.value)
        ok = KERNEL32.QueryFullProcessImageNameW(
            hproc, 0, buffer, ctypes.byref(length)
        )
        if not ok:
            return ""
        return buffer.value.rsplit("\\", 1)[-1]
    finally:
        KERNEL32.CloseHandle(hproc)


def list_windows() -> List[Dict]:
    """Trả về danh sách cửa sổ top-level đang hiển thị."""
    windows: List[Dict] = []

    @WNDENUMPROC
    def _enum(hwnd, _lparam):
        if not USER32.IsWindowVisible(hwnd):
            return True

        title_buf = ctypes.create_unicode_buffer(512)
        USER32.GetWindowTextW(hwnd, title_buf, len(title_buf))
        title = title_buf.value.strip()

        class_buf = ctypes.create_unicode_buffer(256)
        USER32.GetClassNameW(hwnd, class_buf, len(class_buf))

        pid = wintypes.DWORD()
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        windows.append(
            {
                "hwnd": int(hwnd),
                "title": title,
                "class": class_buf.value,
                "pid": int(pid.value),
                "process": _process_name_from_pid(int(pid.value)),
                "affinity": get_window_affinity(int(hwnd)),
            }
        )
        return True

    USER32.EnumWindows(_enum, 0)
    return windows


def unlock_all_excluded_windows(quiet: bool = False) -> List[Dict]:
    """Duyệt mọi cửa sổ đang bật WDA_EXCLUDEFROMCAPTURE và tắt flag đó."""
    unlocked: List[Dict] = []
    for window in list_windows():
        if window.get("affinity") == WDA_EXCLUDEFROMCAPTURE:
            try:
                unlock_window(window["hwnd"])
                window["unlocked"] = True
                unlocked.append(window)
                if not quiet:
                    print(
                        f"  [OK] Đã tắt WDA: pid={window['pid']} "
                        f"title='{window['title'] or window['class']}'"
                    )
            except Exception as exc:
                window["unlocked"] = False
                window["error"] = str(exc)
                if not quiet:
                    print(
                        f"  [FAIL] pid={window['pid']} "
                        f"title='{window['title'] or window['class']}': {exc}"
                    )
    return unlocked


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tắt WDA_EXCLUDEFROMCAPTURE của cửa sổ app khác để tool chụp màn hình được."
    )
    parser.add_argument("--list", action="store_true", help="Liệt kê cửa sổ và trạng thái WDA")
    parser.add_argument("--title", default="", help="Một phần tiêu đề cửa sổ")
    parser.add_argument("--process", default="", help="Một phần tên process, ví dụ exam.exe")
    parser.add_argument("--all", action="store_true", help="Tắt WDA cho mọi cửa sổ đang chặn")
    args = parser.parse_args()

    windows = list_windows()

    if args.list:
        print("Danh sách cửa sổ đang mở:\n")
        for window in windows:
            aff = window.get("affinity")
            aff_text = {
                WDA_NONE: "WDA_NONE",
                WDA_EXCLUDEFROMCAPTURE: "WDA_EXCLUDEFROMCAPTURE",
            }.get(aff, str(aff))
            print(
                f"  hwnd={window['hwnd']} | pid={window['pid']} | "
                f"class={window['class']} | affinity={aff_text} | {window['title']}"
            )
        return 0

    if args.all:
        unlock_all_excluded_windows()
        return 0

    target = find_window(windows, args.title, args.process)
    if target is None:
        print("[ERROR] Không tìm thấy cửa sổ khớp. Dùng --list để xem danh sách.")
        return 2

    print(
        f"Đang tắt WDA cho: hwnd={target['hwnd']} | pid={target['pid']} | "
        f"class={target['class']} | {target['title']}"
    )
    try:
        unlock_window(target["hwnd"])
        print("[OK] Đã tắt WDA_EXCLUDEFROMCAPTURE. Giờ có thể chụp màn hình.")
        return 0
    except Exception as exc:
        print(f"[FAIL] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

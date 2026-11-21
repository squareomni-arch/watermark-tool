#!/usr/bin/env python3
"""Chẩn đoán vì sao hint_layer bị ExamLockdown kill trên máy Windows.

Chạy thủ công TRƯỚC KHI mở app thi, hoặc ngay khi app thi đang mở:

    python exam_diagnostic.py

Script sẽ in:
  1. Python hiện tại (có phải pythonw.exe không, nằm ở đâu).
  2. Cửa sổ app thi mà lib/exam_evasion.py phát hiện được (không cần tên exe).
  3. Kết quả gắn thử overlay thành cửa sổ con của app thi.
  4. Kết quả spawn thử một process con của app thi (chỉ chạy vài giây rồi thoát).

Chỉ chạy trên Windows.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

if not sys.platform.startswith("win"):
    print("[ERROR] Chỉ chạy trên Windows.")
    raise SystemExit(1)

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from lib.exam_evasion import (  # noqa: E402
    find_exam_window,
    get_parent_pid,
    spawn_as_child_of,
)

_IS_PYTHONW = os.path.basename(sys.executable).lower() == "pythonw.exe"


def _open_process_success(pid: int) -> bool:
    PROCESS_CREATE_PROCESS = 0x0080
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.OpenProcess(PROCESS_CREATE_PROCESS, False, pid)
    if h:
        k32.CloseHandle(h)
        return True
    return False


def main() -> int:
    print("=" * 70)
    print(" SESSION ASSISTANT - EXAM LOCKDOWN DIAGNOSTIC")
    print("=" * 70)
    print(f" Current PID   : {os.getpid()}")
    print(f" sys.executable: {sys.executable}")
    print(f" Is pythonw    : {_IS_PYTHONW}")
    print(f" Base dir      : {BASE_DIR}")
    print()

    found = find_exam_window()
    if found is None:
        print("[FAIL] find_exam_window() KHÔNG tìm thấy app thi.")
        print("       Mở app thi lên rồi chạy lại script này.")
        return 2

    exam_hwnd, exam_pid, reason = found
    print("[OK] Tìm thấy app thi:")
    print(f"  HWND : {exam_hwnd}")
    print(f"  PID  : {exam_pid}")
    print(f"  Reason: {reason}")
    print(f"  OpenProcess(PROCESS_CREATE_PROCESS): {_open_process_success(exam_pid)}")
    print()

    from lib.overlay import DesktopHintLayer

    print("Đang gắn thử hint_layer vào cửa sổ app thi...")
    overlay = DesktopHintLayer({"text": "DIAG", "corner": "top_right"})
    try:
        ok = overlay.attach_to(exam_hwnd)
        print(f"  attach_to()          : {ok}")
        print(f"  overlay.is_child     : {overlay.is_child}")
        print(f"  overlay.hwnd         : {overlay.hwnd}")
        if overlay.hwnd:
            u32 = ctypes.WinDLL("user32")
            u32.GetParent.restype = ctypes.c_void_p
            u32.GetParent.argtypes = [ctypes.c_void_p]
            parent = int(u32.GetParent(ctypes.c_void_p(overlay.hwnd)) or 0)
            print(f"  GetParent(overlay)   : {parent}")
            print(f"  Match exam HWND      : {parent == exam_hwnd}")
        print()
        if ok:
            overlay.show("DIAG OK")
            time.sleep(1.0)
            overlay.hide()
        overlay.detach_from_parent()
    finally:
        overlay.destroy()

    print("Đang thử tạo process con của app thi trong 3 giây...")
    probe_argv = [
        sys.executable,
        "-c",
        "import time,sys; time.sleep(3); sys.exit(0)",
    ]
    try:
        child_pid = spawn_as_child_of(exam_pid, argv=probe_argv)
        print(f"  Child PID         : {child_pid}")
        time.sleep(1.0)
        parent = get_parent_pid(child_pid)
        print(f"  Toolhelp parent   : {parent}")
        print(f"  Match exam PID    : {parent == exam_pid}")
        time.sleep(3.0)
    except Exception as exc:
        print(f"  [FAIL] spawn_as_child_of lỗi: {exc}")

    print()
    print("=" * 70)
    print("Gửi lại toàn bộ output này cho developer.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except SystemExit as exc:
        exit_code = int(exc.code or 0)
    except Exception:
        import traceback

        traceback.print_exc()
        exit_code = 1

    try:
        input("\nNhấn Enter để thoát...")
    except (EOFError, KeyboardInterrupt):
        pass

    raise SystemExit(exit_code)

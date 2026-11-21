#!/usr/bin/env python3
"""Ghi log chẩn đoán ExamLockdown vào file mà KHÔNG cần mở terminal giữa giờ thi.

Cách dùng:
    1. Chạy file này TRƯỚC KHI mở app thi:
         Exam_Diagnostic_Watch.bat
       hoặc:
         python exam_diagnostic_watch.py

    2. Mở ExamLockdown và vào bài thi như bình thường.

    3. Sau khi hint_layer bị kill / app thi thoát, mở file:
         data\\exam_diagnostic_watch.log

Script không cần thao tác trong giờ thi. Khi phát hiện app thi, nó tự:
    - ghi HWND/PID/lý do nhận diện.
    - thử OpenProcess(PROCESS_CREATE_PROCESS).
    - tự chạy lại chính nó thành process con của app thi để tránh bị ExamLockdown kill.
    - trong process con: gắn overlay thử, kiểm tra GetParent, tạo probe process con.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path

if not sys.platform.startswith("win"):
    print("[ERROR] Chỉ chạy trên Windows.")
    raise SystemExit(1)

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

LOG_FILE = BASE_DIR / "data" / "exam_diagnostic_watch.log"

from lib.exam_evasion import (  # noqa: E402
    find_exam_window,
    get_parent_pid,
    spawn_as_child_of,
)

CHILD_MODE = "--diagnostic-child" in sys.argv


def log(message: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%H:%M:%S')}] {message}\n")
    except OSError:
        pass


def _open_process_ok(pid: int, access: int) -> bool:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.OpenProcess(access, False, pid)
    if h:
        k32.CloseHandle(h)
        return True
    return False


def run_child() -> int:
    parent_pid = os.getppid()
    log(f"[CHILD] Bắt đầu diagnostic child: pid={os.getpid()}, os.getppid()={parent_pid}")

    exam_pid = None
    exam_hwnd = None
    found = find_exam_window()
    if found is not None:
        exam_hwnd, exam_pid, reason = found
        log(f"[CHILD] Tìm thấy app thi: hwnd={exam_hwnd}, pid={exam_pid}, reason={reason}")
    else:
        log("[CHILD] find_exam_window() chưa thấy app thi.")

    if exam_pid is not None:
        tool_parent = get_parent_pid(os.getpid())
        log(f"[CHILD] Toolhelp parent của diagnostic child: {tool_parent}, match exam pid: {tool_parent == exam_pid}")
        log(f"[CHILD] OpenProcess(PROCESS_CREATE_PROCESS, exam_pid): {_open_process_ok(exam_pid, 0x0080)}")
    else:
        log("[CHILD] Không có exam_pid để kiểm tra parent relationship.")

    # Gắn thử overlay. Làm hidden trước, gắn rồi mới show để tránh top-level race.
    overlay = None
    try:
        from lib.overlay import DesktopHintLayer

        overlay = DesktopHintLayer({"text": "DIAG", "corner": "top_right"})
        if found is not None and overlay.hwnd:
            ok = overlay.attach_to(exam_hwnd)
            parent = None
            u32 = ctypes.WinDLL("user32")
            u32.GetParent.restype = ctypes.c_void_p
            u32.GetParent.argtypes = [ctypes.c_void_p]
            parent = int(u32.GetParent(ctypes.c_void_p(overlay.hwnd)) or 0)
            log(f"[CHILD] attach_to(exam) = {ok}; overlay.is_child={overlay.is_child}; GetParent(overlay)={parent}; match={parent == exam_hwnd}")
            if ok:
                overlay.show("DIAG")
                log("[CHILD] Đã show overlay dạng child window.")
            else:
                log("[CHILD] Không show vì attach thất bại.")
        else:
            log(f"[CHILD] Không tạo được overlay hwnd={overlay.hwnd if overlay else None}.")
    except Exception as exc:
        log(f"[CHILD] Lỗi khi gắn overlay: {exc}")

    # Probe process con: xác nhận spawn_as_child_of hoạt động với app thi.
    if exam_pid is not None:
        probe_argv = [
            sys.executable,
            "-c",
            "import time,sys; time.sleep(30); sys.exit(0)",
        ]
        try:
            probe_pid = spawn_as_child_of(exam_pid, argv=probe_argv)
            log(f"[CHILD] Đã spawn probe child: pid={probe_pid}")
            time.sleep(1.0)
            tool_parent = get_parent_pid(probe_pid)
            log(f"[CHILD] Toolhelp parent của probe: {tool_parent}; match exam pid: {tool_parent == exam_pid}")
        except Exception as exc:
            log(f"[CHILD] spawn probe thất bại: {exc}")

    # Sống một lúc để xác nhận ExamLockdown không kill process child này.
    deadline = time.monotonic() + 60
    tick = 0
    while time.monotonic() < deadline:
        time.sleep(2)
        tick += 1
        log(f"[CHILD] heartbeat {tick}... vẫn sống (pid={os.getpid()})")

    log("[CHILD] Diagnostic child kết thúc 60 giây.")
    if overlay is not None:
        try:
            overlay.hide()
            overlay.detach_from_parent()
            overlay.destroy()
        except Exception:
            pass
    return 0


def run_watch() -> int:
    log("=" * 70)
    log("BẮT ĐẦU diagnostic watcher. Chờ app thi xuất hiện...")
    log(f"  watcher pid={os.getpid()}")
    log(f"  sys.executable={sys.executable}")
    log("=" * 70)

    scanned_once = False
    while True:
        try:
            found = find_exam_window()
            if found is not None:
                exam_hwnd, exam_pid, reason = found
                log(f"[WATCH] Phát hiện app thi: hwnd={exam_hwnd}, pid={exam_pid}, reason={reason}")
                log(f"[WATCH] OpenProcess(PROCESS_CREATE_PROCESS): {_open_process_ok(exam_pid, 0x0080)}")
                log(f"[WATCH] OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION): {_open_process_ok(exam_pid, 0x1000)}")

                child_argv = [
                    sys.executable,
                    str(BASE_DIR / "exam_diagnostic_watch.py"),
                    "--diagnostic-child",
                ]
                try:
                    child_pid = spawn_as_child_of(exam_pid, argv=child_argv)
                    log(f"[WATCH] Đã spawn diagnostic child: pid={child_pid}")
                except Exception as exc:
                    log(f"[WATCH] KHÔNG spawn được diagnostic child: {exc}")

                # Watcher hiện tại có thể bị ExamLockdown kill sau đó. Đã có child
                # (nếu spawn được) tiếp tục ghi log.
                log("[WATCH] Watcher dừng ở đây để child process làm việc.")
                return 0
        except Exception as exc:
            log(f"[WATCH] Lỗi vòng quét: {exc}")

        if not scanned_once:
            scanned_once = True
            log("[WATCH] Chưa thấy app thi; vẫn đang quét...")

        time.sleep(0.3)


def main() -> int:
    if CHILD_MODE:
        return run_child()
    return run_watch()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit as exc:
        raise
    except Exception:
        log("Lỗi ngoài dự kiến")
        import traceback

        log(traceback.format_exc())
        raise

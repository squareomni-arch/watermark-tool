"""Bộ điều phối chính của app (Windows).

CapsLock BẬT  -> app hoạt động: hiện chữ watermark ở góc trên bên phải
CapsLock TẮT  -> app dừng: ẩn watermark
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from .capslock_trigger import CapsLockTrigger
from .gemini_helper import GeminiHelper, ScreenCaptureBlockedError
from .overlay import WatermarkOverlay


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


class StealthHelperApp:
    def __init__(self, config: Optional[Dict] = None, exam_child: bool = False):
        self.config = config or {}
        self.exam_child = exam_child

        trigger_cfg = self.config.get("trigger") or {}
        self.trigger = CapsLockTrigger(poll_interval=trigger_cfg.get("poll_interval", 0.1))

        features = self.config.get("features") or {}
        self.overlay: Optional[WatermarkOverlay] = None
        if features.get("overlay", True):
            self.overlay = WatermarkOverlay(self.config.get("watermark") or {})
            if not self.overlay.available:
                log("[CẢNH BÁO] Không tạo được watermark - app vẫn chạy, chỉ báo trong console.")
                self.overlay = None

        self.ai: Optional[GeminiHelper] = None
        if features.get("ai", False):
            self.ai = GeminiHelper(self.config.get("gemini") or {})
            if not self.ai.available:
                log("[CẢNH BÁO] Chưa có GEMINI_API_KEY - sẽ hiển thị chữ 'Hello' như cũ.")
                self.ai = None

        # Chế độ chi tiết: trả lời tất cả câu hỏi trên màn hình (đề thi dài).
        self.detailed_mode = bool(features.get("answer_all", False))
        try:
            self.max_lines = max(3, int(features.get("detail_max_lines", 20)))
        except (TypeError, ValueError):
            self.max_lines = 20
        try:
            self.max_chars = max(100, int(features.get("detail_max_chars", 800)))
        except (TypeError, ValueError):
            self.max_chars = 800

        self.active = False
        self._running = False
        self._exam_watch_thread: Optional[threading.Thread] = None
        self._relaunching = False
        self._exam_target: Optional[tuple] = None
        self._exam_target_lock = threading.Lock()
        self._exam_attach_ready = threading.Event()

    # ------------------------------------------------------------------ chạy
    def start(self) -> None:
        self._print_banner()

        self._running = True
        self._start_exam_watcher()
        try:
            self.trigger.listen(
                self.activate,
                self.deactivate,
                should_stop=lambda: not self._running,
                on_tick=self._exam_tick,
            )
        except KeyboardInterrupt:
            log("Nhận Ctrl+C, đang thoát...")
        finally:
            self._running = False
            if self._exam_watch_thread is not None:
                self._exam_watch_thread.join(timeout=1.5)
            self.deactivate()
            if self._relaunching:
                log("Đã chuyển sang process con của ExamLockdown.")
            else:
                log("Đã thoát app.")

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------- ẩn khỏi ExamLockdown
    def _start_exam_watcher(self) -> None:
        self._exam_watch_thread = threading.Thread(
            target=self._exam_watch_loop,
            name="ExamLockdownWatcher",
            daemon=True,
        )
        self._exam_watch_thread.start()

    def _exam_watch_loop(self) -> None:
        from .exam_evasion import find_exam_window, spawn_as_child_of

        pid_file = Path(__file__).resolve().parent.parent / "data" / "stealth_helper.pid"
        last_exam_pid: Optional[int] = None

        while self._running:
            try:
                found = find_exam_window()
                if found is None:
                    if last_exam_pid is not None:
                        last_exam_pid = None
                        log("[EVADE] App thi đã thoát, trả watermark về chế độ thường.")
                    with self._exam_target_lock:
                        self._exam_target = None
                    self._exam_attach_ready.clear()
                    time.sleep(0.15)
                    continue

                exam_hwnd, exam_pid, reason = found
                if exam_pid != last_exam_pid:
                    log(f"[EVADE] Phát hiện app thi không cần biết tên exe: hwnd={exam_hwnd}, pid={exam_pid}, lý do={reason}.")
                    last_exam_pid = exam_pid

                with self._exam_target_lock:
                    self._exam_target = (exam_hwnd, exam_pid, reason)
                self._exam_attach_ready.clear()

                # Đợi thread chính (nơi tạo cửa sổ overlay) thực hiện xong
                # SetParent/SetWindowLongPtr. Gọi các API này từ thread nền có
                # thể không được Windows áp dụng, khiến overlay vẫn là top-level
                # và bị ExamLockdown diệt.
                if not self.exam_child and not self._exam_attach_ready.wait(timeout=0.5):
                    # Vẫn tiếp tục để vòng sau thử lại; không tự spawn khi overlay
                    # chưa chắc đã thành child window.
                    time.sleep(0.12)
                    continue

                # Process hiện tại chưa phải process con của app thi.
                # Tự chạy lại main.py dưới dạng process con, rồi process cũ thoát.
                # Process con + overlay dạng child window sẽ nằm trong danh sách
                # miễn trừ của ExamLockdown.
                if not self.exam_child:
                    try:
                        # Xóa pid file trước khi tạo process con để process con
                        # tạo pid file mới cho chính nó.
                        if pid_file.exists():
                            try:
                                old_pid = int(pid_file.read_text(encoding="ascii").strip())
                            except (OSError, ValueError):
                                old_pid = 0
                            if old_pid == os.getpid():
                                pid_file.unlink()

                        child_pid = spawn_as_child_of(exam_pid)
                        log(f"[EVADE] Đã chạy lại dưới dạng process con của app thi (pid {child_pid}).")
                        self._relaunching = True
                        self._running = False
                        return
                    except Exception as exc:
                        log(f"[WARNING] Không chạy lại được process con: {exc}")
            except Exception as exc:
                log(f"[WARNING] ExamLockdown watcher lỗi: {exc}")

            time.sleep(0.12)

    def _exam_tick(self) -> None:
        """Chạy trên thread chính: attach/detach overlay đúng thread tạo cửa sổ."""
        if self.overlay is None:
            return

        with self._exam_target_lock:
            target = self._exam_target

        if target is None:
            if self.overlay.is_child and self.overlay.detach_from_parent():
                self._exam_attach_ready.clear()
            return

        exam_hwnd, _exam_pid, _reason = target
        if self.overlay.attach_to(exam_hwnd):
            self._exam_attach_ready.set()
        else:
            self._exam_attach_ready.clear()
            if self.overlay.hwnd:
                log("[EVADE] Không gắn được watermark vào cửa sổ app thi (SetParent thất bại).")
    def activate(self) -> None:
        if self.active:
            return
        self.active = True

        if self.overlay is None:
            log("[BẬT] CapsLock đang bật.")
            return

        if self.ai is not None:
            self.overlay.show("Đang đọc màn hình...")
            log("[BẬT] CapsLock đang bật -> đang gọi Gemini đọc màn hình...")

            try:
                answer = self.ai.answer_from_screen(self.detailed_mode)
            except ScreenCaptureBlockedError as exc:
                log(f"[CHẶN] {exc}")
                answer = "__SCREEN_BLOCKED__"
            except Exception:
                import traceback

                traceback.print_exc()
                answer = None

            # Trong lúc gọi API, người dùng có thể đã tắt CapsLock.
            if not self.trigger.is_on():
                self.deactivate()
                return

            if answer == "__SCREEN_BLOCKED__":
                self.overlay.show("Không chụp được màn hình (bị chặn)")
            elif answer is None:
                self.overlay.show("Lỗi AI")
            elif answer.strip().upper().startswith("NO_QUESTION"):
                self.overlay.show("Không thấy câu hỏi")
            elif answer.strip().lower().startswith(("tôi là trợ lý", "tôi là ai", "tôi là gemini")):
                # Một số model cũ trả lời nhầm câu lệnh hệ thống thay vì đọc ảnh.
                self.overlay.show("Không thấy câu hỏi")
            elif self.detailed_mode:
                self.overlay.show_lines(answer, max_lines=self.max_lines, max_chars=self.max_chars)
            else:
                self.overlay.show(answer[:120])
            return

        self.overlay.show()
        log("[BẬT] CapsLock đang bật -> hiện watermark.")

    def deactivate(self) -> None:
        if not self.active:
            # Lần đồng bộ đầu tiên: CapsLock đang tắt, không cần log ồn ào.
            if self.overlay is not None:
                self.overlay.hide()
            return
        self.active = False
        if self.overlay is not None:
            self.overlay.hide()
        log("[TẮT] CapsLock đã tắt -> ẩn watermark.")

    # ------------------------------------------------------------------ phụ
    def _print_banner(self) -> None:
        print("=" * 60)
        print(" STEALTH HELPER (Windows)")
        if self.ai is not None:
            if self.detailed_mode:
                print(" Bật CapsLock  -> đọc đề + trả lời TẤT CẢ câu hỏi (chi tiết)")
            else:
                print(" Bật CapsLock  -> đọc màn hình + Gemini trả lời")
        else:
            print(" Bật CapsLock  -> hiện watermark")
        print(" Tắt CapsLock  -> ẩn kết quả")
        print(f" Kiểm tra mỗi: {self.trigger.poll_interval:.2f}s")
        print(" Chạy ngầm: dùng Stop_StealthHelper.vbs để thoát | Chạy console: Ctrl+C")
        print("=" * 60, flush=True)

        state = self.trigger.is_on()
        print(f" Trạng thái CapsLock hiện tại: {'BẬT' if state else 'TẮT'}\n", flush=True)

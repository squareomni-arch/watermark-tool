"""Bộ điều phối chính của app (Windows).

CapsLock BẬT  -> app hoạt động: hiện chữ watermark ở góc trên bên phải
CapsLock TẮT  -> app dừng: ẩn watermark
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from .capslock_trigger import CapsLockTrigger
from .gemini_helper import GeminiHelper, ScreenCaptureBlockedError
from .overlay import WatermarkOverlay

BASE_DIR = Path(__file__).resolve().parent.parent
EVADE_LOG_FILE = BASE_DIR / "data" / "exam_evade.log"


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _evade_log(message: str) -> None:
    """Ghi log chẩn đoán trực tiếp ra file, không phụ thuộc console/pythonw stdout."""
    try:
        EVADE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(EVADE_LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


class StealthHelperApp:
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}

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
        self._exam_attached = False
        self._defensive_hide_pending = False
        self._defensive_hide_lock = threading.Lock()
        self._seen_windows = set()

    # ------------------------------------------------------------------ chạy
    def start(self) -> None:
        _evade_log(
            "=" * 72
            + f"\nSTART pid={os.getpid()} parent={os.getppid()}"
            + f"\nexecutable={sys.executable if hasattr(sys, 'executable') else '?'}"
            + f"\npythonw={'pythonw.exe' in (sys.executable or '').lower()}"
            + "\n"
            + "=" * 72
        )

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
    def _attach_to_current_exam_window(self) -> bool:
        """Tìm app thi đang mở và gắn overlay ngay trên thread chính."""
        if self.overlay is None:
            _evade_log("ATTACH_AT_START overlay=None")
            return False

        try:
            from .exam_evasion import find_exam_window

            found = find_exam_window()
            if found is None:
                _evade_log("ATTACH_AT_START find_exam_window=None")
                log("[EVADE] Process con chưa thấy cửa sổ app thi, sẽ chờ watcher.")
                return False

            exam_hwnd, exam_pid, reason = found
            ok = self.overlay.attach_to(exam_hwnd)
            parent = self._overlay_parent_hwnd()
            _evade_log(
                f"ATTACH_AT_START hwnd={exam_hwnd} pid={exam_pid} reason={reason} "
                f"attach={ok} child={self.overlay.is_child} getparent={parent} match={parent == exam_hwnd}"
            )
            if not ok:
                log(f"[EVADE] Process con không gắn được vào hwnd={exam_hwnd}, pid={exam_pid}.")
                return False

            self._exam_attached = True
            log(f"[EVADE] Process con đã gắn watermark vào cửa sổ app thi hwnd={exam_hwnd}, pid={exam_pid}.")
            return True
        except Exception as exc:
            _evade_log(f"ATTACH_AT_START ERROR {exc!r}")
            log(f"[EVADE] Lỗi khi gắn process con: {exc}")
            return False

    def _overlay_parent_hwnd(self) -> Optional[int]:
        """Đọc parent thực tế của cửa sổ overlay qua GetParent."""
        if self.overlay is None or not self.overlay.hwnd:
            return None
        try:
            import ctypes

            user32 = ctypes.WinDLL("user32")
            user32.GetParent.restype = ctypes.c_void_p
            user32.GetParent.argtypes = [ctypes.c_void_p]
            parent = int(user32.GetParent(ctypes.c_void_p(self.overlay.hwnd)) or 0)
            return parent or None
        except Exception:
            return None

    def _start_exam_watcher(self) -> None:
        _evade_log(f"WATCHER_STARTING pid={os.getpid()}")
        self._exam_watch_thread = threading.Thread(
            target=self._exam_watch_loop,
            name="ExamLockdownWatcher",
            daemon=True,
        )
        self._exam_watch_thread.start()

    def _exam_watch_loop(self) -> None:
        _evade_log(f"WATCHER_THREAD_STARTED pid={os.getpid()}")

        try:
            from .exam_evasion import (
                describe_visible_window,
                find_exam_window,
                find_fullscreen_candidate_window,
                get_foreground_window,
                list_visible_window_scores,
                snapshot_visible_windows,
            )
        except Exception as exc:
            _evade_log(f"WATCHER_IMPORT_FAILED {exc!r}")
            return

        _evade_log("WATCHER_IMPORT_OK")

        last_exam_pid: Optional[int] = None
        last_candidate_log = 0.0
        last_heartbeat_log = 0.0

        try:
            for hwnd, _pid in snapshot_visible_windows():
                self._seen_windows.add(hwnd)
            _evade_log(f"WATCHER_BASELINE windows={len(self._seen_windows)}")
        except Exception as exc:
            _evade_log(f"WATCHER_BASELINE_FAILED {exc!r}")

        # Quan trọng: nếu app thi đã mở trước khi tool chạy, cửa sổ thi nằm
        # trong baseline nên không có sự kiện NEW_WINDOW. Phải xử lý ngay tại
        # đây, trước khi ExamLockdown kịp strict-scan kill overlay top-level.
        found = find_exam_window()
        if found is not None:
            self._handle_exam_found(found, source="BASELINE")
        else:
            defensive = find_fullscreen_candidate_window()
            if defensive is not None:
                self._hide_overlay_immediately(
                    "DEFENSIVE_HIDE_FULLSCREEN_BASELINE hwnd={} pid={} reason={}".format(*defensive)
                )

        while self._running:
            try:
                now_heartbeat = time.monotonic()
                if now_heartbeat - last_heartbeat_log >= 5.0:
                    last_heartbeat_log = now_heartbeat
                    _evade_log(
                        f"WATCHER_ALIVE pid={os.getpid()} parent={os.getppid()} "
                        f"overlay_active={self.overlay.active if self.overlay else None}"
                    )

                found = find_exam_window()
                if found is not None:
                    self._handle_exam_found(found, source="LOOP")
                    time.sleep(0.02)
                    continue

                # Không thấy app thi: ẩn watermark ngay khi có cửa sổ
                # top-level MỚI xuất hiện. Lớp này độc lập hoàn toàn với
                # tên exe/title/class/WDA.
                self._hide_for_new_windows()

                # Foreground tức thời: app thi vào bài thường là fullscreen
                # và giành foreground. Bắt tín hiệu này nhanh hơn EnumWindows.
                fg = get_foreground_window()
                if fg is not None:
                    fg_hwnd, fg_pid, fg_reason = fg
                    fg_info = describe_visible_window(fg_hwnd) or {}
                    title_lower = str(fg_info.get("title", "")).lower()
                    class_lower = str(fg_info.get("class", "")).lower()
                    if (
                        float(fg_info.get("coverage", 0.0)) >= 0.8
                        or "examlockdown" in title_lower
                        or "examlockdown" in class_lower
                    ):
                        self._hide_overlay_immediately("FOREGROUND_GUARD " + fg_reason)
                        with self._exam_target_lock:
                            self._exam_target = (fg_hwnd, fg_pid, fg_reason)
                        self._exam_attach_ready.clear()

                defensive = find_fullscreen_candidate_window()
                if defensive is not None:
                    self._hide_overlay_immediately(
                        "DEFENSIVE_HIDE_FULLSCREEN hwnd={} pid={} reason={}".format(*defensive)
                    )

                if last_exam_pid is not None:
                    last_exam_pid = None
                    _evade_log("EXAM_GONE")
                    log("[EVADE] App thi đã thoát, trả watermark về chế độ thường.")

                with self._exam_target_lock:
                    self._exam_target = None
                self._exam_attach_ready.clear()

                now = time.monotonic()
                if now - last_candidate_log >= 3.0:
                    last_candidate_log = now
                    rows = list_visible_window_scores(limit=12)
                    _evade_log("NO_EXAM candidates: " + repr(rows))

                time.sleep(0.02)
            except Exception as exc:
                _evade_log(f"WATCH_ERROR {exc!r}")
                log(f"[WARNING] ExamLockdown watcher lỗi: {exc}")
                time.sleep(0.05)

    def _hide_overlay_immediately(self, reason: str) -> None:
        """Ẩn watermark ngay trên thread watcher để chạy đua với ExamLockdown."""
        if self.overlay is not None and self.overlay.active:
            self.overlay.hide()
        _evade_log(reason)

    def _hide_for_new_windows(self) -> None:
        """Ẩn watermark nếu có cửa sổ top-level mới xuất hiện."""
        try:
            from .exam_evasion import describe_visible_window, snapshot_visible_windows

            for hwnd, pid in snapshot_visible_windows():
                if hwnd in self._seen_windows:
                    continue
                self._seen_windows.add(hwnd)
                if pid == os.getpid():
                    continue
                info = describe_visible_window(hwnd)
                _evade_log("NEW_WINDOW " + repr(info))
                self._hide_overlay_immediately("DIRECT_HIDE_ON_NEW_WINDOW")
                return
        except Exception as exc:
            _evade_log(f"NEW_WINDOW_CHECK_FAILED {exc!r}")

    def _handle_exam_found(self, found: tuple, source: str) -> None:
        """Xử lý khi phát hiện app thi.

        Chỉ ẩn watermark rồi đưa target cho _exam_tick gắn cửa sổ con.
        Không spawn/đổi parent process để tránh ảnh hưởng tới app thi.
        """
        exam_hwnd, exam_pid, reason = found
        _evade_log(
            f"EXAM_FOUND[{source}] hwnd={exam_hwnd} pid={exam_pid} reason={reason} "
            f"self_pid={os.getpid()} self_parent={os.getppid()}"
        )
        log(f"[EVADE] Phát hiện app thi: hwnd={exam_hwnd}, pid={exam_pid}, lý do={reason}.")

        # Ẩn ngay để không bị strict scan bắt top-level, sau đó giao cho
        # _exam_tick gắn overlay thành child window của app thi.
        self._hide_overlay_immediately("HIDE_BEFORE_ATTACH watermark đang hiện -> đã ẩn.")

        with self._exam_target_lock:
            self._exam_target = (exam_hwnd, exam_pid, reason)
        self._exam_attach_ready.clear()

    def _exam_tick(self) -> None:
        """Chạy trên thread chính: attach/detach overlay đúng thread tạo cửa sổ."""
        if self.overlay is None:
            return

        with self._defensive_hide_lock:
            hide_requested = self._defensive_hide_pending
            self._defensive_hide_pending = False

        if hide_requested and self.overlay.active:
            self.overlay.hide()
            _evade_log("MAIN_THREAD_DEFENSIVE_HIDE overlay đã ẩn vì có cửa sổ mới.")

        with self._exam_target_lock:
            target = self._exam_target

        if target is None:
            if self.overlay.is_child and self.overlay.detach_from_parent():
                self._exam_attached = False
                self._exam_attach_ready.clear()
            return

        exam_hwnd, exam_pid, _reason = target
        ok = self.overlay.attach_to(exam_hwnd)
        parent = self._overlay_parent_hwnd()
        _evade_log(
            f"EXAM_TICK hwnd={exam_hwnd} pid={exam_pid} attach={ok} "
            f"child={self.overlay.is_child} getparent={parent} match={parent == exam_hwnd}"
        )
        if ok:
            self._exam_attached = True
            self._exam_attach_ready.set()
            # Nếu CapsLock đang bật nhưng lúc trước ta cố tình chưa show để tránh
            # top-level race, thì giờ đã an toàn để hiện watermark.
            if self.active and not self.overlay.active:
                self.overlay.show()
        else:
            self._exam_attached = False
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

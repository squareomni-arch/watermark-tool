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

_NO_ANSWER = object()


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
        self._ai_busy = False
        self._ai_busy_lock = threading.Lock()
        self._pending_answer = _NO_ANSWER
        self._pending_answer_lock = threading.Lock()
        # Process name CHỈ là accelerator. Nếu app thi đổi tên, các lớp
        # behavior-based bên dưới vẫn phát hiện và tự thêm tên mới vào đây.
        exam_cfg = self.config.get("exam") or {}
        configured_names = exam_cfg.get("process_names") or [
            "examlockdown.app.exe",
            "examlockdown.exe",
        ]
        self._exam_process_names = {str(n).lower() for n in configured_names if n}
        self._learned_exam_names = set()
        self._exam_process_seen = False
        self._suppress_top_level_until = 0.0
        self._known_pids = set()
        self._last_defensive_log = ""
        self._last_defensive_log_at = 0.0
        self._last_exam_tick_log = ""
        self._last_exam_tick_log_at = 0.0
        self._last_handled_exam = None
        self._last_handled_exam_at = 0.0

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
                find_processes_by_names,
                list_all_pids,
            )
        except Exception as exc:
            _evade_log(f"WATCHER_IMPORT_FAILED {exc!r}")
            return

        _evade_log("WATCHER_IMPORT_OK")

        # Baseline các PID hiện có. Khi một process MỚI xuất hiện, ẩn overlay
        # ngay lập tức để ExamLockdown preflight không kịp strict-scan kill.
        # Đây là lớp phòng thủ độc lập hoàn toàn với tên process.
        try:
            self._known_pids = set(list_all_pids())
            _evade_log(f"WATCHER_PID_BASELINE pids={len(self._known_pids)}")
        except Exception as exc:
            _evade_log(f"WATCHER_PID_BASELINE_FAILED {exc!r}")

        last_heartbeat_log = 0.0

        while self._running:
            try:
                now = time.monotonic()
                if now - last_heartbeat_log >= 5.0:
                    last_heartbeat_log = now
                    _evade_log(
                        f"WATCHER_ALIVE pid={os.getpid()} parent={os.getppid()} "
                        f"overlay_active={self.overlay.active if self.overlay else None}"
                    )

                # KHÔNG dùng "process mới bất kỳ" để ẩn/hủy hiển thị vì Windows
                # liên tục sinh process nền, làm tool tưởng đang có app thi và
                # không bao giờ hiện đáp án.

                # Chỉ dùng process name của ExamLockdown (hoặc tên đã học).
                # Nếu app thi đổi tên, lớp behavior vẫn bắt ở thời điểm bật
                # CapsLock qua _find_current_exam_window; không cần phỏng đoán
                # bằng mọi process mới.
                exam_procs = find_processes_by_names(
                    self._exam_process_names | self._learned_exam_names
                )
                if exam_procs:
                    if not self._exam_process_seen:
                        self._exam_process_seen = True
                        _evade_log("EXAM_PROCESS_SEEN " + repr(exam_procs))
                    with self._defensive_hide_lock:
                        self._defensive_hide_pending = True
                    self._suppress_top_level_until = now + 2.0
                elif self._exam_process_seen:
                    self._exam_process_seen = False
                    _evade_log("EXAM_PROCESS_GONE")

                time.sleep(0.05)
            except Exception as exc:
                _evade_log(f"WATCH_ERROR {exc!r}")
                log(f"[WARNING] ExamLockdown watcher lỗi: {exc}")
                time.sleep(0.2)

    def _hide_overlay_immediately(self, reason: str) -> None:
        """Ẩn watermark ngay trên thread watcher để chạy đua với ExamLockdown."""
        if self.overlay is not None and self.overlay.active:
            self.overlay.hide()

        # Tránh log spam hàng nghìn dòng giống nhau trong 1 giây.
        now = time.monotonic()
        if reason != self._last_defensive_log or now - self._last_defensive_log_at >= 1.0:
            self._last_defensive_log = reason
            self._last_defensive_log_at = now
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
                self._suppress_top_level_until = time.monotonic() + 2.0
                return
        except Exception as exc:
            _evade_log(f"NEW_WINDOW_CHECK_FAILED {exc!r}")

    def _handle_exam_found(self, found: tuple, source: str) -> None:
        """Xử lý khi phát hiện app thi.

        Chỉ ẩn watermark rồi đưa target cho _exam_tick gắn cửa sổ con.
        Không spawn/đổi parent process để tránh ảnh hưởng tới app thi.
        """
        exam_hwnd, exam_pid, reason = found

        # Tránh xử lý/log lặp liên tục cho cùng một cửa sổ trong 1 giây.
        now = time.monotonic()
        key = (exam_hwnd, exam_pid)
        if key == self._last_handled_exam and now - self._last_handled_exam_at < 1.0:
            return
        self._last_handled_exam = key
        self._last_handled_exam_at = now

        _evade_log(
            f"EXAM_FOUND[{source}] hwnd={exam_hwnd} pid={exam_pid} reason={reason} "
            f"self_pid={os.getpid()} self_parent={os.getppid()}"
        )
        log(f"[EVADE] Phát hiện app thi: hwnd={exam_hwnd}, pid={exam_pid}, lý do={reason}.")

        # Đánh dấu để thread chính ẩn overlay. Không gọi overlay.hide() ở
        # thread watcher vì ShowWindow không phải lúc nào cũng an toàn nếu gọi
        # sai thread, và có thể làm watcher treo.
        with self._defensive_hide_lock:
            self._defensive_hide_pending = True

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
            if self.ai is not None:
                self.ai._exam_capture_hwnd = None
            if self.overlay.is_child and self.overlay.detach_from_parent():
                self._exam_attached = False
                self._exam_attach_ready.clear()
            self._process_pending_answer()
            return

        exam_hwnd, exam_pid, _reason = target
        if self.ai is not None:
            self.ai._exam_capture_hwnd = exam_hwnd

        ok = self.overlay.attach_to(exam_hwnd)
        parent = self._overlay_parent_hwnd()
        tick_line = (
            f"EXAM_TICK hwnd={exam_hwnd} pid={exam_pid} attach={ok} "
            f"child={self.overlay.is_child} getparent={parent} match={parent == exam_hwnd}"
        )
        now_tick = time.monotonic()
        if tick_line != self._last_exam_tick_log or now_tick - self._last_exam_tick_log_at >= 1.0:
            self._last_exam_tick_log = tick_line
            self._last_exam_tick_log_at = now_tick
            _evade_log(tick_line)
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

        self._process_pending_answer()

    def _start_ai_worker(self) -> None:
        """Chạy AI ở thread riêng để CapsLock OFF vẫn ẩn được ngay lập tức."""
        with self._ai_busy_lock:
            if self._ai_busy:
                log("[AI] Vẫn đang đọc màn hình, bỏ qua lần bật CapsLock này.")
                return
            self._ai_busy = True

        log("[AI] Bắt đầu worker đọc màn hình.")
        threading.Thread(
            target=self._ai_worker,
            name="StealthAIGeneration",
            daemon=True,
        ).start()

    def _ai_worker(self) -> None:
        try:
            log("[AI] Worker đang chụp màn hình + gọi Gemini...")
            answer = self.ai.answer_from_screen(self.detailed_mode)
            log(f"[AI] Worker xong. answer={'None' if answer is None else (answer[:60] + '...' if len(str(answer)) > 60 else answer)}")
        except ScreenCaptureBlockedError as exc:
            log(f"[CHẶN] {exc}")
            answer = "__SCREEN_BLOCKED__"
        except Exception:
            import traceback

            traceback.print_exc()
            answer = None
        finally:
            with self._ai_busy_lock:
                self._ai_busy = False

        with self._pending_answer_lock:
            self._pending_answer = answer
        log("[AI] Đã đặt pending answer.")

    def _show_answer(self, answer: Optional[str]) -> None:
        log(f"[AI] Hiển thị answer: {repr(answer)[:120]}")
        if answer == "__SCREEN_BLOCKED__":
            self.overlay.show("Không đọc được màn hình")
        elif answer is None:
            self.overlay.show("Lỗi đọc màn hình")
        elif answer.strip().upper().startswith("NO_QUESTION"):
            self.overlay.show("Không thấy câu hỏi")
        elif answer.strip().lower().startswith(("tôi là trợ lý", "tôi là ai", "tôi là gemini")):
            # Một số model cũ trả lời nhầm câu lệnh hệ thống thay vì đọc ảnh.
            self.overlay.show("Không thấy câu hỏi")
        elif self.detailed_mode:
            self.overlay.show_lines(answer, max_lines=self.max_lines, max_chars=self.max_chars)
        else:
            self.overlay.show(answer[:120])

    def _process_pending_answer(self) -> None:
        """Hiển thị câu trả lời AI vừa xong, chỉ khi CapsLock vẫn bật."""
        with self._pending_answer_lock:
            answer = self._pending_answer
            self._pending_answer = _NO_ANSWER

        if answer is _NO_ANSWER:
            return

        log("[AI] Kiểm tra pending answer để hiển thị...")

        # Nếu người dùng vừa tắt CapsLock thì không show kết quả cũ.
        if not self.active or not self.trigger.is_on():
            log("[AI] CapsLock đã tắt, bỏ qua pending answer.")
            return

        # Không có app thi -> hiển thị bình thường, nhưng không show nếu vừa
        # thấy cửa sổ mới / process ExamLockdown xuất hiện.
        with self._exam_target_lock:
            target = self._exam_target
        if target is None:
            if time.monotonic() < self._suppress_top_level_until:
                log("[AI] Có cửa sổ/process mới đáng ngờ, hoãn hiển thị.")
                return
            log("[AI] Không có app thi, hiển thị luôn.")
            self._show_answer(answer)
            return

        # Có app thi. Bắt buộc phải gắn được child window trước khi show.
        if not self._exam_attached:
            self._ensure_attached_to_exam()
        if self._exam_attached:
            self._show_answer(answer)
        else:
            log("[AI] Chưa gắn được child window, chưa hiển thị.")

    def _find_current_exam_window(self):
        """Trả về (hwnd, pid, reason) nếu app thi đang ở chế độ kiosk WDA.

        Chỉ dò theo process name rồi kiểm tra đúng HWND đó có bật
        WDA_EXCLUDEFROMCAPTURE hay không. Không EnumWindows toàn hệ thống,
        tránh treo khi ExamLockdown Not Responding. Cũng không attach vào
        cửa sổ preflight (chưa WDA).
        """
        try:
            from .exam_evasion import find_processes_by_names, find_window_for_process
            from .wda_unlock import get_window_affinity

            procs = find_processes_by_names(
                self._exam_process_names | self._learned_exam_names
            )
            for name, pid in procs.items():
                hwnd = find_window_for_process(pid)
                if not hwnd:
                    continue
                affinity = get_window_affinity(hwnd)
                if affinity == 0x00000011:  # WDA_EXCLUDEFROMCAPTURE
                    return hwnd, pid, f"kiosk_wda:{name}"
            return None
        except Exception:
            return None

    def _ensure_attached_to_exam(self) -> bool:
        """Gắn overlay vào app thi nếu đang có app thi.

        Trả về True khi an toàn để show overlay: không có app thi, hoặc đã
        gắn thành công dưới dạng child window.
        """
        if self.overlay is None:
            return False

        with self._exam_target_lock:
            target = self._exam_target

        if target is None:
            found = self._find_current_exam_window()
            if found is not None:
                self._handle_exam_found(found, source="ENSURE")
                with self._exam_target_lock:
                    target = self._exam_target

        if target is None:
            return True

        # Cung cấp HWND cho GeminiHelper để chụp bằng PrintWindow.
        if self.ai is not None:
            self.ai._exam_capture_hwnd = int(target[0])

        self._exam_tick()
        return self._exam_attached

    def activate(self) -> None:
        if self.active:
            return
        self.active = True

        if self.overlay is None:
            log("[BẬT] CapsLock đang bật.")
            return

        # Không được show top-level khi app thi đang mở: strict scan của
        # ExamLockdown sẽ kill process. Phải gắn thành child window trước.
        if not self._ensure_attached_to_exam():
            _evade_log("ACTIVATE_BLOCKED_NO_CHILD_ATTACH")
            log("[BẬT] CapsLock đang bật, nhưng chưa gắn được watermark vào app thi.")
            return

        if self.ai is not None:
            if not self._ensure_attached_to_exam():
                _evade_log("ACTIVATE_BLOCKED_NO_CHILD_ATTACH")
                log("[BẬT] CapsLock đang bật, nhưng chưa gắn được watermark vào app thi.")
                return
            # Không hiện overlay lúc này để chữ "Đang đọc màn hình..." không
            # lọt vào ảnh chụp gửi cho Gemini.
            self.overlay.hide()
            log("[BẬT] CapsLock đang bật -> đang gọi Gemini đọc màn hình...")
            self._start_ai_worker()
            return

        if not self._ensure_attached_to_exam():
            _evade_log("SHOW_BLOCKED_NO_CHILD_ATTACH")
            return

        self.overlay.show()
        log("[BẬT] CapsLock đang bật -> hiện watermark.")

    def deactivate(self) -> None:
        # CapsLock tắt: khôi phục WDA ngay lập tức nếu tool còn đang giữ
        # trạng thái tạm mở, tránh ExamLockdown phát hiện tamper.
        if self.ai is not None:
            try:
                self.ai.restore_capture_protection_now()
            except Exception:
                pass

        # Hủy kết quả AI đang chờ hiển thị: tắt CapsLock nghĩa là ẩn ngay.
        with self._pending_answer_lock:
            self._pending_answer = _NO_ANSWER

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

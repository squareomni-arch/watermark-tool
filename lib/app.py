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
        self._exam_attached = False
        self._defensive_hide_pending = False
        self._defensive_hide_lock = threading.Lock()
        self._seen_windows = set()
        self._log_relay_thread: Optional[threading.Thread] = None
        self._log_relay_stop = threading.Event()

    # ------------------------------------------------------------------ chạy
    def start(self) -> None:
        _evade_log(
            "=" * 72
            + f"\nSTART pid={os.getpid()} parent={os.getppid()} exam_child={self.exam_child}"
            + f"\nexecutable={sys.executable if hasattr(sys, 'executable') else '?'}"
            + f"\npythonw={'pythonw.exe' in (sys.executable or '').lower()}"
            + "\n"
            + "=" * 72
        )

        self._print_banner()

        self._running = True

        # Process con phải gắn overlay thành cửa sổ con NGAY TRƯỚC KHI hiển thị.
        # Nếu để trigger.listen bật CapsLock show top-level trước, ExamLockdown
        # có thể quét trúng cửa sổ top-level đó trong lúc đua và kill process.
        if self.exam_child:
            self._attach_to_current_exam_window()

        self._start_log_relay()
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
            self._log_relay_stop.set()
            if self._exam_watch_thread is not None:
                self._exam_watch_thread.join(timeout=1.5)
            if self._log_relay_thread is not None:
                self._log_relay_thread.join(timeout=1.5)
            self.deactivate()
            if self._relaunching:
                log("Đã chuyển sang process con của ExamLockdown.")
            else:
                log("Đã thoát app.")

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------- log relay từ xa
    def _start_log_relay(self) -> None:
        try:
            from .log_relay import publish_project_logs
        except Exception:
            return

        def _loop() -> None:
            # Gửi ngay lần đầu để developer có log sớm nhất có thể.
            publish_project_logs(
                BASE_DIR,
                title=f"start pid={os.getpid()} parent={os.getppid()}",
            )
            while not self._log_relay_stop.wait(timeout=3.0):
                try:
                    publish_project_logs(
                        BASE_DIR,
                        title=f"heartbeat pid={os.getpid()}",
                    )
                except Exception:
                    pass

        self._log_relay_thread = threading.Thread(
            target=_loop,
            name="StealthLogRelay",
            daemon=True,
        )
        self._log_relay_thread.start()

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
                get_parent_pid,
                list_visible_window_scores,
                snapshot_visible_windows,
                spawn_as_child_of,
            )
        except Exception as exc:
            _evade_log(f"WATCHER_IMPORT_FAILED {exc!r}")
            return

        pid_file = Path(__file__).resolve().parent.parent / "data" / "stealth_helper.pid"
        last_exam_pid: Optional[int] = None
        last_candidate_log = 0.0
        last_heartbeat_log = 0.0

        try:
            for hwnd, _pid in snapshot_visible_windows():
                self._seen_windows.add(hwnd)
            _evade_log(f"WATCHER_BASELINE windows={len(self._seen_windows)}")
        except Exception as exc:
            _evade_log(f"WATCHER_BASELINE_FAILED {exc!r}")

        while self._running:
            try:
                now_heartbeat = time.monotonic()
                if now_heartbeat - last_heartbeat_log >= 5.0:
                    last_heartbeat_log = now_heartbeat
                    _evade_log(
                        f"WATCHER_ALIVE pid={os.getpid()} parent={os.getppid()} "
                        f"overlay_active={self.overlay.active if self.overlay else None}"
                    )

                # Ẩn watermark ngay khi có cửa sổ top-level MỚI xuất hiện.
                # Đây là lớp rename-proof bắt ExamLockdown ngay từ cửa sổ
                # preflight, trước khi app kịp chuyển fullscreen/WDA.
                try:
                    for hwnd, pid in snapshot_visible_windows():
                        if hwnd in self._seen_windows:
                            continue
                        self._seen_windows.add(hwnd)
                        if pid == os.getpid():
                            continue
                        info = describe_visible_window(hwnd)
                        _evade_log("NEW_WINDOW " + repr(info))
                        with self._defensive_hide_lock:
                            self._defensive_hide_pending = True
                except Exception as exc:
                    _evade_log(f"NEW_WINDOW_CHECK_FAILED {exc!r}")

                found = find_exam_window()
                if found is None:
                    # Lớp phòng thủ độc lập hoàn toàn với tên/title/class/WDA:
                    # app thi có thể đổi tên bất kỳ. Nếu có cửa sổ fullscreen
                    # topmost/popup thì ẩn watermark trước, tránh bị strict scan diệt.
                    defensive = find_fullscreen_candidate_window()
                    if defensive is not None and self.overlay is not None and self.overlay.active:
                        self.overlay.hide()
                        _evade_log(
                            "DEFENSIVE_HIDE_FULLSCREEN hwnd={} pid={} reason={}".format(*defensive)
                        )

                    if last_exam_pid is not None:
                        last_exam_pid = None
                        _evade_log("EXAM_GONE")
                        log("[EVADE] App thi đã thoát, trả watermark về chế độ thường.")
                    with self._exam_target_lock:
                        self._exam_target = None
                    self._exam_attach_ready.clear()

                    # Ghi log ứng viên mỗi 3 giây nếu không nhận diện được app thi.
                    now = time.monotonic()
                    if now - last_candidate_log >= 3.0:
                        last_candidate_log = now
                        rows = list_visible_window_scores(limit=12)
                        _evade_log("NO_EXAM candidates: " + repr(rows))

                    time.sleep(0.15)
                    continue

                exam_hwnd, exam_pid, reason = found
                if exam_pid != last_exam_pid:
                    last_exam_pid = exam_pid
                    _evade_log(
                        f"EXAM_FOUND hwnd={exam_hwnd} pid={exam_pid} reason={reason} "
                        f"self_pid={os.getpid()} self_parent={os.getppid()}"
                    )
                    log(f"[EVADE] Phát hiện app thi không cần biết tên exe: hwnd={exam_hwnd}, pid={exam_pid}, lý do={reason}.")

                # Ẩn ngay lập tức nếu watermark đang hiện: ExamLockdown preflight
                # quét mọi top-level window visible, không chỉ trong giờ thi.
                if self.overlay is not None and self.overlay.active:
                    self.overlay.hide()
                    _evade_log("HIDE_BEFORE_ATTACH watermark đang hiện -> đã ẩn trước khi gắn child.")

                with self._exam_target_lock:
                    self._exam_target = (exam_hwnd, exam_pid, reason)
                self._exam_attach_ready.clear()

                exam_hwnd, exam_pid, reason = found
                if exam_pid != last_exam_pid:
                    last_exam_pid = exam_pid
                    _evade_log(
                        f"EXAM_FOUND hwnd={exam_hwnd} pid={exam_pid} reason={reason} "
                        f"self_pid={os.getpid()} self_parent={os.getppid()}"
                    )
                    log(f"[EVADE] Phát hiện app thi không cần biết tên exe: hwnd={exam_hwnd}, pid={exam_pid}, lý do={reason}.")

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

                        _evade_log(f"SPAWN_CHILD exam_pid={exam_pid} exam_hwnd={exam_hwnd}")
                        child_pid = spawn_as_child_of(exam_pid)
                        time.sleep(0.3)
                        tool_parent = get_parent_pid(child_pid)
                        _evade_log(
                            f"SPAWNED child_pid={child_pid} toolhelp_parent={tool_parent} "
                            f"match_exam_pid={tool_parent == exam_pid}"
                        )
                        log(f"[EVADE] Đã chạy lại dưới dạng process con của app thi (pid {child_pid}).")
                        self._relaunching = True
                        self._running = False
                        return
                    except Exception as exc:
                        _evade_log(f"SPAWN_FAILED exam_pid={exam_pid} error={exc!r}")
                        log(f"[WARNING] Không chạy lại được process con: {exc}")
            except Exception as exc:
                _evade_log(f"WATCH_ERROR {exc!r}")
                log(f"[WARNING] ExamLockdown watcher lỗi: {exc}")

            time.sleep(0.12)

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

        # Process con của app thi: không được show overlay lúc nó còn là
        # top-level window. Đợi _exam_tick gắn xong rồi mới show.
        if self.exam_child and not self._exam_attached:
            _evade_log(f"ACTIVATE_WAIT_ATTACH active={self.active} overlay_active={self.overlay.active}")
            log("[BẬT] CapsLock đang bật, chờ gắn watermark vào cửa sổ app thi.")
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

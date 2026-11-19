"""Bộ điều phối chính của app (Windows).

CapsLock BẬT  -> app hoạt động: hiện chữ watermark ở góc trên bên phải
CapsLock TẮT  -> app dừng: ẩn watermark
"""

from __future__ import annotations

import time
from typing import Dict, Optional

from .capslock_trigger import CapsLockTrigger
from .gemini_helper import GeminiHelper
from .overlay import WatermarkOverlay


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


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

        self.active = False
        self._running = False

    # ------------------------------------------------------------------ chạy
    def start(self) -> None:
        self._print_banner()

        self._running = True
        try:
            self.trigger.listen(
                self.activate,
                self.deactivate,
                should_stop=lambda: not self._running,
            )
        except KeyboardInterrupt:
            log("Nhận Ctrl+C, đang thoát...")
        finally:
            self._running = False
            self.deactivate()
            log("Đã thoát app.")

    def stop(self) -> None:
        self._running = False

    # -------------------------------------------------------------- bật / tắt
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
                answer = self.ai.answer_from_screen()
            except Exception:
                import traceback

                traceback.print_exc()
                answer = None

            # Trong lúc gọi API, người dùng có thể đã tắt CapsLock.
            if not self.trigger.is_on():
                self.deactivate()
                return

            if answer is None:
                self.overlay.show("Lỗi AI")
            elif answer.strip().upper().startswith("NO_QUESTION"):
                self.overlay.show("Không thấy câu hỏi")
            elif answer.strip().lower().startswith(("tôi là trợ lý", "tôi là ai", "tôi là gemini")):
                # Một số model cũ trả lời nhầm câu lệnh hệ thống thay vì đọc ảnh.
                self.overlay.show("Không thấy câu hỏi")
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
            print(" Bật CapsLock  -> đọc màn hình + Gemini trả lời")
        else:
            print(" Bật CapsLock  -> hiện watermark")
        print(" Tắt CapsLock  -> ẩn kết quả")
        print(f" Kiểm tra mỗi: {self.trigger.poll_interval:.2f}s")
        print(" Chạy ngầm: dùng Stop_StealthHelper.vbs để thoát | Chạy console: Ctrl+C")
        print("=" * 60, flush=True)

        state = self.trigger.is_on()
        print(f" Trạng thái CapsLock hiện tại: {'BẬT' if state else 'TẮT'}\n", flush=True)

"""Theo dõi trạng thái phím CapsLock trên Windows (chỉ dùng thư viện chuẩn).

Quy ước của app:
    - CapsLock BẬT  -> app hoạt động (activate)
    - CapsLock TẮT  -> app dừng chạy (deactivate)

Chỉ đọc trạng thái (polling qua Win32 API), không cần hook bàn phím,
không cần cài thêm thư viện ngoài.
"""

from __future__ import annotations

import ctypes
import sys
import time
from typing import Callable, Optional

VK_CAPITAL = 0x14  # mã phím CapsLock


def is_supported_platform() -> bool:
    return sys.platform.startswith("win")


class CapsLockTrigger:
    """Chuyển trạng thái CapsLock thành sự kiện bật/tắt app."""

    backend = "Win32 (GetAsyncKeyState)"

    def __init__(self, poll_interval: float = 0.1):
        if not is_supported_platform():
            raise RuntimeError(
                f"App chỉ hỗ trợ Windows, nền tảng hiện tại là '{sys.platform}'."
            )

        try:
            poll_interval = float(poll_interval)
        except (TypeError, ValueError):
            poll_interval = 0.1
        # Chặn giá trị vô lý: quá nhỏ sẽ ngốn CPU, quá lớn sẽ phản hồi chậm.
        self.poll_interval = min(max(poll_interval, 0.02), 1.0)

        self._user32 = ctypes.windll.user32  # type: ignore[attr-defined]

    def is_on(self) -> bool:
        """True nếu CapsLock đang bật."""
        # Bit thấp (0x0001) trong kết quả là trạng thái bật/tắt của phím toggle.
        state = self._user32.GetAsyncKeyState(VK_CAPITAL)
        if state == 0:
            # Một số phiên bản Windows trả 0 -> dùng GetKeyState làm dự phòng.
            state = self._user32.GetKeyState(VK_CAPITAL)
        return bool(state & 0x0001)

    def listen(
        self,
        on_activate: Callable[[], None],
        on_deactivate: Callable[[], None],
        should_stop: Optional[Callable[[], bool]] = None,
        on_tick: Optional[Callable[[], None]] = None,
    ) -> None:
        """Lặp vô hạn, chỉ gọi callback khi trạng thái CapsLock thay đổi.

        Lần đọc đầu tiên luôn được đồng bộ: nếu CapsLock đang bật thì app
        được bật ngay, nếu đang tắt thì app ở trạng thái dừng.

        `on_tick` (nếu có) chạy mỗi vòng lặp trên chính thread đang gọi
        listen. Dùng để làm các việc bắt buộc phải chạy đúng thread tạo
        cửa sổ overlay, ví dụ SetParent/SetWindowLongPtr.
        """
        active: Optional[bool] = None

        while True:
            if should_stop is not None and should_stop():
                return

            if on_tick is not None:
                try:
                    on_tick()
                except Exception:
                    pass

            state = self.is_on()

            if state != active:
                active = state
                if active:
                    on_activate()
                else:
                    on_deactivate()

            time.sleep(self.poll_interval)

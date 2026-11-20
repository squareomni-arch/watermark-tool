"""Gemini API helper - đọc màn hình và trả lời câu hỏi.

Chỉ dùng thư viện chuẩn + Pillow (để chụp màn hình). Gọi trực tiếp REST API
của Gemini nên không cần cài `google-generativeai`.

Biến môi trường / config:
    GEMINI_API_KEY: API key của Gemini.
    config.json -> "gemini": { "api_key": "...", "model": "..." }
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional

from .http_utils import urlopen

DEFAULT_MODEL = "gemini-3.1-flash-lite"
# Nếu model chính bận / không còn được hỗ trợ thì thử lần lượt các model này.
# Chỉ nên để các model nhỏ, phản hồi nhanh; không bỏ model nặng vào đây.
DEFAULT_FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
]
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

DEFAULT_MODE = "short"
MODE_SHORT = "short"
MODE_DETAIL = "detail"

DEFAULT_PROMPT = (
    "Ảnh chụp màn hình máy tính sẽ được cung cấp riêng."
    " Nhiệm vụ duy nhất của bạn: đọc văn bản TRONG ẢNH,"
    " không trả lời câu hỏi trong hướng dẫn này."
    " Nếu trong ảnh có câu hỏi hoặc bài tập điền từ/chỗ trống cần trả lời,"
    " hãy trả lời ngắn gọn chính xác, tối đa 15 từ, tiếng Việt, một dòng."
    " Nếu trong ảnh không có câu hỏi hoặc bài tập cần trả lời,"
    " chỉ xuất đúng một từ NO_QUESTION."
    " Không giới thiệu bản thân, không thêm lời dẫn."
)

DETAIL_PROMPT = (
    "Ảnh chụp màn hình máy tính được cung cấp riêng."
    " Hãy đọc TOÀN BỘ văn bản trong ảnh thật chính xác: cả phần bên trái"
    " (đoạn văn / passage) và phần bên phải (câu hỏi)."
    "\n\nNhiệm vụ: trả lời TẤT CẢ câu hỏi đang hiển thị trong ảnh.\n"
    "- Câu TRUE / FALSE / NOT GIVEN: chỉ ghi đáp án.\n"
    "- Câu điền từ / chỗ trống: ghi từ cần điền đúng như trong ô chọn.\n"
    "- Câu trắc nghiệm A/B/C/D: ghi chữ cái đáp án.\n"
    "- Câu matching heading / researcher: ghi đúng mã (i, ii, ... hoặc A-E).\n"
    "\nĐịnh dạng bắt buộc: mỗi câu một dòng, dạng 'số. đáp án',"
    " ví dụ '1. TRUE', '27. half-yawns', '33. B'."
    " Không giải thích, không thêm lời dẫn, không giới thiệu bản thân."
    " Nếu ảnh không có câu hỏi nào, chỉ xuất đúng một từ NO_QUESTION."
)

BASE_GENERATION_CONFIG = {
    "temperature": 0.1,
    "topP": 0.9,
}


class ScreenCaptureBlockedError(RuntimeError):
    """Ném khi Windows không cho chụp màn hình hoặc trả về ảnh đen."""


class GeminiHelper:
    """Gọi Gemini Flash để đọc văn bản trên màn hình và trả lời."""

    def __init__(self, settings: Optional[Dict] = None):
        settings = dict(settings or {})

        self.api_key = str(
            settings.get("api_key")
            or os.environ.get("GEMINI_API_KEY")
            or ""
        ).strip()
        self.model = str(settings.get("model") or DEFAULT_MODEL).strip()
        self.fallback_models = [
            str(m).strip()
            for m in settings.get("fallback_models", [])
            if str(m).strip()
        ]
        if not self.fallback_models:
            self.fallback_models = list(DEFAULT_FALLBACK_MODELS)
        self.mode = str(settings.get("mode") or DEFAULT_MODE).strip().lower()
        if self.mode not in (MODE_SHORT, MODE_DETAIL):
            self.mode = DEFAULT_MODE
        self.prompt = str(settings.get("prompt") or DEFAULT_PROMPT)

        try:
            self.timeout = max(5, int(settings.get("timeout", 12)))
        except (TypeError, ValueError):
            self.timeout = 12

        try:
            self.detail_timeout = max(
                self.timeout, int(settings.get("detail_timeout", 40))
            )
        except (TypeError, ValueError):
            self.detail_timeout = 40

        try:
            self.max_attempts = max(1, int(settings.get("max_attempts", 2)))
        except (TypeError, ValueError):
            self.max_attempts = 2

        try:
            self.retries_per_model = max(1, int(settings.get("retries_per_model", 1)))
        except (TypeError, ValueError):
            self.retries_per_model = 1

        try:
            self.max_image_dimension = max(640, int(settings.get("max_image_dimension", 1920)))
        except (TypeError, ValueError):
            self.max_image_dimension = 1920

        try:
            self.detail_image_dimension = max(
                1280, int(settings.get("detail_image_dimension", 2560))
            )
        except (TypeError, ValueError):
            self.detail_image_dimension = 2560

        self.available = bool(self.api_key)
        self._temporarily_unlocked_windows: list = []
        self._exam_capture_hwnd: Optional[int] = None

    # ------------------------------------------------------------------ chụp ảnh
    @staticmethod
    def _is_black_screenshot(image) -> bool:
        """Kiểm tra ảnh chụp có phải toàn đen không.

        Windows thường trả về ảnh đen khi chụp secure desktop hoặc nội dung
        được bảo vệ (DRM). Nếu ảnh gần như đen hoàn toàn, gửi cho Gemini cũng
        vô nghĩa nên cần báo riêng cho người dùng.
        """
        try:
            from PIL import ImageStat

            stats = ImageStat.Stat(image.convert("L"))
            # Giá trị kênh xám trung bình; ảnh bị chặn thường < 4/255.
            return float(stats.mean[0]) < 4.0
        except Exception:
            return False

    @staticmethod
    def _unlock_excluded_from_capture() -> list:
        """Tạm tắt WDA_EXCLUDEFROMCAPTURE của các cửa sổ đang bảo vệ.

        Trả về danh sách `{hwnd, pid, ...}` để người gọi bật lại
        WDA_EXCLUDEFROMCAPTURE sau khi chụp xong. Bắt buộc phải gọi
        `_restore_capture_protection()` ngay sau `ImageGrab.grab()`, nếu không
        ExamLockdown sẽ phát hiện tamper và chuyển sang trạng thái khóa.
        """
        if not sys.platform.startswith("win"):
            return []

        try:
            from .wda_unlock import unlock_all_excluded_windows

            return unlock_all_excluded_windows(quiet=True)
        except Exception as exc:
            print(f"[WARNING] Không tắt được WDA_EXCLUDEFROMCAPTURE: {exc}")
            return []

    @staticmethod
    def _restore_capture_protection(unlocked: list) -> None:
        """Bật lại WDA_EXCLUDEFROMCAPTURE cho các cửa sổ vừa tạm mở."""
        if not unlocked or not sys.platform.startswith("win"):
            return

        try:
            from .wda_unlock import restore_window

            for window in unlocked:
                hwnd = int(window.get("hwnd") or 0)
                if hwnd:
                    try:
                        restore_window(hwnd)
                    except Exception:
                        pass
        except Exception as exc:
            print(f"[WARNING] Không khôi phục được WDA_EXCLUDEFROMCAPTURE: {exc}")

    def restore_capture_protection_now(self) -> None:
        """Khôi phục ngay các cửa sổ WDA còn đang bị tool tạm mở.

        App gọi phương thức này khi CapsLock tắt để đảm bảo ExamLockdown
        không bao giờ phát hiện WDA bị gỡ sau khi tool đã chụp xong.
        """
        unlocked, self._temporarily_unlocked_windows = (
            self._temporarily_unlocked_windows,
            [],
        )
        if unlocked:
            print(f"[WDA] Đang khôi phục {len(unlocked)} cửa sổ...", flush=True)
            self._restore_capture_protection(unlocked)
            print("[WDA] Khôi phục xong.", flush=True)

    def _try_capture_exam_content_without_wda(self):
        """Chụp nội dung WebView2/app thi bằng PrintWindow, không đụng WDA.

        Nếu thành công thì không cần gỡ WDA_EXCLUDEFROMCAPTURE, do đó
        ExamLockdown sẽ không phát hiện tamper và không làm hỏng submit.
        """
        if not sys.platform.startswith("win"):
            print("[CAPTURE] Không phải Windows, bỏ PrintWindow.", flush=True)
            return None

        try:
            from .screen_capture import (
                capture_hwnd_via_printwindow,
                find_exam_content_window,
            )
        except Exception as exc:
            print(f"[CAPTURE] Không import screen_capture: {exc!r}", flush=True)
            return None

        exam_hwnd = self._exam_capture_hwnd
        if exam_hwnd is None:
            print("[CAPTURE] Chưa có _exam_capture_hwnd, bỏ PrintWindow.", flush=True)
            return None

        print(f"[CAPTURE] Tìm child window của hwnd={exam_hwnd}...", flush=True)
        child_hwnd = find_exam_content_window(exam_hwnd) or exam_hwnd
        print(f"[CAPTURE] Dùng hwnd={child_hwnd} để PrintWindow.", flush=True)
        image = capture_hwnd_via_printwindow(child_hwnd)
        if image is None:
            print("[CAPTURE] PrintWindow trả về None.", flush=True)
            return None

        image = image.convert("RGB")
        if self._is_black_screenshot(image):
            print("[CAPTURE] PrintWindow trả về ảnh đen.", flush=True)
            return None
        print("[CAPTURE] PrintWindow thành công.", flush=True)
        return image

    def _capture_screen_as_jpeg_b64(self, detailed: bool = False) -> Optional[str]:
        """Chụp màn hình chính, nén nhỏ và trả về ảnh JPEG dạng base64.

        Ảnh to (4K/Retina) là nguyên nhân phổ biến khiến Gemini timeout. Ta
        giữ nguyên tỉ lệ nhưng giới hạn chiều dài cạnh dài để request nhanh hơn.
        Chế độ chi tiết giữ ảnh lớn hơn để đọc chữ nhỏ (ví dụ đề IELTS).

        Nếu Windows chặn chụp màn hình (secure desktop / DRM) thì ném
        `ScreenCaptureBlockedError` để app hiển thị thông báo rõ ràng.
        """
        try:
            from PIL import Image, ImageGrab
        except ImportError:
            print(
                "[ERROR] Chưa cài thư viện Pillow. "
                "Chạy: python -m pip install Pillow"
            )
            return None

        try:
            print("[CAPTURE] Bắt đầu chụp màn hình...", flush=True)
            # Đường ưu tiên: chụp thẳng child window của app thi bằng
            # PrintWindow. Không cần gỡ WDA -> không gây tamper.
            image = self._try_capture_exam_content_without_wda()
            if image is not None:
                print("[CAPTURE] Dùng PrintWindow child window, không đụng WDA.", flush=True)
            else:
                print("[CAPTURE] PrintWindow không dùng được, fallback ImageGrab...", flush=True)
                # Đường dự phòng: tạm gỡ WDA, chụp màn hình, rồi bật lại NGAY.
                print("[CAPTURE] Đang quét WDA_EXCLUDEFROMCAPTURE...", flush=True)
                unlocked = self._unlock_excluded_from_capture()
                print(f"[CAPTURE] WDA quét xong: unlocked={bool(unlocked)}", flush=True)
                self._temporarily_unlocked_windows = unlocked
                try:
                    try:
                        print("[CAPTURE] Đang gọi ImageGrab.grab()...", flush=True)
                        image = ImageGrab.grab()
                        print("[CAPTURE] ImageGrab.grab() đã trả về.", flush=True)
                    finally:
                        print("[CAPTURE] Đang khôi phục WDA...", flush=True)
                        self.restore_capture_protection_now()
                        print("[CAPTURE] Đã khôi phục WDA.", flush=True)
                except OSError as exc:
                    raise ScreenCaptureBlockedError(
                        f"Windows không cho phép chụp màn hình lúc này: {exc}"
                    ) from exc

                if image is None:
                    raise ScreenCaptureBlockedError(
                        "Windows trả về ảnh rỗng khi chụp màn hình."
                    )
                image = image.convert("RGB")
                print("[CAPTURE] ImageGrab đã chụp xong.", flush=True)

                if self._is_black_screenshot(image):
                    # WDA có thể được app bật lại ngay trước lúc chụp.
                    # Thử thêm một lần, vẫn bật lại ngay sau khi chụp.
                    unlocked = self._unlock_excluded_from_capture()
                    self._temporarily_unlocked_windows = unlocked
                    try:
                        try:
                            image = ImageGrab.grab()
                        finally:
                            self.restore_capture_protection_now()
                    except OSError:
                        image = None
                    if image is not None:
                        image = image.convert("RGB")

            if image is None or self._is_black_screenshot(image):
                raise ScreenCaptureBlockedError(
                    "Ảnh chụp bị đen hoàn toàn. Nội dung có thể đang bị Windows"
                    " chặn chụp (secure desktop, DRM, game hoặc video có bảo vệ)."
                )

            print("[CAPTURE] Chuẩn bị nén ảnh và gửi Gemini...", flush=True)

            if detailed:
                max_dimension = self.detail_image_dimension
                quality = 88
            else:
                max_dimension = self.max_image_dimension
                quality = 78

            width, height = image.size
            max_side = max(width, height)
            if max_side > max_dimension:
                scale = max_dimension / max_side
                new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
                try:
                    resampling = Image.Resampling.LANCZOS
                except AttributeError:
                    resampling = Image.LANCZOS  # type: ignore[attr-defined]
                image = image.resize(new_size, resampling)

            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
            return base64.b64encode(buffer.getvalue()).decode("ascii")
        except ScreenCaptureBlockedError:
            raise
        except Exception as exc:
            print(f"[ERROR] Không chụp được màn hình: {exc}")
            return None

    # ------------------------------------------------------------------ gọi API
    def _generate(self, image_b64: str, detailed: bool = False) -> Optional[str]:
        if not self.available:
            return None

        # Thử model chính trước, sau đó thử các model dự phòng nếu bị lỗi/bận.
        # Giới hạn số lần thử để không chờ quá lâu khi nhiều model cùng bận.
        models = []
        for model in [self.model] + self.fallback_models:
            if model and model not in models:
                models.append(model)
        models = models[: self.max_attempts]

        for model in models:
            for attempt in range(1, self.retries_per_model + 1):
                answer = self._generate_with_model(model, image_b64, detailed)
                if answer is not None:
                    return answer
                if attempt < self.retries_per_model:
                    print(f"[AI] Model '{model}' lần {attempt} không được, thử lại...")
                    time.sleep(0.6)

            print(f"[AI] Model '{model}' không trả được kết quả, thử model khác...")

        return None

    def _generate_with_model(
        self, model: str, image_b64: str, detailed: bool = False
    ) -> Optional[str]:
        prompt = DETAIL_PROMPT if detailed else self.prompt

        generation_config = dict(BASE_GENERATION_CONFIG)
        generation_config["maxOutputTokens"] = 1024 if detailed else 128

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inlineData": {
                                "mimeType": "image/jpeg",
                                "data": image_b64,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": generation_config,
        }

        timeout = self.detail_timeout if detailed else self.timeout

        url = API_URL.format(model=urllib.parse.quote(model, safe=""))
        url += "?" + urllib.parse.urlencode({"key": self.api_key})

        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

        started = time.time()
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            print(f"[ERROR] Gemini API trả về lỗi {exc.code}: {detail}")
            return None
        except urllib.error.URLError as exc:
            print(f"[ERROR] Không gọi được Gemini API: {exc}")
            return None
        except TimeoutError:
            print("[ERROR] Gemini API quá thời gian chờ.")
            return None

        elapsed = time.time() - started
        print(f"[AI] {model} trả lời trong {elapsed:.2f}s")

        try:
            data = json.loads(body)
            answer = data["candidates"][0]["content"]["parts"][0]["text"]
            text = str(answer).strip()

            if detailed:
                # Chế độ chi tiết cần giữ xuống dòng để hiển thị từng câu.
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                return "\n".join(lines)

            return " ".join(text.split())
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Không đọc được kết quả Gemini: {exc}")
            print(f"[DEBUG] {body[:500]}")
            return None

    # ------------------------------------------------------------------ public
    def answer_from_screen(self, detailed: Optional[bool] = None) -> Optional[str]:
        """Đọc màn hình và trả về câu trả lời.

        detailed=True  -> trả lời tất cả câu hỏi, mỗi câu một dòng (đề thi).
        detailed=False -> trả lời ngắn gọn 1 dòng.

        Trả về None nếu không có API key / không chụp được / lỗi API.
        Trả về "NO_QUESTION" nếu màn hình không có câu hỏi.
        """
        if detailed is None:
            detailed = self.mode == MODE_DETAIL

        if not self.available:
            print("[CẢNH BÁO] Chưa có GEMINI_API_KEY, không gọi được Gemini.")
            return None

        image_b64 = self._capture_screen_as_jpeg_b64(detailed=detailed)
        if not image_b64:
            return None

        return self._generate(image_b64, detailed=detailed)

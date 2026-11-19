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
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional

DEFAULT_MODEL = "gemini-2.0-flash"
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

DEFAULT_PROMPT = (
    "Bạn là trợ lý đọc màn hình. Ảnh dưới đây là ảnh chụp màn hình máy tính."
    " Hãy đọc văn bản trong ảnh."
    " Nếu trong văn bản có câu hỏi cần trả lời, hãy trả lời ngắn gọn, chính xác,"
    " tối đa 15 từ, trên một dòng bằng tiếng Việt."
    " Nếu không có câu hỏi nào, chỉ trả lời đúng một từ: NO_QUESTION"
)


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
        self.prompt = str(settings.get("prompt") or DEFAULT_PROMPT)

        try:
            self.timeout = max(5, int(settings.get("timeout", 20)))
        except (TypeError, ValueError):
            self.timeout = 20

        self.available = bool(self.api_key)

    # ------------------------------------------------------------------ chụp ảnh
    def _capture_screen_as_jpeg_b64(self) -> Optional[str]:
        """Chụp màn hình chính và trả về ảnh JPEG dạng base64."""
        try:
            from PIL import ImageGrab
        except ImportError:
            print(
                "[ERROR] Chưa cài thư viện Pillow. "
                "Chạy: python -m pip install Pillow"
            )
            return None

        try:
            image = ImageGrab.grab()
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=80)
            return base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception as exc:
            print(f"[ERROR] Không chụp được màn hình: {exc}")
            return None

    # ------------------------------------------------------------------ gọi API
    def _generate(self, image_b64: str) -> Optional[str]:
        if not self.available:
            return None

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": self.prompt},
                        {
                            "inlineData": {
                                "mimeType": "image/jpeg",
                                "data": image_b64,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 256,
            },
        }

        url = API_URL.format(model=urllib.parse.quote(self.model, safe=""))
        url += "?" + urllib.parse.urlencode({"key": self.api_key})

        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
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

        try:
            data = json.loads(body)
            answer = data["candidates"][0]["content"]["parts"][0]["text"]
            return " ".join(str(answer).strip().split())
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            print(f"[ERROR] Không đọc được kết quả Gemini: {exc}")
            print(f"[DEBUG] {body[:500]}")
            return None

    # ------------------------------------------------------------------ public
    def answer_from_screen(self) -> Optional[str]:
        """Đọc màn hình, trả về câu trả lời ngắn.

        Trả về None nếu không có API key / không chụp được / lỗi API.
        Trả về "NO_QUESTION" nếu màn hình không có câu hỏi.
        """
        if not self.available:
            print("[CẢNH BÁO] Chưa có GEMINI_API_KEY, không gọi được Gemini.")
            return None

        image_b64 = self._capture_screen_as_jpeg_b64()
        if not image_b64:
            return None

        return self._generate(image_b64)

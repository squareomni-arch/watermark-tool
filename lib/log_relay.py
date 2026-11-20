"""Đẩy log chẩn đoán lên ntfy.sh để developer đọc từ xa.

Không cần tài khoản. Các file log được gửi lên một topic công khai cố định:

    https://ntfy.sh/squareomni-watermark-logs-v1

Nếu nội dung nhỏ thì nằm ngay trong `message`; nếu lớn hơn giới hạn, ntfy tự
biến thành attachment và trả về URL file .txt trong trường `attachment`.

Lưu ý: topic công khai — bất kỳ ai biết tên topic đều đọc được log. Không gửi
API key / dữ liệu nhạy cảm lên đây.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import List, Optional

TOPIC = "squareomni-watermark-logs-v1"
# Thử nhiều máy chủ ntfy công cộng. Máy của bạn có thể bị chặn ntfy.sh
# nhưng vẫn truy cập được một host khác.
NTFY_HOSTS = [
    "https://ntfy.sh",
    "https://ntfy.envs.net",
    "https://ntfy.adminforge.de",
]


def _post_bytes(payload: bytes, title: str) -> Optional[dict]:
    last_error: Optional[Exception] = None
    for host in NTFY_HOSTS:
        url = f"{host}/{TOPIC}"
        try:
            request = urllib.request.Request(
                url,
                data=payload,
                method="POST",
                headers={
                    "User-Agent": "StealthHelper-LogRelay/1.0",
                    "Title": title[:120],
                    "Content-Type": "text/plain; charset=utf-8",
                },
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return None


def publish_text(text: str, title: str = "stealth-helper-log") -> Optional[dict]:
    """Gửi một chuỗi ngắn lên ntfy. Trả về JSON response nếu thành công."""
    try:
        return _post_bytes(text.encode("utf-8", errors="replace"), title)
    except Exception as exc:
        print(f"[LOG_RELAY] Không gửi được log: {exc}", flush=True)
        return None


def publish_files(paths: List[Path], title: str = "stealth-helper-log") -> Optional[dict]:
    """Gộp nhiều file log và gửi lên ntfy."""
    try:
        parts: List[str] = []
        for path in paths:
            if path.exists():
                parts.append(f"----- {path.name} -----\n{path.read_text(encoding='utf-8', errors='replace')}\n")
        if not parts:
            return None
        return _post_bytes("\n".join(parts).encode("utf-8", errors="replace"), title)
    except Exception as exc:
        print(f"[LOG_RELAY] Không gửi được log: {exc}", flush=True)
        return None


def publish_project_logs(base_dir: Path, title: str = "stealth-helper-log") -> Optional[dict]:
    """Gửi hai file log chính của project."""
    return publish_files(
        [
            base_dir / "data" / "exam_evade.log",
            base_dir / "data" / "stealth_helper.log",
        ],
        title=title,
    )

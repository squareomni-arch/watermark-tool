"""Đẩy log chẩn đoán lên webhook.site để developer đọc từ xa.

Không cần tài khoản. Tool POST nội dung log vào một webhook cố định; developer
gọi API `GET /token/{uuid}/requests` để đọc các request mới nhất.

Dùng webhook.site thay cho ntfy.sh vì một số mạng chặn ntfy. Vẫn giữ ntfy làm
phương án dự phòng.
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import List, Optional

# Webhook cố định do developer tạo. Đừng thay nếu không có lý do.
WEBHOOK_TOKEN = "fc895dba-cabb-434b-905f-6254433d85e7"
WEBHOOK_URL = f"https://webhook.site/{WEBHOOK_TOKEN}"

TOPIC = "squareomni-watermark-logs-v1"
NTFY_HOSTS = [
    "https://ntfy.sh",
    "https://ntfy.envs.net",
    "https://ntfy.adminforge.de",
]

# JSON storage cố định trên ExtendsClass: tool PUT log mới nhất vào đây,
# developer GET đúng URL này để đọc log.
EXTENDSCLASS_BIN_ID = "efccaac"
EXTENDSCLASS_URL = f"https://extendsclass.com/api/json-storage/bin/{EXTENDSCLASS_BIN_ID}"


def _post_webhook(payload: bytes, title: str) -> dict:
    request = urllib.request.Request(
        WEBHOOK_URL,
        data=payload,
        method="POST",
        headers={
            "User-Agent": "StealthHelper-LogRelay/1.0",
            "Content-Type": "text/plain; charset=utf-8",
            "X-Log-Title": title[:120],
        },
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        response.read()
    return {"ok": True, "channel": "webhook"}


def _post_ntfy(payload: bytes, title: str) -> dict:
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
    raise RuntimeError("không có ntfy host khả dụng")


def _put_extendsclass(payload: bytes, title: str) -> dict:
    body = json.dumps(
        {
            "title": title,
            "content": payload.decode("utf-8", errors="replace"),
            "sent_at": time.time(),
        },
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        EXTENDSCLASS_URL,
        data=body,
        method="PUT",
        headers={
            "User-Agent": "StealthHelper-LogRelay/1.0",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        response.read()
    return {"ok": True, "channel": "extendsclass"}


def _post_bytes(payload: bytes, title: str) -> Optional[dict]:
    # Thử lần lượt các kênh công cộng. Kênh nào tới được thì dùng kênh đó.
    for sender in (_post_webhook, _put_extendsclass, _post_ntfy):
        try:
            return sender(payload, title)
        except Exception:
            continue
    return None


def publish_text(text: str, title: str = "stealth-helper-log") -> Optional[dict]:
    """Gửi một chuỗi ngắn lên kênh log từ xa."""
    try:
        return _post_bytes(text.encode("utf-8", errors="replace"), title)
    except Exception as exc:
        print(f"[LOG_RELAY] Không gửi được log: {exc}", flush=True)
        return None


def publish_files(paths: List[Path], title: str = "stealth-helper-log") -> Optional[dict]:
    """Gộp nhiều file log và gửi lên kênh log từ xa."""
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


if __name__ == "__main__":
    import datetime as _dt

    marker = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = publish_text(
        f"RELAY_SELF_TEST marker={marker}\n"
        f"python={__import__('sys').executable}",
        title=f"relay-self-test {marker}",
    )
    print(f"[LOG_RELAY] result={result}", flush=True)
    print("[LOG_RELAY] Xong. Developer sẽ đọc được marker này từ xa.", flush=True)
    try:
        input("Nhấn Enter để thoát...")
    except (EOFError, KeyboardInterrupt):
        pass

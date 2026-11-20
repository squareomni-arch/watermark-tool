"""Gửi log lên GitHub Gist qua tài khoản GitHub của người dùng.

Vì các dịch vụ pastebin/ntfy/webhook đều có thể bị mạng chặn, còn GitHub API
thì chắc chắn tới được (Update_Now đang tải GitHub Release). Cần một lần đăng
nhập GitHub trên máy chạy tool:

    Log_Relay_Login.bat

Sau khi đăng nhập, tool tự tạo một Gist và ghi ID ra data/log_gist_id.txt.
Mỗi lần gửi log sau đó sẽ cập nhật đúng Gist đó. Developer chỉ cần biết URL
Gist một lần để đọc mọi log về sau.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
TOKEN_FILE = BASE_DIR / "data" / "log_github_token.txt"
GIST_ID_FILE = BASE_DIR / "data" / "log_gist_id.txt"

# Client ID của GitHub CLI (app công khai), chỉ xin scope `gist`.
CLIENT_ID = "Iv1.e7b89e013f801f03"
SCOPE = "gist"

DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GISTS_API_URL = "https://api.github.com/gists"


def _post_form(url: str, fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "User-Agent": "StealthHelper-GitHubLogRelay/1.0",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _open_browser(url: str) -> None:
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:
        pass


def login() -> int:
    print("=" * 60)
    print(" Đăng nhập GitHub để gửi log chẩn đoán từ xa")
    print("=" * 60)

    if TOKEN_FILE.exists() and GIST_ID_FILE.exists():
        print(" Đã có sẵn GitHub token + Gist ID.")
        print(f" Token: {TOKEN_FILE}")
        print(f" Gist : {GIST_ID_FILE.read_text(encoding='utf-8').strip()}")
        return 0

    device = _post_form(
        DEVICE_CODE_URL,
        {"client_id": CLIENT_ID, "scope": SCOPE},
    )
    verification_uri = str(device.get("verification_uri") or "https://github.com/login/device")
    user_code = str(device.get("user_code") or "")
    device_code = str(device.get("device_code") or "")
    interval = max(1, int(device.get("interval") or 5))
    expires_in = int(device.get("expires_in") or 900)

    print()
    print(" Mã xác nhận GitHub của bạn:")
    print()
    print(f"     {user_code}")
    print()
    print(" Mình đang cố mở trình duyệt tới:")
    print(f"     {verification_uri}")
    print()
    print(" Nếu trình duyệt không tự mở, hãy mở URL trên và nhập mã trên.")
    print(f" Mã hết hạn sau {expires_in // 60} phút.")
    _open_browser(verification_uri)

    deadline = time.monotonic() + expires_in
    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            token = _post_form(
                ACCESS_TOKEN_URL,
                {
                    "client_id": CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
        except Exception as exc:
            print(f" [LOGIN] Chưa xác nhận xong: {exc}")
            continue

        access_token = str(token.get("access_token") or "")
        if access_token:
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(access_token, encoding="ascii")
            try:
                os.chmod(TOKEN_FILE, 0o600)
            except OSError:
                pass
            print(" [OK] Đã lấy GitHub token.")

            gist_id, gist_url = _ensure_gist(access_token)
            if gist_id:
                GIST_ID_FILE.write_text(gist_id, encoding="ascii")
                print(f" [OK] Đã tạo Gist chứa log.")
                print(f" URL Gist: {gist_url}")
                print()
                print(" HÃY GỬI URL NÀY CHO DEVELOPER MỘT LẦN.")
                print(" Từ lần sau developer tự đọc log qua URL này.")
                return 0
            print(" [ERROR] Tạo Gist thất bại.")
            return 1

        error = str(token.get("error") or "")
        if error == "authorization_pending":
            continue
        if error:
            print(f" [LOGIN] GitHub trả lỗi: {error} - {token.get('error_description')}")
            if error not in ("slow_down",):
                return 1

    print(" [ERROR] Hết thời gian chờ xác nhận GitHub.")
    return 1


def _ensure_gist(token: str) -> tuple:
    """Tạo Gist mới nếu chưa có; trả về (gist_id, gist_url)."""
    if GIST_ID_FILE.exists():
        gist_id = GIST_ID_FILE.read_text(encoding="ascii").strip()
        if gist_id:
            return gist_id, f"https://gist.github.com/{gist_id}"

    payload = {
        "description": "Stealth Helper diagnostic logs",
        "public": True,
        "files": {
            "watermark-logs.txt": {
                "content": "Stealth Helper log relay initialized.\n",
            }
        },
    }
    request = urllib.request.Request(
        GISTS_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "User-Agent": "StealthHelper-GitHubLogRelay/1.0",
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.loads(response.read().decode("utf-8"))

    gist_id = str(data.get("id") or "")
    gist_url = str(data.get("html_url") or (f"https://gist.github.com/{gist_id}" if gist_id else ""))
    return gist_id, gist_url


def relay_project_logs(base_dir: Path, title: str = "stealth-helper-log") -> bool:
    """Gửi hai file log lên Gist. Trả về True nếu gửi thành công."""
    if not TOKEN_FILE.exists():
        return False

    token = TOKEN_FILE.read_text(encoding="ascii").strip()
    if not token:
        return False

    try:
        gist_id, _ = _ensure_gist(token)
        if not gist_id:
            return False
    except Exception:
        return False

    parts = []
    for name in ("exam_evade.log", "stealth_helper.log"):
        path = base_dir / "data" / name
        if path.exists():
            parts.append(
                f"----- {name} -----\n{path.read_text(encoding='utf-8', errors='replace')}\n"
            )
    if not parts:
        content = "(chưa có log)\n"
    else:
        content = "\n".join(parts)

    payload = {
        "description": f"Stealth Helper logs - {title}",
        "files": {
            "watermark-logs.txt": {
                "content": content,
            }
        },
    }
    request = urllib.request.Request(
        f"{GISTS_API_URL}/{gist_id}",
        data=json.dumps(payload).encode("utf-8"),
        method="PATCH",
        headers={
            "User-Agent": "StealthHelper-GitHubLogRelay/1.0",
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        response.read()
    return True


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        return login()
    print("Dùng Log_Relay_Login.bat để đăng nhập lần đầu.")
    print("Hoặc gọi relay_project_logs() từ app nền.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[ERROR] {exc}")
        raise

#!/usr/bin/env python3
"""Cập nhật Session Assistant bằng tay khi auto-update bị lỗi.

Cách dùng trên Windows:
    python update_now.py

Hoặc double-click `Update_Now.bat`.

Luồng hoạt động:
    1. Đọc file `dist/version.json` từ GitHub.
    2. Tải file zip bản mới.
    3. Tắt app đang chạy (nếu có).
    4. Giải nén đè lên thư mục app, KHÔNG ghi đè:
       - data/config.json (giữ API key)
       - data/session_assistant.pid / data/session_assistant.log
    5. Khởi động lại `Start_SessionAssistant.vbs`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from lib.http_utils import urlopen  # noqa: E402

GITHUB_REPO = "squareomni-arch/watermark-tool"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
MANIFEST_URL = "https://raw.githubusercontent.com/squareomni-arch/watermark-tool/main/dist/version.json"

PROTECTED_RELATIVE_PATHS = {
    "data/config.json",
    "data/session_assistant.pid",
    "data/session_assistant.log",
    ".DS_Store",
}

# File launcher/log của bản cũ cần xóa khi nâng cấp để không còn tên cũ trên đĩa.
OBSOLETE_RELATIVE_PATHS = {
    "Start_" + "StealthHelper.vbs",
    "Stop_" + "StealthHelper.vbs",
    "Stop_" + "StealthHelper.bat",
}

SKIPPED_DIR_NAMES = {"__pycache__", ".git", "dist", "build", "venv", ".venv"}

LOG_FILE = BASE_DIR / "data" / "update_now.log"


def _write_log(message: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
            )
    except OSError:
        pass


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SessionAssistant-UpdateNow/1.0"},
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_release_from_api(data: dict) -> tuple:
    """Lấy (version, download_url, notes) từ dữ liệu GitHub Release API."""
    version = str(data.get("tag_name") or data.get("name") or "").strip()
    version = version.lstrip("vV")
    notes = str(data.get("body") or data.get("name") or "").strip()

    download_url = ""
    assets = data.get("assets") or []
    for asset in assets:
        name = str(asset.get("name") or "")
        if name.lower().endswith(".zip"):
            download_url = str(asset.get("browser_download_url") or "").strip()
            break

    return version, download_url, notes


def _parse_release_from_manifest(data: dict) -> tuple:
    version = str(data.get("version") or "").strip()
    version = version.lstrip("vV")
    download_url = str(data.get("download_url") or "").strip()
    notes = str(data.get("notes") or "").strip()
    return version, download_url, notes


def download_file(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SessionAssistant-UpdateNow/1.0"},
    )
    with urlopen(request, timeout=180) as response:
        with open(destination, "wb") as handle:
            shutil.copyfileobj(response, handle)


def stop_running_app() -> None:
    # Dừng cả bản mới và bản cũ nếu người dùng chưa từng chạy Update_Now.
    pid_files = [
        BASE_DIR / "data" / "session_assistant.pid",
        BASE_DIR / "data" / ("stealth" + "_helper.pid"),
    ]
    for pid_file in pid_files:
        if not pid_file.exists():
            continue

        try:
            pid = int(pid_file.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            continue

        if sys.platform.startswith("win") and pid > 0:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
            )
        try:
            pid_file.unlink()
        except OSError:
            pass


def install_files(extracted_dir: Path, dry_run: bool = False) -> int:
    copied = 0
    for source in extracted_dir.rglob("*"):
        rel = source.relative_to(extracted_dir)
        rel_posix = rel.as_posix()

        if rel_posix in PROTECTED_RELATIVE_PATHS:
            continue
        if any(part in SKIPPED_DIR_NAMES for part in rel.parts):
            continue
        if source.is_dir():
            continue
        if source.name.endswith(".pyc"):
            continue

        target = BASE_DIR / rel
        if dry_run:
            print(f"  [dry-run] {rel_posix}")
            copied += 1
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1

    # Xóa file launcher của bản cũ (nếu đang nâng cấp từ bản watermark cũ).
    if not dry_run:
        for obsolete in OBSOLETE_RELATIVE_PATHS:
            try:
                (BASE_DIR / obsolete).unlink(missing_ok=True)
            except OSError:
                pass

    return copied


def relaunch_app() -> None:
    vbs_launcher = BASE_DIR / "Start_SessionAssistant.vbs"
    if sys.platform.startswith("win") and vbs_launcher.exists():
        subprocess.Popen(["wscript.exe", str(vbs_launcher)])
        return

    subprocess.Popen(
        [sys.executable, str(BASE_DIR / "main.py")],
        cwd=str(BASE_DIR),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Cập nhật Session Assistant bằng tay")
    parser.add_argument(
        "--manifest-url",
        default=MANIFEST_URL,
        help="URL tới file dist/version.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chỉ tải và liệt kê file, không cài đè hay khởi động lại",
    )
    args = parser.parse_args()

    print("=" * 60)
    print(" SESSION ASSISTANT - CẬP NHẬT BẰNG TAY")
    print("=" * 60)

    version = ""
    download_url = ""
    notes = ""

    if args.manifest_url and args.manifest_url != MANIFEST_URL:
        # Người dùng chỉ định file manifest khác -> ưu tiên dùng đúng file đó.
        try:
            data = fetch_json(args.manifest_url)
            version, download_url, notes = _parse_release_from_manifest(data)
        except Exception as exc:
            print(f"[ERROR] Không tải được manifest: {exc}")
            return 1
    else:
        # Mặc định ưu tiên GitHub API vì không bị cache như raw.githubusercontent.
        try:
            data = fetch_json(RELEASES_API_URL)
            version, download_url, notes = _parse_release_from_api(data)
        except Exception as exc:
            print(f"[WARNING] Không đọc được GitHub API ({exc}). Thử manifest...")

        if not download_url:
            try:
                data = fetch_json(args.manifest_url or MANIFEST_URL)
                version, download_url, notes = _parse_release_from_manifest(data)
            except Exception as exc:
                print(f"[ERROR] Không tải được manifest: {exc}")
                return 1

    if not version or not download_url:
        print("[ERROR] Không lấy được version/download_url.")
        return 1

    print(f"Bản mới nhất: v{version}")
    if notes:
        print(f"Mô tả: {notes}")
    print(f"Link tải: {download_url}")
    print()

    tmp_dir = Path(tempfile.mkdtemp(prefix="session_assistant_update_now_"))
    zip_path = tmp_dir / "update.zip"
    extracted_dir = tmp_dir / "extracted"

    try:
        print("Đang tải bản cập nhật...")
        download_file(download_url, zip_path)
        print(f"Đã tải xong: {zip_path.stat().st_size} bytes")

        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(extracted_dir)

        if args.dry_run:
            print("\nDanh sách file sẽ cập nhật:")
            count = install_files(extracted_dir, dry_run=True)
            print(f"\nTổng cộng {count} file.")
            return 0

        print("Đang dừng app cũ (nếu đang chạy)...")
        stop_running_app()

        print("Đang cài file mới...")
        count = install_files(extracted_dir)
        print(f"Đã cập nhật {count} file.")

        print("Đang khởi động lại app...")
        relaunch_app()
        print("Xong. Hãy bật CapsLock để test.")
        _write_log(f"Cập nhật thành công lên v{version}")
        return 0
    except Exception as exc:
        print(f"[ERROR] Cập nhật thất bại: {exc}")
        traceback.print_exc()
        _write_log(f"Cập nhật thất bại: {exc}")
        return 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

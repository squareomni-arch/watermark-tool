#!/usr/bin/env python3
"""Cập nhật Stealth Helper bằng tay khi auto-update bị lỗi.

Cách dùng trên Windows:
    python update_now.py

Hoặc double-click `Update_Now.bat`.

Luồng hoạt động:
    1. Đọc file `dist/version.json` từ GitHub.
    2. Tải file zip bản mới.
    3. Tắt app đang chạy (nếu có).
    4. Giải nén đè lên thư mục app, KHÔNG ghi đè:
       - data/config.json (giữ API key)
       - data/stealth_helper.pid / data/stealth_helper.log
    5. Khởi động lại `Start_StealthHelper.vbs`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from lib.http_utils import urlopen  # noqa: E402

MANIFEST_URL = "https://raw.githubusercontent.com/squareomni-arch/watermark-tool/main/dist/version.json"

PROTECTED_RELATIVE_PATHS = {
    "data/config.json",
    "data/stealth_helper.pid",
    "data/stealth_helper.log",
    ".DS_Store",
}

SKIPPED_DIR_NAMES = {"__pycache__", ".git", "dist", "build", "venv", ".venv"}


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "StealthHelper-UpdateNow/1.0"},
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def download_file(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "StealthHelper-UpdateNow/1.0"},
    )
    with urlopen(request, timeout=180) as response:
        with open(destination, "wb") as handle:
            shutil.copyfileobj(response, handle)


def stop_running_app() -> None:
    pid_file = BASE_DIR / "data" / "stealth_helper.pid"
    if not pid_file.exists():
        return

    try:
        pid = int(pid_file.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return

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

    return copied


def relaunch_app() -> None:
    vbs_launcher = BASE_DIR / "Start_StealthHelper.vbs"
    if sys.platform.startswith("win") and vbs_launcher.exists():
        subprocess.Popen(["wscript.exe", str(vbs_launcher)])
        return

    subprocess.Popen(
        [sys.executable, str(BASE_DIR / "main.py")],
        cwd=str(BASE_DIR),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Cập nhật Stealth Helper bằng tay")
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
    print(" STEALTH HELPER - CẬP NHẬT BẰNG TAY")
    print("=" * 60)

    try:
        manifest = fetch_json(args.manifest_url)
    except Exception as exc:
        print(f"[ERROR] Không tải được manifest: {exc}")
        return 1

    version = str(manifest.get("version") or "").strip()
    download_url = str(manifest.get("download_url") or "").strip()
    notes = str(manifest.get("notes") or "").strip()

    if not version or not download_url:
        print("[ERROR] Manifest thiếu version/download_url.")
        return 1

    print(f"Bản mới nhất: v{version}")
    if notes:
        print(f"Mô tả: {notes}")
    print(f"Link tải: {download_url}")
    print()

    tmp_dir = Path(tempfile.mkdtemp(prefix="stealth_helper_update_now_"))
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
        return 0
    except Exception as exc:
        print(f"[ERROR] Cập nhật thất bại: {exc}")
        return 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

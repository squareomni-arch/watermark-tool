#!/usr/bin/env python3
"""Đóng gói bản cập nhật Session Assistant thành file zip + version.json.

Dùng trên máy dev (Windows/macOS/Linux) mỗi lần muốn phát hành bản mới.

Ví dụ:
    python build_update.py --version 1.0.1 \
        --download-url "https://your-server.com/session-assistant-1.0.1.zip" \
        --notes "Nang cap Gemini, them auto-update"

Kết quả nằm trong thư mục dist/:
    dist/session-assistant-1.0.1.zip   -> bản cập nhật người dùng sẽ tải
    dist/version.json               -> upload lên URL mà `update.check_url` trỏ tới

Lưu ý:
    - File `data/config.json` KHÔNG được đóng vào bản cập nhật để không làm mất
      API key / cấu hình riêng của người dùng.
    - Sau khi chạy, `data/version.json` trên máy dev cũng được nâng version.
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / "dist"
LOCAL_VERSION_FILE = BASE_DIR / "data" / "version.json"

EXCLUDED_FILES = {
    ".DS_Store",
    "data/config.json",
    "data/session_assistant.pid",
    "data/session_assistant.log",
    "build_update.py",
}
EXCLUDED_DIR_NAMES = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "dist",
    "build",
}


def read_local_version() -> str:
    try:
        data = json.loads(LOCAL_VERSION_FILE.read_text(encoding="utf-8"))
        return str(data.get("version") or "1.0.0").strip()
    except (OSError, json.JSONDecodeError):
        return "1.0.0"


def bump_patch(version: str) -> str:
    parts = re.findall(r"\d+", version)
    if not parts:
        return "1.0.1"

    numbers = [int(p) for p in parts[:3]]
    while len(numbers) < 3:
        numbers.append(0)
    numbers[-1] += 1
    return f"{numbers[0]}.{numbers[1]}.{numbers[2]}"


def should_include(path: Path, rel: Path) -> bool:
    if path.is_dir():
        return False

    rel_posix = rel.as_posix()
    if rel_posix in EXCLUDED_FILES:
        return False
    if any(part in EXCLUDED_DIR_NAMES for part in rel.parts):
        return False
    if path.name.endswith(".pyc"):
        return False
    if "__pycache__" in path.parts:
        return False

    return True


def build_zip(version: str) -> Path:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DIST_DIR / f"session-assistant-{version}.zip"

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(BASE_DIR.rglob("*")):
            rel = path.relative_to(BASE_DIR)
            if should_include(path, rel):
                archive.write(path, rel)

    return zip_path


def write_version_file(version: str, download_url: str, notes: str) -> Path:
    manifest = {
        "version": version,
        "download_url": download_url,
        "notes": notes,
    }

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = DIST_DIR / "version.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Cập nhật version cục bộ của mã nguồn dev.
    LOCAL_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_VERSION_FILE.write_text(
        json.dumps({"version": version}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Đóng gói bản cập nhật Session Assistant")
    parser.add_argument(
        "--version",
        default=None,
        help="Version bản mới, ví dụ 1.0.1. Mặc định: tự tăng patch từ data/version.json.",
    )
    parser.add_argument(
        "--download-url",
        default="",
        help="URL tải file zip sau khi bạn upload lên server.",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="Mô tả ngắn hiển thị trong popup cập nhật.",
    )
    args = parser.parse_args()

    current = read_local_version()
    version = args.version or bump_patch(current)

    # Quan trọng: ghi version.json TRƯỚC rồi mới đóng zip, nếu không file
    # data/version.json trong bản cập nhật vẫn mang version cũ.
    manifest_path = write_version_file(version, args.download_url, args.notes)
    zip_path = build_zip(version)

    print("=" * 60)
    print("DA DONG GOI BAN CAP NHAT")
    print(f"  Version: {version} (cu: {current})")
    print(f"  Zip:     {zip_path}")
    print(f"  Manifest:{manifest_path}")
    print("=" * 60)
    print()
    print("Cac buoc phat hanh:")
    print("  1. Upload file zip len server/Drive/... lay URL tai truc tiep.")
    print("  2. Chay lai lenh build voi --download-url la URL do.")
    print("  3. Upload dist/version.json len URL ma update.check_url tro toi.")
    print("  4. Nguoi dung se thay popup update khi app kiem tra.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

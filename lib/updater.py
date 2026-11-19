"""Bộ kiểm tra và cài cập nhật cho Stealth Helper.

Cách hoạt động:
    1. Định kỳ tải file JSON điều khiển từ `update.check_url` trong config.
    2. Nếu version trên server mới hơn version cục bộ -> hiện popup Windows.
    3. Người dùng đồng ý -> tải file zip, giải nén đè lên thư mục app
       (KHÔNG ghi đè `data/config.json` để giữ API key của người dùng).
    4. Khởi động lại app bằng `Start_StealthHelper.vbs`.

File version cục bộ: `data/version.json`.
File điều khiển trên server có dạng:
    {
        "version": "1.0.1",
        "download_url": "https://.../watermark-tool-1.0.1.zip",
        "notes": "Mô tả bản mới"
    }
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Optional

from .http_utils import urlopen

_IS_WINDOWS = sys.platform.startswith("win")

BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_VERSION_FILE = BASE_DIR / "data" / "version.json"
PID_FILE = BASE_DIR / "data" / "stealth_helper.pid"

# Những file KHÔNG được đè khi cập nhật (để không mất API key / trạng thái app).
PROTECTED_RELATIVE_PATHS = {
    "data/config.json",
    "data/stealth_helper.pid",
    "data/stealth_helper.log",
    ".DS_Store",
}

# Thư mục bỏ qua khi giải nén bản cập nhật.
SKIPPED_DIR_NAMES = {"__pycache__", ".git", "dist", "build", "venv", ".venv"}


class UpdateInfo:
    def __init__(self, version: str, download_url: str, notes: str):
        self.version = version.strip()
        self.download_url = download_url.strip()
        self.notes = notes.strip()


def _parse_version(version: str) -> tuple:
    """Chuyển '1.2.3' / 'v1.2.3' thành tuple (1, 2, 3) để so sánh."""
    parts = re.findall(r"\d+", str(version or ""))
    return tuple(int(p) for p in (parts + ["0", "0", "0"])[:3])


def _read_local_version() -> str:
    try:
        data = json.loads(LOCAL_VERSION_FILE.read_text(encoding="utf-8"))
        version = str(data.get("version") or "").strip()
        return version or "0.0.0"
    except (OSError, json.JSONDecodeError):
        return "0.0.0"


def _write_local_version(version: str) -> None:
    try:
        LOCAL_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOCAL_VERSION_FILE.write_text(
            json.dumps({"version": version}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _message_box(title: str, text: str) -> bool:
    """Hiện popup Yes/No trên Windows. Trả về True nếu người dùng bấm Yes."""
    if not _IS_WINDOWS:
        print(f"[UPDATE] {title}: {text}")
        print("[UPDATE] Không phải Windows nên không hiện popup.")
        return False

    MB_YESNO = 0x00000004
    MB_ICONINFORMATION = 0x00000040
    IDYES = 6
    try:
        user32 = ctypes.windll.user32
        user32.MessageBoxW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
        ]
        user32.MessageBoxW.restype = ctypes.c_int
        result = user32.MessageBoxW(
            None, text, title, MB_YESNO | MB_ICONINFORMATION
        )
        return result == IDYES
    except Exception as exc:
        print(f"[UPDATE] Không hiện được popup: {exc}")
        return False


def _info_box(title: str, text: str) -> None:
    if not _IS_WINDOWS:
        print(f"[UPDATE] {title}: {text}")
        return

    MB_OK = 0x00000000
    MB_ICONINFORMATION = 0x00000040
    try:
        user32 = ctypes.windll.user32
        user32.MessageBoxW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
        ]
        user32.MessageBoxW.restype = ctypes.c_int
        user32.MessageBoxW(None, text, title, MB_OK | MB_ICONINFORMATION)
    except Exception:
        pass


def _release_pid_file_if_owner() -> None:
    try:
        if PID_FILE.exists() and PID_FILE.read_text(encoding="ascii").strip() == str(os.getpid()):
            PID_FILE.unlink()
    except (OSError, ValueError):
        pass


class Updater:
    def __init__(self, settings: Optional[Dict] = None):
        settings = dict(settings or {})

        self.enabled = bool(settings.get("enabled", True))
        self.check_url = str(settings.get("check_url") or "").strip()

        try:
            self.interval_hours = max(1, float(settings.get("check_interval_hours", 6)))
        except (TypeError, ValueError):
            self.interval_hours = 6

        self.local_version = _read_local_version()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ thread
    def start(self) -> None:
        """Bắt đầu kiểm tra cập nhật trong một thread nền."""
        if not self.enabled or not self.check_url:
            return
        if self._thread is not None:
            return

        self._thread = threading.Thread(
            target=self._run, name="stealth-updater", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        # Chờ vài giây cho app khởi động xong rồi mới kiểm tra lần đầu.
        first_delay = 5
        if self._stop_event.wait(first_delay):
            return

        while not self._stop_event.is_set():
            try:
                print("[UPDATE] Kiểm tra cập nhật...", flush=True)
                info = self.check_now()
                if info is None:
                    print("[UPDATE] Không lấy được thông tin bản mới (xem lỗi bên trên nếu có).", flush=True)
                elif _parse_version(info.version) > _parse_version(self.local_version):
                    print(f"[UPDATE] Đã có bản mới: v{info.version}", flush=True)
                    self._handle_update(info)
                else:
                    print(
                        f"[UPDATE] Đã là bản mới nhất (máy: v{self.local_version}, "
                        f"server: v{info.version}).",
                        flush=True,
                    )
            except Exception as exc:
                print(f"[UPDATE] Kiểm tra cập nhật lỗi: {exc}", flush=True)

            # Chờ tới lần kiểm tra kế tiếp (có thể bị stop sớm).
            if self._stop_event.wait(self.interval_hours * 3600):
                break

    # ------------------------------------------------------------------ kiểm tra
    def check_now(self) -> Optional[UpdateInfo]:
        """Tải file version.json từ server. Trả về UpdateInfo nếu đọc được."""
        if not self.check_url:
            return None

        urls = [self.check_url] + self._build_fallback_urls(self.check_url)
        errors = []
        best_info: Optional[UpdateInfo] = None

        for url in urls:
            try:
                request = urllib.request.Request(
                    url,
                    headers={"User-Agent": "StealthHelper-Updater/1.0"},
                )
                with urlopen(request, timeout=15) as response:
                    data = json.loads(response.read().decode("utf-8"))

                version = str(data.get("version") or "").strip()
                download_url = str(data.get("download_url") or "").strip()
                notes = str(data.get("notes") or "").strip()

                if not version:
                    continue

                info = UpdateInfo(version, download_url, notes)
                if best_info is None or _parse_version(info.version) > _parse_version(best_info.version):
                    best_info = info
            except Exception as exc:
                errors.append(f"{url}: {exc}")
                continue

        # GitHub API không bị cache như raw.githubusercontent nên thử thêm để
        # lấy ngay release mới nhất nếu manifest CDN bị trễ.
        api_url = self._build_github_api_url(self.check_url)
        if api_url:
            try:
                request = urllib.request.Request(
                    api_url,
                    headers={"User-Agent": "StealthHelper-Updater/1.0"},
                )
                with urlopen(request, timeout=15) as response:
                    data = json.loads(response.read().decode("utf-8"))

                version = str(data.get("tag_name") or data.get("name") or "").strip().lstrip("vV")
                notes = str(data.get("body") or data.get("name") or "").strip()
                download_url = ""
                for asset in data.get("assets") or []:
                    if str(asset.get("name") or "").lower().endswith(".zip"):
                        download_url = str(asset.get("browser_download_url") or "").strip()
                        break

                if version and download_url:
                    info = UpdateInfo(version, download_url, notes)
                    if best_info is None or _parse_version(info.version) > _parse_version(best_info.version):
                        best_info = info
            except Exception as exc:
                errors.append(f"{api_url}: {exc}")

        if best_info is None:
            for error in errors:
                print(f"[UPDATE] Không tải được manifest: {error}", flush=True)
        return best_info

    @staticmethod
    def _build_github_api_url(check_url: str) -> str:
        """Suy ra URL GitHub API của repo từ link raw/jsDelivr."""
        base = check_url.split("?", 1)[0]
        owner = repo = ""

        raw_prefix = "https://raw.githubusercontent.com/"
        if base.startswith(raw_prefix):
            parts = base[len(raw_prefix):].split("/", 3)
            if len(parts) >= 2:
                owner, repo = parts[0], parts[1]

        jsd_prefix = "https://cdn.jsdelivr.net/gh/"
        if base.startswith(jsd_prefix):
            parts = base[len(jsd_prefix):].split("/", 3)
            if len(parts) >= 2 and "@" in parts[1]:
                owner = parts[0]
                repo = parts[1].rsplit("@", 1)[0]

        if owner and repo:
            return f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        return ""

    @staticmethod
    def _build_fallback_urls(check_url: str) -> list:
        """Tạo các URL tương đương (raw <-> jsDelivr) để tránh bị CDN cache lệch."""
        fallbacks = []
        base = check_url.split("?", 1)[0]

        try:
            raw_prefix = "https://raw.githubusercontent.com/"
            if base.startswith(raw_prefix):
                rest = base[len(raw_prefix):]
                parts = rest.split("/", 3)
                if len(parts) >= 4:
                    owner, repo, branch, path = parts
                    fallbacks.append(
                        f"https://cdn.jsdelivr.net/gh/{owner}/{repo}@{branch}/{path}"
                    )
        except Exception:
            pass

        try:
            jsd_prefix = "https://cdn.jsdelivr.net/gh/"
            if base.startswith(jsd_prefix):
                rest = base[len(jsd_prefix):]
                parts = rest.split("/", 3)
                if len(parts) >= 4 and "@" in parts[1]:
                    owner = parts[0]
                    repo, branch = parts[1].rsplit("@", 1)
                    path = parts[2] + "/" + parts[3]
                    fallbacks.append(
                        f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
                    )
        except Exception:
            pass

        return fallbacks

    # ------------------------------------------------------------------ xử lý
    def _handle_update(self, info: UpdateInfo) -> None:
        text = (
            f"Đã có bản mới: v{info.version}\n"
            f"Bản hiện tại: v{self.local_version}\n\n"
            f"{info.notes or 'Không có mô tả.'}\n\n"
            "Bạn có muốn cập nhật ngay không?"
        )
        if not _message_box("Stealth Helper - Cập nhật", text):
            return

        ok, message = self.apply_update(info)
        if ok:
            _info_box("Stealth Helper - Cập nhật", "Cập nhật xong. App sẽ khởi động lại.")
            self._relaunch_app()
        else:
            _info_box("Stealth Helper - Cập nhật", f"Cập nhật thất bại.\n{message}")

    def apply_update(self, info: UpdateInfo) -> tuple:
        """Tải và giải nén bản cập nhật. Trả về (thành_công, thông_báo)."""
        if not info.download_url:
            return False, "Server không cung cấp link tải bản mới."

        tmp_dir = Path(tempfile.mkdtemp(prefix="stealth_helper_update_"))
        zip_path = tmp_dir / "update.zip"
        extracted_dir = tmp_dir / "extracted"

        try:
            self._download(info.download_url, zip_path)
            with zipfile.ZipFile(zip_path, "r") as archive:
                archive.extractall(extracted_dir)

            self._install_files(extracted_dir)
            _write_local_version(info.version)
            self.local_version = info.version
            return True, ""
        except Exception as exc:
            return False, str(exc)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _download(self, url: str, destination: Path) -> None:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "StealthHelper-Updater/1.0"},
        )
        with urlopen(request, timeout=120) as response:
            with open(destination, "wb") as handle:
                shutil.copyfileobj(response, handle)

    def _install_files(self, extracted_dir: Path) -> None:
        """Chép các file từ bản cập nhật vào thư mục app, bỏ file cần bảo vệ."""
        candidates = list(extracted_dir.rglob("*"))
        for source in candidates:
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
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def _relaunch_app(self) -> None:
        _release_pid_file_if_owner()

        vbs_launcher = BASE_DIR / "Start_StealthHelper.vbs"
        if _IS_WINDOWS and vbs_launcher.exists():
            try:
                subprocess.Popen(["wscript.exe", str(vbs_launcher)])
                time.sleep(1)
                os._exit(0)
                return
            except Exception as exc:
                print(f"[UPDATE] Không khởi động lại bằng VBS: {exc}")

        # Dự phòng: chạy lại trực tiếp bằng python/pythonw hiện tại.
        try:
            subprocess.Popen(
                [sys.executable, str(BASE_DIR / "main.py")],
                cwd=str(BASE_DIR),
            )
            time.sleep(1)
            os._exit(0)
        except Exception as exc:
            print(f"[UPDATE] Không khởi động lại được: {exc}")

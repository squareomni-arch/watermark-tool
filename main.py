#!/usr/bin/env python3
"""Stealth Helper - Windows watermark tool.

    Bật CapsLock  -> hiện chữ "Hello" vàng mờ ở góc trên bên phải
    Tắt CapsLock  -> ẩn
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

CONFIG_PATH = BASE_DIR / "data" / "config.json"
PID_FILE = BASE_DIR / "data" / "stealth_helper.pid"
LOG_FILE = BASE_DIR / "data" / "stealth_helper.log"

# Giá trị ExitCode của Windows cho tiến trình còn đang chạy.
STILL_ACTIVE = 259


def setup_background_stdio() -> None:
    """Khi chạy bằng pythonw.exe thì không có console.

    Pythonw thường để sys.stdout/sys.stderr là None; nếu cứ print thì app
    sẽ lỗi. Ta ghi log ra file để vẫn tra cứu được lỗi khi cần.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return

    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        handle = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = handle
        if sys.stderr is None:
            sys.stderr = handle
    except OSError:
        pass


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        print(f"[CẢNH BÁO] Không thấy {CONFIG_PATH}, dùng cấu hình mặc định.")
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[CẢNH BÁO] Không đọc được {CONFIG_PATH} ({exc}), dùng cấu hình mặc định.")
    return {}


def migrate_runtime_config(config: dict) -> dict:
    """Tự sửa những cấu hình AI cũ / chậm đã biết, giữ nguyên API key.

    Mục đích: bản mới vẫn chạy tốt dù người dùng có file config.json cũ.
    """
    changed = False

    features = config.setdefault("features", {})
    feature_defaults = {
        "ai": True,
        "answer_all": True,
        "detail_max_lines": 20,
        "detail_max_chars": 800,
    }
    for key, value in feature_defaults.items():
        if key not in features:
            features[key] = value
            changed = True

    watermark = config.setdefault("watermark", {})
    watermark_defaults = {
        "max_width": 620,
        "box_color": "#121212",
        "box_opacity": 0.82,
    }
    for key, value in watermark_defaults.items():
        if key not in watermark:
            watermark[key] = value
            changed = True

    gemini = config.setdefault("gemini", {})
    defaults = {
        "model": "gemini-3.1-flash-lite",
        "fallback_models": [
            "gemini-3.5-flash-lite",
            "gemini-flash-lite-latest",
        ],
        "timeout": 12,
        "detail_timeout": 40,
        "max_attempts": 2,
        "retries_per_model": 1,
        "max_image_dimension": 1920,
        "detail_image_dimension": 2560,
    }

    for key, value in defaults.items():
        if gemini.get(key) != value:
            gemini[key] = value
            changed = True

    # Luôn giữ API key cũ; nếu chưa có thì dùng biến môi trường.
    if not gemini.get("api_key"):
        env_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if env_key:
            gemini["api_key"] = env_key
            changed = True

    if changed:
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
                json.dump(config, handle, ensure_ascii=False, indent=4)
            print("[CẤU HÌNH] Đã cập nhật cấu hình AI để tăng tốc độ.")
        except OSError as exc:
            print(f"[CẢNH BÁO] Không ghi được {CONFIG_PATH}: {exc}")

    return config


def set_console_title(title: str = "Stealth Helper") -> None:
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        pass


# --------------------------------------------------------------- quản lý PID
def _read_pid() -> Optional[int]:
    try:
        text = PID_FILE.read_text(encoding="ascii").strip()
        return int(text) if text else None
    except (FileNotFoundError, ValueError, OSError):
        return None


def _is_pid_running(pid: int) -> bool:
    """Kiểm tra PID còn sống hay không, tránh trường hợp PID cũ bị tái sử dụng."""
    if pid <= 0:
        return False

    if sys.platform.startswith("win"):
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False

            code = ctypes.c_uint32()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            kernel32.CloseHandle(handle)
            return bool(ok and code.value == STILL_ACTIVE)
        except Exception:
            # Không kiểm tra được thì cứ coi là đang chạy để tránh mở trùng.
            return True

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True


def _acquire_pid_file() -> bool:
    """Tạo file pid để các script Start/Stop biết app đang chạy.

    Trả về False nếu app đã được mở trước đó và vẫn còn sống.
    """
    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    for _ in range(2):
        try:
            with open(PID_FILE, "x", encoding="ascii") as handle:
                handle.write(str(os.getpid()))
            return True
        except FileExistsError:
            old_pid = _read_pid()
            if old_pid == os.getpid():
                return True
            if old_pid and _is_pid_running(old_pid):
                return False

            # File pid cũ nhưng tiến trình đã tắt -> xoá rồi tạo lại.
            try:
                PID_FILE.unlink()
            except OSError:
                pass
        except OSError:
            return False
    return False


def _release_pid_file() -> None:
    """Xoá file pid nếu file đó đúng là do tiến trình hiện tại tạo ra."""
    try:
        if PID_FILE.exists() and PID_FILE.read_text(encoding="ascii").strip() == str(os.getpid()):
            PID_FILE.unlink()
    except (OSError, ValueError):
        pass


def main() -> int:
    setup_background_stdio()

    if not sys.platform.startswith("win"):
        print(f"[ERROR] App chỉ hỗ trợ Windows (nền tảng hiện tại: {sys.platform}).")
        return 1

    config = migrate_runtime_config(load_config())

    if not _acquire_pid_file():
        print("[WARNING] Stealth Helper đang chạy rồi. Không mở thêm bản nữa.")
        return 0

    from lib.app import StealthHelperApp
    from lib.overlay import enable_dpi_awareness
    from lib.updater import Updater

    # Gọi trước khi tạo cửa sổ để chữ không bị nhoè khi Windows scale > 100%.
    enable_dpi_awareness()
    set_console_title("Stealth Helper - CapsLock ON/OFF")

    exam_child = "--exam-child" in sys.argv

    app = None
    updater = None
    try:
        try:
            app = StealthHelperApp(config, exam_child=exam_child)
        except RuntimeError as exc:
            print(f"[ERROR] {exc}")
            return 1
        except Exception:
            import traceback

            print("[ERROR] Không khởi tạo được app:")
            traceback.print_exc()
            return 1

        updater = Updater(config.get("update") or {})
        updater.start()

        try:
            app.start()
        finally:
            if app.overlay is not None:
                app.overlay.destroy()
        return 0
    finally:
        if updater is not None:
            updater.stop()
        _release_pid_file()


if __name__ == "__main__":
    sys.exit(main())

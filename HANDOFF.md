# HANDOFF — watermark-tool / Stealth Helper

Cập nhật: 2026-11-20. Commit gần nhất: `929f28b` (`v1.1.33`).

Repo dev:
- `watermark-tool`: `https://github.com/squareomni-arch/watermark-tool`
- `ExamLockdown`: thư mục `/Users/dev/Desktop/Square Omni/ExamLockdown`, chưa push / chưa có remote.

Máy test là Windows thật. Tool chạy bằng `pythonw.exe` từ:
`C:\Program Files\Python311\pythonw.exe`.

---

## 1. Mục tiêu hiện tại

Stealth Helper hiện có 2 chế độ:

- Không có AI key: CapsLock bật -> hiện watermark "Hello".
- Có AI key: CapsLock bật -> chụp màn hình -> gọi Gemini -> hiện đáp án.

Logic CapsLock mong muốn (theo user):

```text
CapsLock ON  -> chụp hình + hiện đáp án mới
CapsLock OFF -> ẩn text đáp án ngay lập tức
CapsLock ON lại -> chụp hình mới + hiện đáp án mới
CapsLock OFF -> ẩn text đáp án ngay lập tức
```

---

## 2. Bản mới nhất đã phát hành

```text
v1.1.33 - AI worker diagnostics
https://github.com/squareomni-arch/watermark-tool/releases/latest
```

Chạy trên máy Win:

```bat
Update_Now.bat
```

Nếu đang chạy nền thì nên:

```bat
Stop_StealthHelper.bat
Start_StealthHelper.vbs
```

Để debug trực tiếp thì dùng console:

```bat
run.bat
```

Log cần user gửi:

```text
data\exam_evade.log
data\stealth_helper.log
```

---

## 3. Lịch sử thay đổi quan trọng gần đây

- `v1.1.25`: bỏ hoàn toàn việc tạo process con của `ExamLockdown` vì làm ảnh hưởng app thi khi submit.
- `v1.1.26`: sau khi tạm tắt `WDA_EXCLUDEFROMCAPTURE` để chụp thì khôi phục lại ngay. Watcher chạy 20 ms thay vì 120–150 ms.
- `v1.1.27`: khôi phục WDA ngay khi CapsLock tắt.
- `v1.1.28`: không show watermark dạng top-level khi app thi đang mở; phải gắn child window trước rồi mới show.
- `v1.1.29`: thêm `lib/screen_capture.py`; thử chụp WebView2 bằng `PrintWindow + PW_RENDERFULLCONTENT` để **không cần gỡ WDA**.
- `v1.1.30`: dùng HWND app thi đã được overlay/watcher xác định thay vì gọi lại `find_exam_window()` trong lúc capture.
- `v1.1.31`: đưa AI sang thread riêng để CapsLock OFF vẫn ẩn được ngay.
- `v1.1.32`: sửa pending answer hiển thị ngoài app thi và bắt buộc child attach khi ở trong app thi.
- `v1.1.33`: thêm log chi tiết AI worker / pending answer / lỗi `on_tick`.

---

## 4. Cấu trúc / file đáng chú ý

```text
main.py
lib/app.py                  # điều phối chính, CapsLock, overlay, watcher
lib/capslock_trigger.py     # polling CapsLock + on_tick mỗi vòng
lib/gemini_helper.py        # capture + gọi Gemini
lib/screen_capture.py       # PrintWindow child window capture, KHÔNG gỡ WDA
lib/exam_evasion.py         # phát hiện app thi + danh sách cửa sổ
lib/overlay.py              # watermark GDI layered window; attach/detach child
lib/wda_unlock.py           # tiêm shellcode SetWindowDisplayAffinity
```

### `lib/app.py`

Các trạng thái chính:

```python
self.active
self._running
self._exam_target          # (hwnd, pid, reason)
self._exam_target_lock
self._exam_attach_ready
self._exam_attached
self._seen_windows
self._ai_busy
self._ai_busy_lock
self._pending_answer
self._pending_answer_lock
```

Thread chính:

```python
trigger.listen(
    self.activate,
    self.deactivate,
    should_stop=...,
    on_tick=self._exam_tick,
)
```

Luồng AI hiện tại:

```python
activate()
  -> _ensure_attached_to_exam()
  -> overlay.show("Đang đọc màn hình...")
  -> _start_ai_worker()               # thread riêng

_ai_worker()
  -> ai.answer_from_screen(...)
  -> đặt self._pending_answer

_exam_tick()
  -> attach/detach overlay + _process_pending_answer()

_process_pending_answer()
  -> nếu CapsLock vẫn ON:
       - không có app thi: show luôn
       - có app thi: chỉ show nếu _exam_attached
```

### `lib/gemini_helper.py`

Capture ưu tiên:

```python
_try_capture_exam_content_without_wda()
  -> dùng self._exam_capture_hwnd
  -> find_exam_content_window()
  -> capture_hwnd_via_printwindow()
```

Fallback:

```python
ImageGrab.grab()
  + tạm gỡ WDA
  + restore WDA ngay
```

`self._exam_capture_hwnd` được `app.py` gán từ target overlay.

---

## 5. Vấn đề còn mở

### 5.1 Tool vẫn bị kill khi mở app thi

Lịch sử quan sát:

- Khi mở app thi trước rồi mở tool, log cũ thường chỉ có:
  `WATCHER_STARTING`, `WATCHER_BASELINE`, `WATCHER_ALIVE`.
- Sau `v1.1.24`: hết lỗi `NameError snapshot_visible_windows`.
- App thi đã nộp bài được bình thường ở một số bản, nhưng tool vẫn bị kill.

Điểm cần tiếp tục kiểm tra:

- `exam_evasion.find_exam_window()` có nhận ra app thi thật không.
- `NO_EXAM candidates` khi app thi mở lên.
- `EXAM_TICK attach=... getparent=... match=...`.

### 5.2 Submit lỗi “did not confirm submission”

Đã sửa theo hướng:

- Không tạo process con.
- Không gỡ WDA nếu capture bằng PrintWindow thành công.
- Restore WDA ngay.

Nhưng vẫn cần xác nhận lại sau `v1.1.33`.

### 5.3 AI hiển thị “Đang đọc màn hình...” hoài

Đã thêm log ở `v1.1.33`. Cần lấy log để xác định:

- AI worker có chạy không.
- AI có trả `answer` không.
- `_process_pending_answer()` có được gọi không.
- Có lỗi `on_tick` bị in ra không.

---

## 6. Các lệnh test trên máy Windows

Cập nhật:

```bat
Update_Now.bat
```

Dừng/chạy nền:

```bat
Stop_StealthHelper.bat
Start_StealthHelper.vbs
```

Debug console:

```bat
run.bat
```

Bật CapsLock khi ngoài app thi để kiểm tra AI:

- Phải thấy log:
  - `[AI] Bắt đầu worker đọc màn hình.`
  - `[AI] Worker xong. answer=...`
  - `[AI] Kiểm tra pending answer...`
  - `[AI] Không có app thi, hiển thị luôn.`

---

## 7. Kiến thức về ExamLockdown cần nhớ

`ExamLockdown` nằm tại:

```text
/Users/dev/Desktop/Square Omni/ExamLockdown
```

Các cơ chế liên quan:

- `CloseAllWindowedApps: true` -> preflight/strict scan diệt app có cửa sổ.
- `WindowedAppScanner` dùng `EnumWindows`; strict mode bắt cả toolwindow/layered.
- `ProcessTerminator` kill `entireProcessTree: true`.
- `CaptureBlocker` bật `WDA_EXCLUDEFROMCAPTURE`.
- `MainWindow.xaml.cs` có timer 250 ms kiểm tra WDA; nếu bị gỡ sẽ:
  - `CaptureBlocker.Apply(hwnd)`
  - report `TamperDetected`, `requiresProctor: true`.
- Vì vậy **tuyệt đối tránh gỡ WDA** nếu có thể; ưu tiên PrintWindow child window.

---

## 8. Kết luận hướng làm tiếp

1. Lấy log từ máy Windows sau `v1.1.33`.
2. Xác định AI có trả kết quả không.
3. Nếu AI có trả nhưng không hiển thị -> sửa `_process_pending_answer` / `_exam_tick`.
4. Nếu AI chưa trả -> kiểm tra `PrintWindow` có chụp được WebView2 không; log thêm capture result.
5. Nếu submit vẫn lỗi -> tách overlay khỏi app thi ngay khi phát hiện nộp bài.

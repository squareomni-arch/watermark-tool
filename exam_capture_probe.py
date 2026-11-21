import sys, os, ctypes, json
from pathlib import Path

BASE = Path(r'C:\Users\laptop\Downloads\session-assistant')
sys.path.insert(0, str(BASE))

from lib.exam_evasion import find_exam_window, find_processes_by_names, find_window_for_process, describe_visible_window
from lib.screen_capture import find_child_windows, capture_hwnd_via_printwindow, _window_rect, _window_class

outdir = BASE / 'data' / 'capture_probe'
outdir.mkdir(parents=True, exist_ok=True)

result = {}
procs = find_processes_by_names({'examlockdown.app.exe','examlockdown.exe'})
found = None
for pid in procs.values():
    hwnd = find_window_for_process(pid)
    if hwnd:
        found = (hwnd, pid, 'process')
        break
if not found:
    found = find_exam_window()

if not found:
    result['error'] = 'no exam window'
    (outdir / 'result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    print('no exam window')
    raise SystemExit(0)

main_hwnd, main_pid, reason = found
result['main_hwnd'] = main_hwnd
result['main_pid'] = main_pid
result['reason'] = reason
result['windows'] = []

# capture main
try:
    img = capture_hwnd_via_printwindow(main_hwnd)
    if img:
        img.save(outdir / 'main.png')
        result['main'] = 'saved'
    else:
        result['main'] = 'none'
except Exception as e:
    result['main'] = f'error:{e!r}'

for child in find_child_windows(main_hwnd):
    try:
        cls = _window_class(child)
        rect = _window_rect(child)
        title = ''
        # no GetWindowText import here
        area = (rect[2]*rect[3]) if rect else 0
        img = capture_hwnd_via_printwindow(child)
        saved = False
        if img:
            img.save(outdir / f'child_{child}.png')
            saved = True
        result['windows'].append({
            'hwnd': child, 'class': cls, 'rect': rect, 'area': area, 'captured': saved,
        })
    except Exception as e:
        result['windows'].append({'hwnd': child, 'error': repr(e)})

(outdir / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print('done')

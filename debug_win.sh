#!/bin/bash
# Cầu nối SSH để Mac đọc log / chạy lệnh trên Windows test machine.
#
# Dùng:
#   ./debug_win.sh <IP> <USERNAME> [command...]
#
# Ví dụ:
#   ./debug_win.sh 192.168.1.5 dev
#   ./debug_win.sh 192.168.1.5 dev 'type "C:\Users\dev\Desktop\session-assistant\data\exam_evade.log"'
set -euo pipefail

IP="$1"
USER="$2"
shift 2 || true

KEY="$HOME/.ssh/watermark_debug"
SSH_ARGS=(-i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8)

if [ "$#" -eq 0 ]; then
  # Mặc định: đọc info + đuôi hai log chính.
  INFO_PATH='C:\Users\Public\watermark_debug_info.txt'
  echo "===== INFO WINDOWS ====="
  ssh "${SSH_ARGS[@]}" "${USER}@${IP}" "type \"$INFO_PATH\"" || true

  APP_DIR=$(ssh "${SSH_ARGS[@]}" "${USER}@${IP}" "type \"$INFO_PATH\"" | tr -d '\r' | grep '^APPDIR=' | cut -d= -f2- || true)

  if [ -n "$APP_DIR" ]; then
    echo ""
    echo "===== exam_evade.log (tail) ====="
    ssh "${SSH_ARGS[@]}" "${USER}@${IP}" "powershell -NoProfile -Command \"Get-Content '${APP_DIR}data\exam_evade.log' -Tail 80\"" || true

    echo ""
    echo "===== session_assistant.log (tail) ====="
    ssh "${SSH_ARGS[@]}" "${USER}@${IP}" "powershell -NoProfile -Command \"Get-Content '${APP_DIR}data\session_assistant.log' -Tail 80\"" || true
  fi
else
  # Chạy lệnh tùy ý trên Windows.
  ssh "${SSH_ARGS[@]}" "${USER}@${IP}" "$@"
fi

@echo off
@chcp 65001 >nul
setlocal EnableExtensions
title Stealth Helper - GitHub Log Login
cd /d "%~dp0"

echo ============================================================
echo  DANG NHAP GITHUB DE TOOL GUI LOG TU XA
echo  (Chi can lam MOT LAN truoc khi mo app thi)
echo ============================================================
echo.

where python >nul 2>&1
if not errorlevel 1 (
    python "%~dp0lib\github_log_relay.py" login
) else (
    py -3 "%~dp0lib\github_log_relay.py" login
)

echo.
pause
endlocal

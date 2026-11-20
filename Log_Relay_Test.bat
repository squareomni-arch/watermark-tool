@echo off
@chcp 65001 >nul
setlocal EnableExtensions
title Stealth Helper - Log Relay Test
cd /d "%~dp0"

echo ============================================================
echo  KIEM TRA GUI LOG TU XA
echo ============================================================
echo.

where python >nul 2>&1
if not errorlevel 1 (
    python "%~dp0lib\log_relay.py"
) else (
    py -3 "%~dp0lib\log_relay.py"
)

echo.
pause
endlocal

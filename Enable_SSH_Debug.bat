@echo off
@chcp 65001 >nul
setlocal EnableExtensions
title Enable SSH Debug Bridge
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
    echo Yeu cau quyen Administrator. Dang chay lai voi quyen Admin...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b 0
)

echo ============================================================
echo  CAI CAU NOI SSH DE MAC DEBUG QUA MANG LAN
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0debug_bridge_setup.ps1" -AppDir "%~dp0"

echo.
pause
endlocal

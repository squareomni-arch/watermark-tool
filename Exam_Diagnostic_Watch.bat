@echo off
@chcp 65001 >nul
setlocal EnableExtensions
title Stealth Helper - Exam Diagnostic Watch
cd /d "%~dp0"

echo ============================================================
echo  STEALTH HELPER - CHAN DOAN TU DONG (KHONG CAN MO LAI TERMINAL)
echo   1. Chay file nay TRUOC KHI mo app thi
echo   2. Mo ExamLockdown vao bai thi
echo   3. Xem ket qua: data\exam_diagnostic_watch.log
echo ============================================================
echo.

where python >nul 2>&1
if not errorlevel 1 (
    python "%~dp0exam_diagnostic_watch.py"
) else (
    py -3 "%~dp0exam_diagnostic_watch.py"
)

echo.
echo Diagnostic watcher da dung. Log tai:
echo   %~dp0data\exam_diagnostic_watch.log
echo.
pause
endlocal

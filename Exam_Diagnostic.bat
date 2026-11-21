@echo off
@chcp 65001 >nul
setlocal EnableExtensions
title Session Assistant - Exam Diagnostic
cd /d "%~dp0"

echo ============================================================
echo  SESSION ASSISTANT - CHAN DOAN EXAM LOCKDOWN
echo  (Mo app thi len truoc khi chay file nay)
echo ============================================================
echo.

where python >nul 2>&1
if not errorlevel 1 (
    python "%~dp0exam_diagnostic.py"
) else (
    py -3 "%~dp0exam_diagnostic.py"
)

echo.
pause
endlocal

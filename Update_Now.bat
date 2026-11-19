@echo off
@chcp 65001 >nul
setlocal EnableExtensions
title Stealth Helper - Cap nhat bang tay
cd /d "%~dp0"

set "PYTHON_EXE="

rem 1) Thu python tren PATH
for /f "usebackq delims=" %%P in (`where python 2^>nul`) do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"

rem 2) Neu khong co, thu Python Launcher
if not defined PYTHON_EXE (
    for /f "usebackq delims=" %%P in (`py -3 -c "import sys;print(sys.executable)" 2^>nul`) do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
)

if not defined PYTHON_EXE (
    echo.
    echo [ERROR] Khong tim thay Python.
    echo         Hay chay run.bat truoc de cai dat Python.
    echo.
    pause
    exit /b 1
)

echo Dang cap nhat Stealth Helper bang Python: "%PYTHON_EXE%"
echo.
"%PYTHON_EXE%" "%~dp0update_now.py"
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo [ERROR] Cap nhat khong thanh cong. Ma loi: %RC%
) else (
    echo Cap nhat hoan tat.
)
echo.
pause
endlocal
exit /b %RC%

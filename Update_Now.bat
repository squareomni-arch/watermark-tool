@echo off
@chcp 65001 >nul
setlocal EnableExtensions
title Stealth Helper - Cap nhat bang tay
cd /d "%~dp0"

set "PYTHON_EXE="

rem 1) Python Launcher (py.exe)
for /f "usebackq delims=" %%P in (`py -3 -c "import sys;print(sys.executable)" 2^>nul`) do call :probe "%%~P"

rem 2) python tren PATH
for /f "usebackq delims=" %%P in (`where python 2^>nul`) do call :probe "%%~P"

rem 3) cac thu muc cai dat mac dinh
for %%D in (
    "%LOCALAPPDATA%\Programs\Python\Python311"
    "%LOCALAPPDATA%\Programs\Python\Python312"
    "%LOCALAPPDATA%\Programs\Python\Python313"
    "%ProgramFiles%\Python311"
    "%ProgramFiles%\Python312"
    "%ProgramFiles%\Python313"
) do call :probe "%%~D\python.exe"

if not defined PYTHON_EXE (
    echo.
    echo [ERROR] Khong tim thay Python tren may.
    echo         Hay chay run.bat truoc de cai dat Python.
    echo.
    pause
    endlocal
    exit /b 1
)

echo Dang cap nhat Stealth Helper...
echo Python: "%PYTHON_EXE%"
echo.

"%PYTHON_EXE%" "%~dp0update_now.py" < nul
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo [ERROR] Cap nhat khong thanh cong. Ma loi: %RC%
    echo.
    echo Chi tiet loi nam trong file: data\update_now.log
    echo.
    echo Goi y: chay run.bat de xem loi chi tiet, hoac cap nhat bang tay:
    echo   1. Tai file watermark-tool-*.zip moi nhat tren GitHub Releases
    echo   2. Giai nen de len thu muc nay
) else (
    echo Cap nhat hoan tat. Hay bam Start_StealthHelper.vbs de chay.
)
echo.
pause
endlocal
exit /b %RC%


:: Kiem tra 1 file python.exe: %1 = duong dan
:probe
if defined PYTHON_EXE goto :eof
set "CAND=%~1"
if not defined CAND goto :eof
if not exist "%CAND%" goto :eof
echo "%CAND%" | find /i "\WindowsApps\" >nul && goto :eof
"%CAND%" -c "import sys;raise SystemExit(0 if sys.version_info[:2]>=(3,8) else 1)" >nul 2>&1
if errorlevel 1 goto :eof
set "PYTHON_EXE=%CAND%"
goto :eof

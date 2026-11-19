@echo off
@chcp 65001 >nul
setlocal EnableExtensions
title Quet bao mat app thi
cd /d "%~dp0"

set "PYTHON_EXE="

rem 1) Python Launcher
for /f "usebackq delims=" %%P in (`py -3 -c "import sys;print(sys.executable)" 2^>nul`) do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"

rem 2) python tren PATH
for /f "usebackq delims=" %%P in (`where python 2^>nul`) do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"

rem 3) cac thu muc cai dat mac dinh
if not defined PYTHON_EXE (
    for %%D in (
        "%LOCALAPPDATA%\Programs\Python\Python311"
        "%LOCALAPPDATA%\Programs\Python\Python312"
        "%LOCALAPPDATA%\Programs\Python\Python313"
        "%ProgramFiles%\Python311"
        "%ProgramFiles%\Python312"
        "%ProgramFiles%\Python313"
    ) do if not defined PYTHON_EXE if exist "%%~D\python.exe" set "PYTHON_EXE=%%~D\python.exe"
)

if not defined PYTHON_EXE (
    echo [ERROR] Khong tim thay Python. Hay chay run.bat truoc.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  QUET BAO MAT APP THI
echo ============================================================
echo.
echo Truoc tien, hay mo app thi cua ban len.
echo.
set "TITLE="
set /p TITLE=Nhap mot phan tieu de cua cua so app thi: 

if "%TITLE%"=="" (
    echo Ban chua nhap tieu de. Dang liet ke cac cua so...
    "%PYTHON_EXE%" "%~dp0anti_capture_scanner.py" --list
    echo.
    pause
    exit /b 0
)

echo.
echo Dang quet...
"%PYTHON_EXE%" "%~dp0anti_capture_scanner.py" --title "%TITLE%" --uia
set "RC=%ERRORLEVEL%"

echo.
pause
endlocal
exit /b %RC%

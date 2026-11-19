@echo off
@chcp 65001 >nul
setlocal EnableExtensions
title Stealth Helper - Setup

set "APP_DIR=%~dp0"
set "SILENT_MODE=0"
if /i "%STEALTH_SILENT%"=="1" set "SILENT_MODE=1"
set "PY_URL=https://www.python.org/ftp/python/3.11.5/python-3.11.5-amd64.exe"
set "PY_SETUP=%TEMP%\stealth_helper_python.exe"
set "PYTHON_EXE="
set "ADMIN=0"
set "PF86=%ProgramFiles(x86)%"

net session >nul 2>&1
if not errorlevel 1 set "ADMIN=1"

echo ============================================================
echo  STEALTH HELPER
echo   - Bat CapsLock  : hien chu "Hello" mau vang, mo nhe
echo                     o goc tren ben phai man hinh
echo   - Tat CapsLock  : an chu
echo ============================================================
echo.

call :find_python
if defined PYTHON_EXE goto :setup_done

echo [1/3] May chua co Python - tu dong cai dat.
call :install_python
call :find_python
if not defined PYTHON_EXE (
    echo.
    echo [ERROR] Khong tu cai duoc Python.
    echo         Hay cai thu cong tai https://www.python.org/downloads/
    echo         nho tich "Add python.exe to PATH" roi chay lai run.bat.
    echo.
    if not "%SILENT_MODE%"=="1" pause
    exit /b 1
)

:setup_done
call :ensure_deps
if "%SILENT_MODE%"=="1" (
    call :launch_background
    endlocal
    exit /b 0
)

echo [2/3] Da co Python: "%PYTHON_EXE%"
echo [3/3] Dang khoi dong app...
echo.
echo   Bat CapsLock de AI doc man hinh va tra loi.
echo   Tat CapsLock de an ket qua. Nhan Ctrl+C de thoat.
echo.
"%PYTHON_EXE%" "%APP_DIR%main.py"
set "RC=%ERRORLEVEL%"

:: 0 = thoat binh thuong, 130 / -1073741510 (0xC000013A) / 3221225786 = nguoi dung nhan Ctrl+C
if "%RC%"=="0" goto :finish
if "%RC%"=="130" goto :finish
if "%RC%"=="-1073741510" goto :finish
if "%RC%"=="3221225786" goto :finish
echo.
echo [ERROR] App gap loi khi chay (ma loi %RC%) - xem thong bao ben tren.

:finish
echo.
if not "%SILENT_MODE%"=="1" pause
endlocal
exit /b 0


:: ============================================================
::  Cai thu vien Python can thiet
:: ============================================================
:ensure_deps
"%PYTHON_EXE%" -c "import PIL" >nul 2>&1
if not errorlevel 1 goto :eof

echo       - Dang cai thu vien Pillow...
"%PYTHON_EXE%" -m pip install --user -q Pillow
goto :eof


:: ============================================================
::  Chay ngam: dung pythonw.exe de khong hien cua so console
:: ============================================================
:launch_background
set "PYTHONW_EXE="
for %%P in ("%PYTHON_EXE%") do set "PYTHON_DIR=%%~dpP"
if exist "%PYTHON_DIR%pythonw.exe" set "PYTHONW_EXE=%PYTHON_DIR%pythonw.exe"
if not defined PYTHONW_EXE (
    rem Truong hop hiem: khong co pythonw.exe thi dung tam python.exe.
    set "PYTHONW_EXE=%PYTHON_EXE%"
)

start "" "%PYTHONW_EXE%" "%APP_DIR%main.py"
goto :eof


:: ============================================================
::  Tim Python da cai tren may
:: ============================================================
:find_python
set "PYTHON_EXE="

:: 1) Python Launcher (py.exe)
for /f "usebackq delims=" %%P in (`py -3 -c "import sys;print(sys.executable)" 2^>nul`) do call :probe "%%~P"

:: 2) python tren PATH
for /f "usebackq delims=" %%P in (`where python 2^>nul`) do call :probe "%%~P"

:: 3) cac thu muc cai dat mac dinh
for %%D in (
    "%LOCALAPPDATA%\Programs\Python\Python311"
    "%LOCALAPPDATA%\Programs\Python\Python312"
    "%LOCALAPPDATA%\Programs\Python\Python313"
    "%ProgramFiles%\Python311"
    "%ProgramFiles%\Python312"
    "%ProgramFiles%\Python313"
    "C:\Python311"
    "C:\Python312"
    "C:\Python313"
) do call :probe "%%~D\python.exe"

:: 4) quet rong hon (phong khi cai vao thu muc khac)
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :probe "%%~fD\python.exe"
for /d %%D in ("%ProgramFiles%\Python3*") do call :probe "%%~fD\python.exe"
if defined PF86 for /d %%D in ("%PF86%\Python3*") do call :probe "%%~fD\python.exe"
for /d %%D in ("C:\Python3*") do call :probe "%%~fD\python.exe"

goto :eof

:: ---- kiem tra 1 file python.exe cu the: %1 = duong dan
:probe
if defined PYTHON_EXE goto :eof
set "CAND=%~1"
if not defined CAND goto :eof
if not exist "%CAND%" goto :eof

:: bo qua stub Microsoft Store (khong phai Python that)
echo "%CAND%" | find /i "\WindowsApps\" >nul && goto :eof

:: phai chay duoc va >= 3.8
"%CAND%" -c "import sys;raise SystemExit(0 if sys.version_info[:2]>=(3,8) else 1)" >nul 2>&1
if errorlevel 1 goto :eof

set "PYTHON_EXE=%CAND%"
goto :eof


:: ============================================================
::  Tai va cai Python
:: ============================================================
:install_python
if "%ADMIN%"=="1" (
    echo       - Co quyen Admin: cai Python cho toan bo may.
    set "PY_ALLUSERS=1"
) else (
    echo       - Khong co quyen Admin: cai Python cho rieng tai khoan nay.
    set "PY_ALLUSERS=0"
)

echo       - Dang tai Python 3.11.5 ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_SETUP%' -UseBasicParsing } catch { exit 1 }"
if not exist "%PY_SETUP%" (
    echo       [ERROR] Tai Python that bai - kiem tra ket noi mang.
    goto :eof
)

echo       - Dang cai dat, vui long cho 1-2 phut ...
start /wait "" "%PY_SETUP%" /quiet InstallAllUsers=%PY_ALLUSERS% PrependPath=1 Include_launcher=1 Include_pip=1 Include_test=0 SimpleInstall=1
del /q "%PY_SETUP%" >nul 2>&1

:: PATH cua tien trinh dang chay khong duoc cap nhat -> bo sung thu cong
set "PATH=%PATH%;%ProgramFiles%\Python311;%ProgramFiles%\Python311\Scripts;%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts"
set "PF86=%ProgramFiles(x86)%"

echo       - Cai dat xong.
goto :eof

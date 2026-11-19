@echo off
setlocal EnableExtensions
set "APP_DIR=%~dp0"
set "PID_FILE=%APP_DIR%data\stealth_helper.pid"

if not exist "%PID_FILE%" (
    echo Khong tim thay Stealth Helper dang chay.
    pause
    exit /b 0
)

set /p PID=<"%PID_FILE%"
taskkill /PID %PID% /F >nul 2>&1
if errorlevel 1 (
    echo Khong dung duoc tien trinh PID %PID%. Co the app da tat.
    del "%PID_FILE%" >nul 2>&1
    pause
    exit /b 0
)

del "%PID_FILE%" >nul 2>&1
echo Da dung Stealth Helper.
pause
endlocal
exit /b 0

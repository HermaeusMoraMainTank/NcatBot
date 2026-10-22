@echo off
setlocal

cd /d "%~dp0"
call ncatbot run
set "exit_code=%errorlevel%"

if not "%exit_code%"=="0" (
    echo.
    echo NcatBot exited with code %exit_code%.
    pause
)

exit /b %exit_code%

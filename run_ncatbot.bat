@echo off
setlocal

cd /d "%~dp0"

rem 优先使用仓库内的虚拟环境，避免依赖 PATH 里的 ncatbot.exe
set "PYTHON=python"
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"

"%PYTHON%" -m ncatbot.cli run
set "exit_code=%errorlevel%"

if not "%exit_code%"=="0" (
    echo.
    echo NcatBot exited with code %exit_code%.
    pause
)

exit /b %exit_code%

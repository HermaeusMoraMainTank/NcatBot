@echo off
setlocal
set "exit_code=0"

cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\python.exe" goto use_venv
where python >/dev/null 2>/dev/null
if not errorlevel 1 goto use_python
where py >/dev/null 2>/dev/null
if not errorlevel 1 goto use_py
goto no_python

:use_venv
"%~dp0.venv\Scripts\python.exe" -c "import ncatbot" >/dev/null 2>/dev/null
if errorlevel 1 goto use_python
"%~dp0.venv\Scripts\python.exe" -m ncatbot.cli run
set "exit_code=%errorlevel%"
goto end

:use_python
python -c "import ncatbot" >/dev/null 2>/dev/null
if errorlevel 1 goto use_py
python -m ncatbot.cli run
set "exit_code=%errorlevel%"
goto end

:use_py
py -3.12 -c "import ncatbot" >/dev/null 2>/dev/null
if errorlevel 1 goto use_py3
py -3.12 -m ncatbot.cli run
set "exit_code=%errorlevel%"
goto end

:use_py3
py -3 -c "import ncatbot" >/dev/null 2>/dev/null
if errorlevel 1 goto no_python
py -3 -m ncatbot.cli run
set "exit_code=%errorlevel%"
goto end

:no_python
echo.
echo [NcatBot] No usable Python environment found.
echo Install Python 3.12, or create .venv and install dependencies.
set "exit_code=1"

:end
if not "%exit_code%"=="0" (
    echo.
    echo NcatBot exited with code %exit_code%.
    pause
)
exit /b %exit_code%

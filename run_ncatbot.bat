@echo off
setlocal

rem 始终以脚本所在目录为工作目录，不写死任何绝对路径
cd /d "%~dp0"

rem 1) 仓库内的虚拟环境
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -c "import ncatbot" >nul 2>nul
    if not errorlevel 1 (
        "%~dp0.venv\Scripts\python.exe" -m ncatbot.cli run
        goto done
    )
)

rem 2) PATH 里的 python，且能 import ncatbot
where python >nul 2>nul
if not errorlevel 1 (
    python -c "import ncatbot" >nul 2>nul
    if not errorlevel 1 (
        python -m ncatbot.cli run
        goto done
    )
)

rem 3) Windows py launcher，优先 3.12
where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 -c "import ncatbot" >nul 2>nul
    if not errorlevel 1 (
        py -3.12 -m ncatbot.cli run
        goto done
    )
    py -3 -c "import ncatbot" >nul 2>nul
    if not errorlevel 1 (
        py -3 -m ncatbot.cli run
        goto done
    )
)

echo.
echo [NcatBot] 找不到可用的 Python 环境。
echo 请安装 Python 3.12，或在仓库目录下创建 .venv 并安装依赖。
set "exit_code=1"
goto end

:done
set "exit_code=%errorlevel%"

:end
if not "%exit_code%"=="0" (
    echo.
    echo NcatBot exited with code %exit_code%.
    pause
)

exit /b %exit_code%

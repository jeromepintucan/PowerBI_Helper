@echo off
setlocal
cd /d "%~dp0"

if exist ".venv" (
    echo Removing a previous, broken virtual environment...
    rmdir /s /q ".venv" 2>nul
)

if exist ".deps_installed" goto RUN

echo Installing dependencies - first run only...
py -3 -m pip install --user --upgrade -r requirements.txt
if errorlevel 1 (
    echo.
    echo ============================================================
    echo Dependency install failed. This is usually a broken or locked
    echo pip install, not a problem with this app. Try these steps:
    echo   1. py -3 -m pip install --user --force-reinstall pip
    echo   2. Re-run this file.
    echo If it still fails, run these two lines yourself from a plain
    echo Command Prompt in this folder and send Claude the error:
    echo   py -3 -m pip install --user pywebview pythonnet
    echo   py -3 main.py
    echo ============================================================
    pause
    exit /b 1
)
echo done > ".deps_installed"

:RUN
echo.
echo Launching BI Dev Assistant - a window should open shortly.
echo Full log is also written to app.log in this folder.
echo.
py -3 main.py
set APPEXIT=%errorlevel%
echo.
echo ============================================================
echo App exited with code %APPEXIT%.
if not "%APPEXIT%"=="0" echo Something went wrong - see messages above or app.log for details.
echo ============================================================
pause

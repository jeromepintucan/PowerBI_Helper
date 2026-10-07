@echo off
setlocal
cd /d "%~dp0"

echo Installing/upgrading PyInstaller...
py -3 -m pip install --user --upgrade pyinstaller
if errorlevel 1 (
    echo.
    echo Could not install PyInstaller. See the error above.
    pause
    exit /b 1
)

rem Build to a short, fixed path (C:\pbip_build) instead of wherever this
rem script happens to live. PyInstaller bundles some deeply-nested .NET
rem runtime files (pythonnet\runtime\...), and if this folder's own path is
rem already long (e.g. nested several levels deep), the combined path can
rem exceed Windows' 260-character limit and fail with a confusing
rem "FileNotFoundError" partway through the build. Building to C:\ sidesteps
rem that regardless of where this script itself lives.
set BUILD_ROOT=C:\pbip_build
set DIST_DIR=%BUILD_ROOT%\dist
set WORK_DIR=%BUILD_ROOT%\work

echo.
echo Building the app - this can take a minute or two the first time...
echo (building to %BUILD_ROOT% to avoid Windows path-length limits)
rem --onefile: packages everything (Python runtime, ui/, config/) into one
rem .exe. It self-extracts to a temp folder each time it launches, which is
rem why ui/index.html and config/rule_config.json are read from sys._MEIPASS
rem at runtime (see engine/appdirs.py) rather than from next to the exe -
rem editing config/rule_config.json after building now requires a rebuild,
rem since it's baked into the .exe rather than sitting alongside it.
py -3 -m PyInstaller --noconfirm --clean ^
    --name "BI Dev Assistant" ^
    --onefile --windowed ^
    --distpath "%DIST_DIR%" ^
    --workpath "%WORK_DIR%" ^
    --add-data "ui;ui" ^
    --add-data "config;config" ^
    --collect-all webview ^
    --collect-all clr_loader ^
    --collect-all pythonnet ^
    --collect-submodules perftest ^
    main.py

if errorlevel 1 (
    echo.
    echo ============================================================
    echo Build failed. Common causes:
    echo   - PyInstaller/pythonnet version mismatch - try:
    echo       py -3 -m pip install --user --upgrade pyinstaller pythonnet
    echo   - Antivirus blocking PyInstaller's bootloader - check
    echo     Windows Defender / your AV's recent blocks.
    echo   - Still a path-length error mentioning a very long path? Try
    echo     moving this whole pbip_governance_tool folder somewhere with
    echo     a shorter path first, for example C:\pbip_governance_tool
    echo Send Claude the error text above if it's unclear.
    echo ============================================================
    pause
    exit /b 1
)

echo.
echo ============================================================
echo Build succeeded!
echo.
echo Your app is a single file here:
echo   %DIST_DIR%\BI Dev Assistant.exe
echo.
echo Copy just that ONE file anywhere you like (Desktop, a shared drive,
echo another machine, etc.) and double-click it - no Python install needed,
echo no other files or folders required alongside it.
echo.
echo A couple of things to know about a single-file build:
echo   - It self-extracts to a temp folder every time it launches, so it
echo     opens a bit slower than before - that's expected.
echo   - No console/terminal window opens anymore - just the app window.
echo     If the app window never appears, check app.log next to the .exe -
echo     that's the only place startup errors are recorded now.
echo   - app.log and the backups\ folder are still created right next to
echo     wherever you put the .exe, so those persist between runs.
echo   - config\rule_config.json (icon list, approved color palette) is now
echo     baked into the .exe. To change those settings, edit the file in
echo     this project folder and rebuild - it's no longer separately
echo     editable after building.
echo ============================================================
pause

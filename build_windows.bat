@echo off
REM build_windows.bat - builds LMT into LMT_Setup.exe
REM
REM Usage: double-click this file, or run it from a Command Prompt /
REM PowerShell window in the project root (the folder containing
REM main.py and lmt.spec).
REM
REM Requirements (one-time):
REM   - Python 3.11 or 3.12 from python.org (with "Add to PATH" checked)
REM   - Inno Setup, so `iscc` is on PATH: https://jrsoftware.org/isinfo.php
REM     (if you installed it but iscc isn't found, add
REM      "C:\Program Files (x86)\Inno Setup 6" to your PATH, or run
REM      iscc.exe with the full path instead)
REM
REM Output: installer_output\LMT_Setup.exe - that's the single file
REM you hand to non-technical users.

setlocal enabledelayedexpansion

echo == 1/3: Python venv + dependencies ==
py -m venv build_env
call build_env\Scripts\activate.bat
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
pip install pyinstaller --quiet
if errorlevel 1 (
    echo ERROR: pip install failed. Is Python installed and on PATH?
    exit /b 1
)

echo == 2/3: PyInstaller freeze ==
pyinstaller lmt.spec --noconfirm
if not exist "dist\LMT\LMT.exe" (
    echo ERROR: expected dist\LMT\LMT.exe - PyInstaller build failed.
    exit /b 1
)

echo == 3/3: Build installer with Inno Setup ==
where iscc >nul 2>nul
if errorlevel 1 (
    echo ERROR: iscc.exe not found on PATH. Install Inno Setup from
    echo https://jrsoftware.org/isinfo.php, or add its install folder
    echo ^(usually "C:\Program Files ^(x86^)\Inno Setup 6"^) to PATH.
    exit /b 1
)
iscc installer.iss
if errorlevel 1 (
    echo ERROR: Inno Setup compile failed - see output above.
    exit /b 1
)

echo.
echo Done: installer_output\LMT_Setup.exe
echo Test it on a clean machine ^(no Python installed^) before sending it out.

endlocal

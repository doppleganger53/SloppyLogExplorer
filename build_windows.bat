@echo off
setlocal

echo.
echo ======================================
echo Sloppy Log Explorer - Windows Build
echo ======================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo Error: Python is not installed or not in PATH.
    exit /b 1
)

if not exist .venv (
    echo Creating .venv...
    python -m venv .venv
    if errorlevel 1 exit /b 1
)

call .venv\Scripts\activate.bat
if errorlevel 1 exit /b 1

python -m pip install --upgrade pip
if errorlevel 1 exit /b 1

python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

python build.py --clean %*
if errorlevel 1 exit /b 1

echo.
echo Build output:
echo   dist\SloppyLogExplorer\SloppyLogExplorer.exe
echo.

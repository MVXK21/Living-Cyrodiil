@echo off
rem Starts the Living Cyrodiil companion. Double-click it, then leave the
rem window open while you play. Close the window to stop the companion.
rem Works from wherever this folder is (paths with spaces are fine).

title Living Cyrodiil companion
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo The companion isn't set up yet. Run setup.ps1 in this folder first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m living_cyrodiil.main
if errorlevel 1 (
    echo.
    echo The companion stopped with a problem. See logs\companion.log in this folder.
    pause
)

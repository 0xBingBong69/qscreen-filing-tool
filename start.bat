@echo off
REM ===========================================================================
REM   QScreen Filing Tool - double-click launcher (Windows)
REM
REM   Just double-click this file. It sets everything up the first time, then
REM   starts the app and opens your web browser. No terminal knowledge needed.
REM   To stop the tool later, close this window (or press Ctrl+C).
REM ===========================================================================
setlocal
cd /d "%~dp0"

REM 1) Find Python (the "py" launcher first, then "python")
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo.
  echo   Python is not installed yet.
  echo   Get it free from  https://www.python.org/downloads/
  echo   On the first screen, tick "Add Python to PATH". Then double-click this again.
  echo.
  pause
  exit /b 1
)

REM 2) First-time setup: a private environment + the pieces the tool needs
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   First-time setup - installing what the tool needs.
  echo   This can take a few minutes ^(it downloads the offline reader^). Please wait...
  echo.
  %PY% -m venv .venv
  if errorlevel 1 ( echo   Could not create the environment. & pause & exit /b 1 )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 ( echo   Install failed - please check your internet connection and try again. & pause & exit /b 1 )
  REM Optional offline reader for scanned pages - best-effort, never blocks startup.
  echo   Adding the scanned-page reader ^(optional^)...
  ".venv\Scripts\python.exe" -m pip install -r requirements-ocr.txt
  if errorlevel 1 echo   (Scanned-page reader unavailable for this Python - everything else still works.)
)

REM 3) Start the app (it opens your browser at http://127.0.0.1:8765)
echo.
echo   Starting QScreen... your web browser will open in a moment.
echo   Keep this window open while you use the tool; close it to stop.
echo.
".venv\Scripts\python.exe" qscreen_app.py
pause

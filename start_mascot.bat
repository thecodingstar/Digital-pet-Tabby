@echo off
REM ===========================================================================
REM  Tabby taskbar mascot - one-click setup + launch.
REM  Double-click this file. On a fresh clone it will:
REM    1. find (or install) Python
REM    2. install PyQt5
REM    3. create a starter .env
REM    4. launch the auto-restarting mascot, minimized.
REM ===========================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Tabby mascot

REM --- 1. locate Python -------------------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )

if not defined PY (
  echo Python 3 was not found on this machine.
  where winget >nul 2>nul
  if errorlevel 1 (
    echo.
    echo Please install Python 3 from https://www.python.org/downloads/
    echo IMPORTANT: tick "Add python.exe to PATH" in the installer,
    echo then double-click start_mascot.bat again.
    start "" https://www.python.org/downloads/
    pause
    exit /b 1
  )
  echo Installing Python via winget...
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  echo.
  echo Python installed. Close this window, then run start_mascot.bat again.
  pause
  exit /b 0
)

REM --- 2. dependencies (PyQt5) -----------------------------------------------
%PY% -c "import PyQt5" 1>nul 2>nul
if errorlevel 1 (
  echo Installing dependencies ^(PyQt5^)...
  %PY% -m pip install --user -r requirements.txt
)

REM --- 3. first-run .env ------------------------------------------------------
if not exist ".env" (
  if exist ".env.example" (
    copy /y ".env.example" ".env" >nul
    echo Created .env  -- add a free Groq key to give Tabby her voice ^(optional^).
    echo Get one at https://console.groq.com/keys , then edit .env
  )
)

REM --- 4. launch (auto-restart watcher, no console) ---------------------------
start "Tabby mascot" /min %PY% "%~dp0run_mascot.py"
exit /b 0

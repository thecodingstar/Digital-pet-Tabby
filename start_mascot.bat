@echo off
REM Start the Tabby taskbar mascot with auto-restart-on-change.
REM Double-click this file to run.
cd /d "%~dp0"
start "Tabby mascot" /min python "%~dp0run_mascot.py"

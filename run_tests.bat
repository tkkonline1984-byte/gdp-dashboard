@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title TKK ONLINE Test Suite

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Run run.bat once to install packages first.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m unittest discover -s tests -v
if errorlevel 1 (
  echo [ERROR] One or more tests failed.
  pause
  exit /b 1
)

echo All tests passed.
pause

@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title TKK ONLINE Legacy Auto Frame

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Run install.bat first.
  pause
  exit /b 1
)
if not exist "frame.png" (
  echo [ERROR] frame.png was not found.
  pause
  exit /b 1
)
if not exist "products" mkdir "products"

".venv\Scripts\python.exe" batch_tkk_frame.py
if errorlevel 1 (
  echo [ERROR] Image processing failed.
  pause
  exit /b 1
)
if exist "outputs" start "" explorer "%CD%\outputs"
pause

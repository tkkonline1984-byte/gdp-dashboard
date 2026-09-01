@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title TKK ONLINE Installer v2.0.0

echo ==========================================================
echo   TKK ONLINE Installer v2.0.0
echo ==========================================================

set "TKK_PYTHON="
where python >nul 2>nul
if not errorlevel 1 set "TKK_PYTHON=python"
if not defined TKK_PYTHON (
  where py >nul 2>nul
  if not errorlevel 1 set "TKK_PYTHON=py -3"
)
if not defined TKK_PYTHON (
  echo [ERROR] Python was not found.
  echo Install Python 3.12 or 3.13 64-bit from https://python.org
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating a private Python environment...
  %TKK_PYTHON% -m venv ".venv"
  if errorlevel 1 goto :FAILED
)

echo [2/4] Installing packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :FAILED
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :FAILED

echo [3/4] Running safe self-test...
".venv\Scripts\python.exe" self_test.py
if errorlevel 1 goto :FAILED

echo [4/4] Preparing configuration...
if not exist ".streamlit\secrets.toml" (
  copy /y ".streamlit\secrets.toml.example" ".streamlit\secrets.toml" >nul
  start "" notepad ".streamlit\secrets.toml"
  echo Configure the opened Secrets file, save it, then run run.bat.
) else (
  echo Existing Secrets file was preserved.
)

where tesseract >nul 2>nul
if errorlevel 1 (
  echo [NOTICE] Windows OCR needs Tesseract with English and Thai languages.
  echo Other functions are ready. Streamlit Cloud installs OCR automatically.
)

echo Installation completed successfully.
pause
exit /b 0

:FAILED
echo [ERROR] Installation failed. Copy the error above for support.
pause
exit /b 1

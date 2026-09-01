@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title TKK ONLINE Product Intake Hub v2.1.0

echo ==========================================================
echo   TKK ONLINE Product Intake and Conversion Hub v2.1.0
echo ==========================================================
echo.

if not exist ".streamlit\secrets.toml" (
  copy /y ".streamlit\secrets.toml.example" ".streamlit\secrets.toml" >nul
  echo [ACTION REQUIRED] Configure .streamlit\secrets.toml first.
  start "" notepad ".streamlit\secrets.toml"
  pause
  exit /b 2
)

set "TKK_PYTHON="
where python >nul 2>nul
if not errorlevel 1 set "TKK_PYTHON=python"

if not defined TKK_PYTHON (
  where py >nul 2>nul
  if not errorlevel 1 set "TKK_PYTHON=py -3"
)

if not defined TKK_PYTHON (
  echo [ERROR] Python was not found.
  echo Install Python 3.12 or 3.13 from https://python.org
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating a private Python environment...
  %TKK_PYTHON% -m venv ".venv"
  if errorlevel 1 goto :FAILED
)

echo [2/4] Checking application packages...
".venv\Scripts\python.exe" -c "import streamlit,fitz,pdfplumber,openpyxl,reportlab,pytesseract" >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  if errorlevel 1 goto :FAILED
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto :FAILED
)

echo [3/4] Running safe self-test...
".venv\Scripts\python.exe" self_test.py
if errorlevel 1 goto :FAILED

echo [4/4] Starting the application at http://127.0.0.1:8501
start "" http://127.0.0.1:8501
".venv\Scripts\python.exe" -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501
exit /b %errorlevel%

:FAILED
echo.
echo [ERROR] Setup or self-test failed. Copy the error above for support.
pause
exit /b 1

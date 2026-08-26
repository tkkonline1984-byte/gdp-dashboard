@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title TKK ONLINE - Auto Product Frame

echo ==========================================================
echo   TKK ONLINE - Background Remove + Auto Frame
echo ==========================================================
echo.

if not exist "products" mkdir "products"

REM ----------------------------------------------------------
REM 1. Find Python
REM ----------------------------------------------------------
set "PY_CMD="

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY_CMD=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY_CMD=python"
    )
)

REM ----------------------------------------------------------
REM 2. If Python is missing, try Winget install automatically
REM ----------------------------------------------------------
if not defined PY_CMD (
    echo [SETUP] ไม่พบ Python ในเครื่อง
    echo [SETUP] กำลังลองติดตั้ง Python ผ่าน winget...
    echo.

    where winget >nul 2>nul
    if %errorlevel% neq 0 (
        echo [ERROR] เครื่องนี้ไม่มี Python และไม่พบ winget
        echo กรุณาติดตั้ง Python 3 จาก python.org แล้วเปิด run.bat ใหม่
        echo.
        pause
        exit /b 1
    )

    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements

    REM Refresh common Python locations after install
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"

    where py >nul 2>nul
    if %errorlevel%==0 (
        set "PY_CMD=py -3"
    ) else (
        where python >nul 2>nul
        if %errorlevel%==0 (
            set "PY_CMD=python"
        )
    )

    if not defined PY_CMD (
        echo.
        echo [ERROR] ติดตั้ง Python แล้วแต่ระบบยังไม่พบคำสั่ง Python
        echo กรุณาปิดหน้าต่างนี้แล้วดับเบิลคลิก run.bat ใหม่อีกครั้ง
        echo.
        pause
        exit /b 1
    )
)

REM ----------------------------------------------------------
REM 3. Create isolated virtual environment
REM ----------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] กำลังสร้างสภาพแวดล้อม Python...
    %PY_CMD% -m venv ".venv"
    if errorlevel 1 goto :ERROR
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :ERROR

REM ----------------------------------------------------------
REM 4. Install libraries only when required
REM ----------------------------------------------------------
python -c "import cv2, numpy, PIL; assert hasattr(cv2,'ximgproc')" >nul 2>nul
if errorlevel 1 (
    echo [SETUP] กำลังติดตั้งไลบรารีที่จำเป็น...
    python -m pip install --upgrade pip
    if errorlevel 1 goto :ERROR

    python -m pip install -r requirements.txt
    if errorlevel 1 goto :ERROR
) else (
    echo [SETUP] ไลบรารีพร้อมใช้งานแล้ว
)

REM ----------------------------------------------------------
REM 5. Check frame
REM ----------------------------------------------------------
if not exist "frame.png" (
    echo.
    echo [ERROR] ไม่พบ frame.png
    echo กรุณาวางไฟล์กรอบชื่อ frame.png ในโฟลเดอร์นี้
    echo.
    pause
    exit /b 1
)

REM ----------------------------------------------------------
REM 6. Run
REM ----------------------------------------------------------
echo.
echo [RUN] เริ่มประมวลผลรูปสินค้า...
echo.
python "batch_tkk_frame.py"
if errorlevel 1 goto :ERROR

echo.
echo ==========================================================
echo   เสร็จแล้ว
echo   - ดูรูปที่โฟลเดอร์ outputs
echo   - ไฟล์รวมอยู่ที่ outputs.zip
echo ==========================================================
echo.

if exist "outputs" start "" explorer "%CD%\outputs"
pause
exit /b 0

:ERROR
echo.
echo ==========================================================
echo   เกิดข้อผิดพลาด
echo ==========================================================
echo กรุณาดูข้อความ ERROR ด้านบน
echo.
pause
exit /b 1

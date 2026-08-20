@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Iwara Downloader
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

echo ============================================
echo   Iwara Downloader
echo   %CD%
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [SETUP] Tao moi truong Python...
  where py >nul 2>nul
  if %ERRORLEVEL%==0 (py -3 -m venv .venv) else (python -m venv .venv)
  if errorlevel 1 (
    echo [ERROR] Can cai Python 3.11+ tu python.org
    pause
    exit /b 1
  )
)

echo [CHECK] Cai dat thu vien neu thieu...
".venv\Scripts\python.exe" -c "import customtkinter" 1>nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -U pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] pip install that bai
    pause
    exit /b 1
  )
)

echo [RUN] Mo cua so GUI...
echo Neu thay cua so Iwara Downloader = OK
echo ----------------------------------------
".venv\Scripts\python.exe" -u main.py
set EXITCODE=%ERRORLEVEL%
echo ----------------------------------------
echo Exit code: %EXITCODE%
if not %EXITCODE%==0 (
  echo [ERROR] App bi loi. Doc log o tren.
)
echo.
pause
exit /b %EXITCODE%

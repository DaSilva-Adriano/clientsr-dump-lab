@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title ClientSR Dump Lab

rem Double-click launcher. Uses the project venv; falls back to uv.

if exist ".venv\Scripts\pythonw.exe" (
  start "" /D "%~dp0" ".venv\Scripts\pythonw.exe" -m app
  exit /b 0
)

if exist ".venv\Scripts\python.exe" (
  start "" /D "%~dp0" ".venv\Scripts\python.exe" -m app
  exit /b 0
)

where uv >nul 2>&1
if %errorlevel%==0 (
  echo Starting ClientSR Dump Lab via uv...
  uv run python -m app
  if errorlevel 1 (
    echo.
    echo ClientSR Dump Lab exited with an error.
    pause
  )
  exit /b %errorlevel%
)

echo Could not find .venv or uv in:
echo   %cd%
echo.
echo Run this once, then double-click launch.bat again:
echo   uv sync
echo.
pause
exit /b 1

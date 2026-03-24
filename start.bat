@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

for /f "usebackq delims=" %%i in (`wsl wslpath -a "%CD%" 2^>nul`) do set "WSLP=%%i"
if not defined WSLP (
  echo WSL is not installed or wslpath failed. Check: wsl --status
  pause
  exit /b 1
)

wsl bash -lc "cd '!WSLP!' && python3 asset_store_download.py"
if errorlevel 1 (
  echo.
  echo If import errors: in WSL run: sudo apt update ^&^& sudo apt install -y python3 python3-pip ^&^& pip3 install requests
)
pause

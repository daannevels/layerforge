@echo off
REM Layerforge self-test for Windows - double-click this file. Shows PASS or FAIL.
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 (
  echo First start: installing uv ^(one time only^)...
  powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
echo Preparing Layerforge (first start can take a minute)...
uv sync --quiet
if errorlevel 1 (
  echo FAIL - setup failed, see above.
  pause
  exit /b 1
)
uv run layerforge selftest
echo.
echo Press any key to close this window.
pause >nul

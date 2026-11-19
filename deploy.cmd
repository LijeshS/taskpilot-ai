@echo off
rem Double-click to install and start TaskPilot AI. Extra options are passed through (e.g. deploy.cmd -InstallStartup).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" %*
echo.
pause

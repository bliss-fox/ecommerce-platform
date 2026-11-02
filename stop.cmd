@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\local-app.ps1" -Stop %*
set "appExit=%errorlevel%"
if not "%appExit%"=="0" pause
exit /b %appExit%

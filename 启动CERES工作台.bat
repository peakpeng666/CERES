@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\windows\start-lab.ps1"
set "ceres_exit=%errorlevel%"
if not "%ceres_exit%"=="0" pause
exit /b %ceres_exit%

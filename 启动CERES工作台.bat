@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\windows\start-lab.ps1"
set "urp_exit=%errorlevel%"
if not "%urp_exit%"=="0" pause
exit /b %urp_exit%

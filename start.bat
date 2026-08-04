@echo off
REM Double-click entry point: runs start.ps1 without needing to change
REM PowerShell's execution policy first.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*

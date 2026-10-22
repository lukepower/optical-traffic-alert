@echo off
rem Double-click: checks the USB camera(s) and this PC's speed (see tools\camera_check.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0camera_check.ps1" %*

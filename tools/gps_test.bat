@echo off
rem Double-click: lists the COM ports and checks the GPS receiver (put it at a window)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0gps_test.ps1" %*

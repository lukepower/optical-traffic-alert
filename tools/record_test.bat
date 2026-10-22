@echo off
rem Double-click: 30 s test recording with the Svpro (usb:1) and a check for lost frames
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0record_test.ps1" %*

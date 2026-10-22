@echo off
rem Double-click: checks the RTL-SDR driver, starts dump1090 and lists the aircraft received
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0adsb_test.ps1" %*

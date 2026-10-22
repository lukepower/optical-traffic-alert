@echo off
rem Double-click: builds dump1090.exe from the gvanem/Dump1090 source in F:\Programming\dump1090
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_dump1090.ps1" %*

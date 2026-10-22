@echo off
rem Double-click: tests only the 4K modes of the Svpro via DirectShow (usb:1) and Media Foundation (msmf:1).
rem Change the numbers below if your camera has others (see: ota cameras).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0camera_check.ps1" -Camera "usb:1,msmf:1" -Only4k -NoBench %*

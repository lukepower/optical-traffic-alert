@echo off
rem Double-click: tests only the 4K modes of camera msmf:1 (change below if your camera has another number)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0camera_check.ps1" -Camera msmf:1 -Only4k -NoBench %*

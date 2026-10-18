@echo off
rem Double-click: sets up the environment and trains on the NVIDIA GPU (see tools\train_windows.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0train_windows.ps1" %*

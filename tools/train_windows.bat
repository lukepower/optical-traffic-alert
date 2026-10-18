@echo off
rem Double-click: sets up the environment and trains on the GPU (NVIDIA or AMD) (see tools\train_windows.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0train_windows.ps1" %*

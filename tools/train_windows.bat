@echo off
rem Doppelklick startet Einrichtung + Training auf der NVIDIA-GPU (siehe tools\train_windows.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0train_windows.ps1" %*

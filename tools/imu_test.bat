@echo off
rem Double-click: live view of the WitMotion attitude sensor (finds the USB-TTL adapter, tries 115200 and 9600 baud)
if not exist "%~dp0..\.venv\Scripts\python.exe" (echo Run tools\camera_check.bat first & pause & exit /b 1)
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m pip install --quiet -e ".[openvino,serial]"
".venv\Scripts\python.exe" -m ota ports
".venv\Scripts\python.exe" -m ota imu-check %*
pause

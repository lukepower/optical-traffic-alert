@echo off
rem Builds dist\OTA-Recorder\OTA-Recorder.exe locally (the GitHub workflow does the same).
rem Run from the repository root:  packaging\build_recorder.bat
py -3.12 -m venv .venv-build || goto :error
call .venv-build\Scripts\activate.bat
python -m pip install --upgrade pip
pip install . pyserial boto3 pyinstaller || goto :error
pyinstaller --noconfirm --clean --windowed --name OTA-Recorder ^
  --icon packaging\ota-recorder.ico --collect-submodules ota --hidden-import serial.tools.list_ports --collect-data botocore ^
  packaging\recorder_main.py || goto :error
echo.
echo Done: dist\OTA-Recorder\OTA-Recorder.exe
goto :eof
:error
echo Build failed.
exit /b 1

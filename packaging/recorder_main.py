"""Entry point for the packaged OTA-Recorder.exe (PyInstaller)."""

import sys

from ota.recorder.gui import main

if __name__ == "__main__":
    sys.exit(main())

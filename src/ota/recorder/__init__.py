"""Session recorder for data collection (camera + ADS-B + FLARM/OGN).

* ``session.py`` - recording logic, usable without a GUI (``ota session``)
* ``gui.py``     - simple window for club members (``ota-recorder``)
"""

from .session import SessionRecorder, SessionSettings

__all__ = ["SessionRecorder", "SessionSettings"]

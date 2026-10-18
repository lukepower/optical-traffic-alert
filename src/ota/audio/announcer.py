"""Audio callouts into the intercom.

Phrases follow the familiar TCAS/FLARM style:

    "Traffic, two o'clock, high."            (ADVISORY / TRAFFIC)
    "Traffic, traffic, eleven o'clock, level, closing."   (WARNING)

Rules against alert fatigue (false or repeated alerts kill acceptance faster
than a missed target):

* at most one callout every ``min_gap_s`` seconds
* the same track is repeated only if its level rises, its clock position
  changes, or ``repeat_s`` has passed
* the same sector (clock position + high/level/low) is not repeated within
  ``repeat_s`` unless the level rises - one aircraft that is briefly split
  into two tracks must not cause two callouts
* higher levels pre-empt lower ones

Speech backends
---------------
* ``print``   - console only (development, tests)
* ``pyttsx3`` - Windows SAPI voices, no downloads (``pip install -e .[tts]``)
* ``piper``   - Piper neural TTS, offline, good quality; the same on Windows
  and on the Jetson. Needs the piper executable and a voice model (.onnx).
"""

from __future__ import annotations

import logging
import os
import platform
import queue
import subprocess
import tempfile
import threading
import time

from ..config import AudioConfig
from ..tracking.threat import Assessment, ThreatLevel

log = logging.getLogger(__name__)

NUMBERS = {
    "en": ["", "one", "two", "three", "four", "five", "six", "seven", "eight",
           "nine", "ten", "eleven", "twelve"],
    "de": ["", "ein", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht",
           "neun", "zehn", "elf", "zwölf"],
}
WORDS = {
    "en": {"traffic": "Traffic", "oclock": "o'clock", "high": "high", "low": "low",
           "level": "level", "closing": "closing"},
    "de": {"traffic": "Verkehr", "oclock": "Uhr", "high": "höher", "low": "tiefer",
           "level": "gleiche Höhe", "closing": "nähert sich"},
}


def compose_phrase(a: Assessment, language: str = "en") -> str:
    lang = language if language in WORDS else "en"
    w = WORDS[lang]
    clock = f"{NUMBERS[lang][a.clock]} {w['oclock']}"
    parts = [w["traffic"]]
    if a.level >= ThreatLevel.WARNING:
        parts = [w["traffic"], w["traffic"].lower()]
    parts += [clock, w[a.vertical]]
    if a.level >= ThreatLevel.ADVISORY:
        parts.append(w["closing"])
    return ", ".join(parts) + "."


# --------------------------------------------------------------------------- speakers

class Speaker:
    def say(self, text: str) -> None:
        raise NotImplementedError


class PrintSpeaker(Speaker):
    def say(self, text: str) -> None:
        print(f"[AUDIO] {text}", flush=True)


class Pyttsx3Speaker(Speaker):  # pragma: no cover - needs audio hardware
    def __init__(self, cfg: AudioConfig):
        import pyttsx3
        self.engine = pyttsx3.init()
        self.engine.setProperty("volume", cfg.volume)
        self.engine.setProperty("rate", 185)

    def say(self, text: str) -> None:
        self.engine.say(text)
        self.engine.runAndWait()


class PiperSpeaker(Speaker):  # pragma: no cover - needs piper binary
    def __init__(self, cfg: AudioConfig):
        self.cfg = cfg

    def say(self, text: str) -> None:
        with tempfile.TemporaryDirectory() as d:
            wav = os.path.join(d, "out.wav")
            subprocess.run([self.cfg.piper_executable, "--model", self.cfg.piper_model,
                            "--output_file", wav], input=text.encode("utf-8"),
                           check=True, capture_output=True)
            if platform.system() == "Windows":
                import winsound
                winsound.PlaySound(wav, winsound.SND_FILENAME)
            else:
                subprocess.run(["aplay", "-q", wav], check=False)


def make_speaker(cfg: AudioConfig) -> Speaker:
    if cfg.backend == "print":
        return PrintSpeaker()
    if cfg.backend == "pyttsx3":
        return Pyttsx3Speaker(cfg)
    if cfg.backend == "piper":
        return PiperSpeaker(cfg)
    raise ValueError(f"Unknown audio backend: {cfg.backend!r}")


# --------------------------------------------------------------------------- announcer

class Announcer:
    """Decides *what* to say and *when*; speaking happens on a worker thread
    so the vision pipeline never blocks on audio."""

    def __init__(self, cfg: AudioConfig, speaker: Speaker | None = None,
                 clock=time.monotonic, threaded: bool = True):
        self.cfg = cfg
        self.speaker = speaker or make_speaker(cfg)
        self.clock = clock
        self._last_any = -1e9
        # track key -> (time, level, clock position)
        self._last: dict[str, tuple[float, ThreatLevel, int]] = {}
        # sector (clock, vertical) -> (time, level): one physical target may
        # temporarily be split into several tracks; don't repeat it per track
        self._sector: dict[tuple[int, str], tuple[float, ThreatLevel]] = {}
        self.spoken: list[str] = []
        self._threaded = threaded
        self._q: queue.Queue[str] = queue.Queue(maxsize=2)
        if threaded:
            threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        while True:
            text = self._q.get()
            try:
                self.speaker.say(text)
            except Exception:  # noqa: BLE001 - audio must never crash the pipeline
                log.exception("speech output failed")

    def _should_announce(self, a: Assessment, now: float) -> bool:
        if a.level == ThreatLevel.NONE:
            return False
        sec = self._sector.get((a.clock, a.vertical))
        if sec and now - sec[0] < self.cfg.repeat_s and sec[1] >= a.level:
            return False
        key = f"{a.camera}:{a.track_id}"
        prev = self._last.get(key)
        if prev is None:
            return True
        t, level, clock = prev
        if a.level > level:
            return True
        if a.clock != clock and a.level >= ThreatLevel.ADVISORY:
            return True
        return a.level >= ThreatLevel.ADVISORY and now - t >= self.cfg.repeat_s

    def offer(self, assessments: list[Assessment], now: float | None = None) -> str | None:
        """Pick the most urgent assessment and announce it if the rules allow.

        ``now`` is the frame timestamp; pass it so that timing rules follow the
        video time (a recorded video may be processed faster or slower than
        real time). Returns the phrase that was spoken (or None)."""
        now = self.clock() if now is None else now
        candidates = [a for a in assessments if self._should_announce(a, now)]
        if not candidates:
            return None
        a = max(candidates, key=lambda x: (x.level, -(x.tau_s or 1e9)))
        # A warning may interrupt the global gap; everything else waits.
        if now - self._last_any < self.cfg.min_gap_s and a.level < ThreatLevel.WARNING:
            return None
        phrase = compose_phrase(a, self.cfg.language)
        self._last[f"{a.camera}:{a.track_id}"] = (now, a.level, a.clock)
        self._sector[(a.clock, a.vertical)] = (now, a.level)
        self._last_any = now
        self.spoken.append(phrase)
        if self._threaded:
            try:
                self._q.put_nowait(phrase)
            except queue.Full:
                log.warning("audio queue full, dropping: %s", phrase)
        else:
            self.speaker.say(phrase)
        return phrase

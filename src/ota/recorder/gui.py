"""OTA Recorder - simple recording window for club members.

Records camera video + ADS-B + FLARM/OGN into one session folder (see
``session.py``). Start with ``ota-recorder`` or the packaged ``OTA-Recorder.exe``.
Tkinter only (part of Python), so the .exe needs nothing else installed.
"""

from __future__ import annotations

import base64
import logging
import os
import platform
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2

from .. import __version__
from ..sensors.traffic import look_angles
from .assets import LOGO_64_PNG_B64
from .session import CameraRecorder, SessionRecorder, SessionSettings

log = logging.getLogger(__name__)

LANGS = {"de": "Deutsch", "it": "Italiano", "en": "English"}

# key: (de, it, en)
TEXT = {
    "title": ("OTA Recorder – Aufzeichnung für Optical Traffic Alert",
              "OTA Recorder – registrazione per Optical Traffic Alert",
              "OTA Recorder – recording for Optical Traffic Alert"),
    "session": ("Aufnahme", "Registrazione", "Recording"),
    "operator": ("Name", "Nome", "Name"),
    "station": ("Ort / Flugplatz", "Luogo / aeroporto", "Location / airfield"),
    "lat": ("Breite (°)", "Latitudine (°)", "Latitude (°)"),
    "lon": ("Länge (°)", "Longitudine (°)", "Longitude (°)"),
    "elev": ("Höhe Kamera (m MSL)", "Quota camera (m s.l.m.)", "Camera altitude (m MSL)"),
    "mode": ("Betrieb", "Modalità", "Mode"),
    "mode_ground": ("Am Boden (fest)", "A terra (fissa)", "On the ground (fixed)"),
    "mode_aircraft": ("Im Flugzeug", "In volo", "In the aircraft"),
    "qnh": ("QNH (hPa)", "QNH (hPa)", "QNH (hPa)"),
    "notes": ("Notizen", "Note", "Notes"),
    "folder": ("Zielordner", "Cartella", "Folder"),
    "camera": ("Kamera", "Camera", "Camera"),
    "cam_on": ("Video aufnehmen", "Registra video", "Record video"),
    "device": ("Gerät", "Dispositivo", "Device"),
    "search": ("Suchen", "Cerca", "Search"),
    "resolution": ("Auflösung", "Risoluzione", "Resolution"),
    "fps": ("Bilder/s", "Fotogrammi/s", "Frames/s"),
    "format": ("Format", "Formato", "Format"),
    "exposure": ("Belichtung (leer = auto)", "Esposizione (vuoto = auto)", "Exposure (empty = auto)"),
    "gain": ("Verstärkung (leer = auto)", "Guadagno (vuoto = auto)", "Gain (empty = auto)"),
    "heading": ("Blickrichtung (° rechtweisend)", "Direzione (° vera)", "Looking towards (° true)"),
    "heading_hint": ("ungefähr genügt – wird bei der Auswertung korrigiert",
                     "basta circa – viene corretta nell'analisi",
                     "approximate is fine – corrected during evaluation"),
    "pitch": ("Neigung nach oben (°)", "Inclinazione verso l'alto (°)", "Tilt up (°)"),
    "hfov": ("Bildwinkel horizontal (°)", "Angolo orizzontale (°)", "Horizontal field of view (°)"),
    "rec_mode": ("Aufnahme", "Registrazione", "Recording"),
    "rec_smart": ("Nur bei Verkehr/Bewegung (spart Speicher)", "Solo con traffico/movimento (risparmia spazio)",
                  "Only with traffic/motion (saves space)"),
    "rec_all": ("Alles aufnehmen", "Registra tutto", "Record everything"),
    "motion": ("Auch bei Bewegung im Bild (Flugzeuge ohne FLARM)", "Anche con movimento nell'immagine (senza FLARM)",
               "Also on motion in the image (aircraft without FLARM)"),
    "kept": ("behalten", "conservato", "kept"),
    "of": ("von", "di", "of"),
    "trigger": ("Auslöser", "Attivazione", "Trigger"),
    "trig_traffic": ("Verkehr", "traffico", "traffic"),
    "trig_motion": ("Bewegung", "movimento", "motion"),
    "trig_none": ("–", "–", "–"),
    "preview": ("Vorschau", "Anteprima", "Preview"),
    "preview_stop": ("Vorschau aus", "Anteprima off", "Stop preview"),
    "traffic": ("Verkehrsdaten", "Dati traffico", "Traffic data"),
    "adsb": ("ADS-B (dump1090)", "ADS-B (dump1090)", "ADS-B (dump1090)"),
    "dump1090": ("dump1090.exe starten (optional)", "avvia dump1090.exe (opzionale)",
                 "start dump1090.exe (optional)"),
    "ogn_online": ("FLARM/OGN aus dem Internet", "FLARM/OGN da internet", "FLARM/OGN from the internet"),
    "radius": ("Umkreis (km)", "Raggio (km)", "Radius (km)"),
    "ogn_local": ("Eigener OGN-Empfänger (868 MHz)", "Ricevitore OGN proprio (868 MHz)",
                  "Own OGN receiver (868 MHz)"),
    "flarm": ("FLARM-Gerät (NMEA)", "Dispositivo FLARM (NMEA)", "FLARM device (NMEA)"),
    "start": ("● Aufnahme starten", "● Avvia registrazione", "● Start recording"),
    "stop": ("■ Aufnahme beenden", "■ Termina registrazione", "■ Stop recording"),
    "starting": ("Starte …", "Avvio …", "Starting …"),
    "open_folder": ("Ordner öffnen", "Apri cartella", "Open folder"),
    "status": ("Status", "Stato", "Status"),
    "idle": ("Bereit", "Pronto", "Ready"),
    "recording": ("Aufnahme läuft", "Registrazione in corso", "Recording"),
    "elapsed": ("Dauer", "Durata", "Duration"),
    "disk": ("Freier Speicher", "Spazio libero", "Free disk space"),
    "frames": ("Bilder", "Fotogrammi", "Frames"),
    "dropped": ("verworfen", "persi", "dropped"),
    "sharpness": ("Schärfe", "Nitidezza", "Sharpness"),
    "brightness": ("Helligkeit", "Luminosità", "Brightness"),
    "clipped": ("überbelichtet", "sovraesposto", "overexposed"),
    "connected": ("verbunden", "connesso", "connected"),
    "offline": ("keine Verbindung", "non connesso", "not connected"),
    "lines": ("Zeilen", "righe", "lines"),
    "aircraft": ("Luftfahrzeuge", "Aeromobili", "Aircraft"),
    "col_id": ("Kennung", "ID", "ID"),
    "col_call": ("Rufzeichen", "Nominativo", "Callsign"),
    "col_src": ("Quelle", "Fonte", "Source"),
    "col_type": ("Typ", "Tipo", "Type"),
    "col_dist": ("km", "km", "km"),
    "col_brg": ("Richtung °", "Direzione °", "Bearing °"),
    "col_alt": ("Höhe m", "Quota m", "Alt m"),
    "col_age": ("Alter s", "Età s", "Age s"),
    "in_view": ("im Bild", "in vista", "in view"),
    "done_title": ("Aufnahme beendet", "Registrazione terminata", "Recording finished"),
    "done_msg": ("Gespeichert in:\n{folder}\n\nDauer {dur}, {frames} Bilder, {ac} Luftfahrzeuge.\n\n"
                 "Bitte den ganzen Ordner weitergeben.",
                 "Salvato in:\n{folder}\n\nDurata {dur}, {frames} fotogrammi, {ac} aeromobili.\n\n"
                 "Per favore consegnare l'intera cartella.",
                 "Saved in:\n{folder}\n\nDuration {dur}, {frames} frames, {ac} aircraft.\n\n"
                 "Please hand over the whole folder."),
    "error": ("Fehler", "Errore", "Error"),
    "bad_value": ("Ungültiger Wert bei „{field}“", "Valore non valido per «{field}»",
                  "Invalid value for “{field}”"),
    "nothing": ("Weder Kamera noch Verkehrsdaten ausgewählt.", "Né camera né dati traffico selezionati.",
                "Neither camera nor traffic data selected."),
    "warn_fps": ("Kamera liefert nur {fps:.1f} statt {want:.0f} Bilder/s",
                 "La camera fornisce solo {fps:.1f} invece di {want:.0f} fotogrammi/s",
                 "Camera delivers only {fps:.1f} instead of {want:.0f} frames/s"),
    "warn_res": ("Kamera liefert {w}×{h} statt {ww}×{hh}", "La camera fornisce {w}×{h} invece di {ww}×{hh}",
                 "Camera delivers {w}×{h} instead of {ww}×{hh}"),
    "warn_clip": ("Zu hell – Belichtung verringern", "Troppo chiaro – ridurre l'esposizione",
                  "Too bright – reduce exposure"),
    "warn_drop": ("Bilder werden verworfen – Rechner/Festplatte zu langsam? Bildrate senken.",
                  "Fotogrammi persi – computer/disco troppo lento? Ridurre i fotogrammi/s.",
                  "Frames are dropped – computer/disk too slow? Reduce frame rate."),
    "focus_hint": ("Schärfe: auf ein fernes Objekt scharf stellen, Wert maximieren.",
                   "Nitidezza: mettere a fuoco un oggetto lontano, massimizzare il valore.",
                   "Sharpness: focus on a distant object and maximise the value."),
    "language": ("Sprache", "Lingua", "Language"),
    "no_cameras": ("Keine Kamera gefunden", "Nessuna camera trovata", "No camera found"),
}


def settings_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "OTA-Recorder" / "settings.json"


def open_in_explorer(path: Path) -> None:
    if platform.system() == "Windows":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports
        return [p.device for p in list_ports.comports()]
    except Exception:
        return []


def fmt_duration(s: float) -> str:
    s = int(s)
    return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"


class RecorderApp:
    PREVIEW_W, PREVIEW_H = 640, 360

    def __init__(self, root: tk.Tk, settings_file: Path | None = None):
        self.root = root
        self.settings_file = settings_file or settings_path()
        self.s = SessionSettings.load(self.settings_file)
        self.rec: SessionRecorder | None = None
        self.preview_cam: CameraRecorder | None = None
        self._starting = False
        self._photo = None
        self.vars: dict[str, tk.Variable] = {}
        try:
            self.logo = tk.PhotoImage(data=LOGO_64_PNG_B64)
            root.iconphoto(True, self.logo)
        except tk.TclError:
            self.logo = None
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.main: ttk.Frame | None = None
        self.build()
        self.root.after(300, self.tick)

    # -- helpers --

    def t(self, key: str) -> str:
        i = {"de": 0, "it": 1, "en": 2}.get(self.s.language, 0)
        return TEXT[key][i]

    def var(self, name: str, value, kind=tk.StringVar):
        v = kind(value=value)
        self.vars[name] = v
        return v

    def row(self, parent, r: int, label: str, widget: tk.Widget, hint: str | None = None) -> None:
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", padx=(6, 4), pady=2)
        widget.grid(row=r, column=1, sticky="ew", padx=(0, 6), pady=2)
        if hint:
            ttk.Label(parent, text=hint, foreground="#666").grid(row=r + 1, column=1, sticky="w",
                                                               padx=(0, 6))

    def entry(self, parent, name: str, value, width: int = 22) -> ttk.Entry:
        return ttk.Entry(parent, textvariable=self.var(name, "" if value is None else str(value)),
                         width=width)

    # -- layout --

    def build(self) -> None:
        if self.main is not None:
            self.main.destroy()
        self.vars.clear()
        s = self.s
        self.root.title(self.t("title"))
        main = self.main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)

        # header
        head = ttk.Frame(main)
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        if self.logo:
            ttk.Label(head, image=self.logo).pack(side="left")
        ttk.Label(head, text="OTA Recorder", font=("Segoe UI", 16, "bold")).pack(side="left", padx=8)
        ttk.Label(head, text=f"v{__version__}", foreground="#666").pack(side="left")
        lang = ttk.Combobox(head, values=list(LANGS.values()), state="readonly", width=10)
        lang.set(LANGS.get(s.language, "Deutsch"))
        lang.bind("<<ComboboxSelected>>", lambda e: self.change_language(lang.get()))
        lang.pack(side="right")
        ttk.Label(head, text=self.t("language")).pack(side="right", padx=4)

        left = ttk.Notebook(main)
        left.grid(row=1, column=0, sticky="nsw", padx=(0, 8), pady=(6, 0))
        right = ttk.Frame(main)
        right.grid(row=1, column=1, sticky="nsew")
        main.rowconfigure(1, weight=1)

        # -- session --
        f = ttk.Frame(left, padding=4)
        left.add(f, text=self.t("session"))
        f.columnconfigure(1, weight=1)
        self.row(f, 0, self.t("operator"), self.entry(f, "operator", s.operator))
        self.row(f, 1, self.t("station"), self.entry(f, "station_name", s.station_name))
        self.row(f, 2, self.t("lat"), self.entry(f, "lat", s.lat))
        self.row(f, 3, self.t("lon"), self.entry(f, "lon", s.lon))
        self.row(f, 4, self.t("elev"), self.entry(f, "elev_m", s.elev_m))
        modes = {"ground": self.t("mode_ground"), "aircraft": self.t("mode_aircraft")}
        self._modes = {v: k for k, v in modes.items()}
        mode = ttk.Combobox(f, values=list(modes.values()), state="readonly",
                            textvariable=self.var("mode", modes.get(s.mode, modes["ground"])))
        self.row(f, 5, self.t("mode"), mode)
        self.row(f, 6, self.t("qnh"), self.entry(f, "qnh_hpa", s.qnh_hpa))
        self.row(f, 7, self.t("notes"), self.entry(f, "notes", s.notes))
        fr = ttk.Frame(f)
        ttk.Entry(fr, textvariable=self.var("output_dir", s.output_dir), width=17).pack(
            side="left", fill="x", expand=True)
        ttk.Button(fr, text="…", width=3, command=self.choose_folder).pack(side="left")
        self.row(f, 8, self.t("folder"), fr)

        # -- camera --
        f = ttk.Frame(left, padding=4)
        left.add(f, text=self.t("camera"))
        f.columnconfigure(1, weight=1)
        ttk.Checkbutton(f, text=self.t("cam_on"),
                        variable=self.var("camera_enabled", s.camera_enabled, tk.BooleanVar)
                        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=6)
        fr = ttk.Frame(f)
        self.cam_box = ttk.Combobox(fr, values=["usb:0", "usb:1", "usb:2", "usb:3"], width=12,
                                    textvariable=self.var("camera_source", s.camera_source))
        self.cam_box.pack(side="left", fill="x", expand=True)
        ttk.Button(fr, text=self.t("search"), command=self.find_cameras).pack(side="left")
        self.row(f, 1, self.t("device"), fr)
        res = ttk.Combobox(f, values=["3840x2160", "2560x1440", "1920x1080"],
                           textvariable=self.var("resolution", f"{s.width}x{s.height}"), width=12)
        self.row(f, 2, self.t("resolution"), res)
        self.row(f, 3, self.t("fps"), self.entry(f, "fps", s.fps))
        fmt = ttk.Combobox(f, values=["YUY2", "MJPG"], textvariable=self.var("pixel_format",
                                                                           s.pixel_format), width=8)
        self.row(f, 4, self.t("format"), fmt)
        self.row(f, 5, self.t("exposure"), self.entry(f, "exposure", s.exposure))
        self.row(f, 6, self.t("gain"), self.entry(f, "gain", s.gain))
        self.row(f, 7, self.t("heading"), self.entry(f, "cam_heading_deg", s.cam_heading_deg),
                 self.t("heading_hint"))
        self.row(f, 9, self.t("pitch"), self.entry(f, "cam_pitch_deg", s.cam_pitch_deg))
        self.row(f, 10, self.t("hfov"), self.entry(f, "hfov_deg", s.hfov_deg))
        recm = {"smart": self.t("rec_smart"), "all": self.t("rec_all")}
        self._recmodes = {v: k for k, v in recm.items()}
        self.row(f, 11, self.t("rec_mode"), ttk.Combobox(
            f, values=list(recm.values()), state="readonly", width=30,
            textvariable=self.var("record_mode", recm.get(s.record_mode, recm["smart"]))))
        ttk.Checkbutton(f, text=self.t("motion"),
                        variable=self.var("motion_trigger", s.motion_trigger, tk.BooleanVar)
                        ).grid(row=12, column=0, columnspan=2, sticky="w", padx=6)
        self.preview_btn = ttk.Button(f, text=self.t("preview"), command=self.toggle_preview)
        self.preview_btn.grid(row=13, column=0, columnspan=2, sticky="ew", padx=6, pady=4)

        # -- traffic --
        f = ttk.Frame(left, padding=4)
        left.add(f, text=self.t("traffic"))
        f.columnconfigure(1, weight=1)
        r = 0
        for name, label, enabled, addr_name, addr in (
                ("adsb_enabled", self.t("adsb"), s.adsb_enabled, "adsb_address", s.adsb_address),
                ("ogn_local", self.t("ogn_local"), s.ogn_local, "ogn_local_address", s.ogn_local_address)):
            ttk.Checkbutton(f, text=label, variable=self.var(name, enabled, tk.BooleanVar)).grid(
                row=r, column=0, sticky="w", padx=6)
            self.entry(f, addr_name, addr, 18).grid(row=r, column=1, sticky="ew", padx=(0, 6))
            r += 1
            if name == "adsb_enabled":
                fr = ttk.Frame(f)
                ttk.Entry(fr, textvariable=self.var("dump1090_path", s.dump1090_path), width=15).pack(
                    side="left", fill="x", expand=True)
                ttk.Button(fr, text="…", width=3, command=self.choose_dump1090).pack(side="left")
                ttk.Label(f, text="  " + self.t("dump1090"), foreground="#666").grid(
                    row=r, column=0, sticky="w", padx=6)
                fr.grid(row=r, column=1, sticky="ew", padx=(0, 6))
                r += 1
        ttk.Checkbutton(f, text=self.t("ogn_online"),
                        variable=self.var("ogn_online", s.ogn_online, tk.BooleanVar)).grid(
            row=r, column=0, sticky="w", padx=6)
        fr = ttk.Frame(f)
        ttk.Label(fr, text=self.t("radius")).pack(side="left")
        ttk.Entry(fr, textvariable=self.var("ogn_radius_km", str(s.ogn_radius_km)), width=6).pack(
            side="left", padx=4)
        fr.grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Checkbutton(f, text=self.t("flarm"),
                        variable=self.var("flarm_enabled", s.flarm_enabled, tk.BooleanVar)).grid(
            row=r, column=0, sticky="w", padx=6)
        ports = [f"{p}@19200" for p in list_serial_ports()] or ["COM4@19200"]
        ttk.Combobox(f, values=ports, textvariable=self.var("flarm_address", s.flarm_address),
                     width=16).grid(row=r, column=1, sticky="ew", padx=(0, 6))

        # -- right: preview, status, traffic --
        right.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(right, width=self.PREVIEW_W, height=self.PREVIEW_H, bg="#0B1F3A",
                                highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="n")
        self.canvas.create_text(self.PREVIEW_W // 2, self.PREVIEW_H // 2, text=self.t("camera"),
                                fill="#FFB300", font=("Segoe UI", 14))
        btns = ttk.Frame(right)
        btns.grid(row=1, column=0, sticky="ew", pady=6)
        style = ttk.Style()
        style.configure("Big.TButton", font=("Segoe UI", 13, "bold"), padding=8)
        self.start_btn = ttk.Button(btns, text=self.t("start"), style="Big.TButton",
                                    command=self.toggle_recording)
        self.start_btn.pack(side="left", fill="x", expand=True)
        ttk.Button(btns, text=self.t("open_folder"), command=self.open_folder).pack(side="left", padx=6)

        st = ttk.LabelFrame(right, text=self.t("status"))
        st.grid(row=2, column=0, sticky="ew")
        self.status_var = tk.StringVar(value=self.t("idle"))
        self.cam_var = tk.StringVar(value="")
        self.feed_var = tk.StringVar(value="")
        self.warn_var = tk.StringVar(value="")
        ttk.Label(st, textvariable=self.status_var, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=6)
        ttk.Label(st, textvariable=self.cam_var, font=("Consolas", 9)).pack(anchor="w", padx=6)
        ttk.Label(st, textvariable=self.feed_var, font=("Consolas", 9)).pack(anchor="w", padx=6)
        ttk.Label(st, textvariable=self.warn_var, foreground="#B00020", wraplength=620).pack(
            anchor="w", padx=6)

        tf = ttk.LabelFrame(right, text=self.t("aircraft"))
        tf.grid(row=3, column=0, sticky="nsew", pady=(6, 0))
        right.rowconfigure(3, weight=1)
        cols = ("id", "call", "src", "type", "dist", "brg", "alt", "age")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", height=8)
        widths = (80, 80, 70, 110, 55, 80, 65, 55)
        for c, w in zip(cols, widths):
            self.tree.heading(c, text=self.t(f"col_{c}"))
            self.tree.column(c, width=w, anchor="w" if c in ("id", "call", "src", "type") else "e")
        self.tree.tag_configure("view", background="#FFF3CD")
        self.tree.pack(fill="both", expand=True)
        self.set_running_ui()

    # -- settings --

    def collect(self) -> SessionSettings | None:
        s = SessionSettings(**asdict(self.s))
        v = {k: x.get() for k, x in self.vars.items()}
        try:
            for name in ("operator", "station_name", "notes", "output_dir", "camera_source",
                         "pixel_format", "adsb_address", "dump1090_path", "ogn_local_address",
                         "flarm_address"):
                setattr(s, name, str(v[name]).strip())
            s.record_mode = self._recmodes.get(v["record_mode"], "smart")
            for name in ("camera_enabled", "adsb_enabled", "ogn_online", "ogn_local", "flarm_enabled",
                         "motion_trigger"):
                setattr(s, name, bool(v[name]))
            s.mode = self._modes.get(v["mode"], "ground")
            fields = {"lat": "lat", "lon": "lon", "elev_m": "elev", "qnh_hpa": "qnh", "fps": "fps",
                      "cam_heading_deg": "heading", "cam_pitch_deg": "pitch", "hfov_deg": "hfov",
                      "ogn_radius_km": "radius"}
            for name, label in fields.items():
                try:
                    setattr(s, name, float(str(v[name]).replace(",", ".")))
                except ValueError:
                    raise ValueError(self.t("bad_value").format(field=self.t(label))) from None
            for name, label in (("exposure", "exposure"), ("gain", "gain")):
                txt = str(v[name]).strip().replace(",", ".")
                try:
                    setattr(s, name, float(txt) if txt else None)
                except ValueError:
                    raise ValueError(self.t("bad_value").format(field=self.t(label))) from None
            try:
                w, h = (int(x) for x in str(v["resolution"]).lower().split("x"))
                s.width, s.height = w, h
            except ValueError:
                raise ValueError(self.t("bad_value").format(field=self.t("resolution"))) from None
            if not (-90 <= s.lat <= 90 and -180 <= s.lon <= 180):
                raise ValueError(self.t("bad_value").format(field=self.t("lat")))
            if s.ogn_online and not 1 <= s.ogn_radius_km <= 500:
                raise ValueError(self.t("bad_value").format(field=self.t("radius")))
        except ValueError as e:
            messagebox.showerror(self.t("error"), str(e))
            return None
        return s

    def save_settings(self) -> None:
        s = self.collect() if self.vars else None
        if s:
            self.s = s
            try:
                s.save(self.settings_file)
            except OSError as e:  # pragma: no cover
                log.warning("cannot save settings: %s", e)

    def change_language(self, name: str) -> None:
        code = next((k for k, v in LANGS.items() if v == name), "de")
        s = self.collect()
        if s:
            self.s = s
        self.s.language = code
        self.build()

    def choose_folder(self) -> None:
        d = filedialog.askdirectory(initialdir=self.vars["output_dir"].get() or str(Path.home()))
        if d:
            self.vars["output_dir"].set(d)

    def choose_dump1090(self) -> None:
        f = filedialog.askopenfilename(filetypes=[("dump1090", "*.exe"), ("*", "*")])
        if f:
            self.vars["dump1090_path"].set(f)

    def find_cameras(self) -> None:
        backend = cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY
        found = []
        for i in range(6):
            if self.preview_cam and self.vars["camera_source"].get() == f"usb:{i}":
                found.append(f"usb:{i}")
                continue
            cap = cv2.VideoCapture(i, backend)
            if cap.isOpened():
                found.append(f"usb:{i}")
            cap.release()
        self.cam_box.configure(values=found or ["usb:0"])
        if found and self.vars["camera_source"].get() not in found:
            self.vars["camera_source"].set(found[0])
        if not found:
            messagebox.showinfo(self.t("camera"), self.t("no_cameras"))

    # -- preview / recording --

    def toggle_preview(self) -> None:
        if self.preview_cam:
            self.preview_cam.stop()
            self.preview_cam = None
            self.preview_btn.configure(text=self.t("preview"))
            return
        s = self.collect()
        if not s:
            return
        try:
            self.preview_cam = CameraRecorder(s, None).start()
        except Exception as e:
            messagebox.showerror(self.t("error"), str(e))
            return
        self.preview_btn.configure(text=self.t("preview_stop"))

    def toggle_recording(self) -> None:
        if self._starting:
            return
        if self.rec and self.rec.running:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        s = self.collect()
        if not s:
            return
        if not (s.camera_enabled or s.adsb_enabled or s.ogn_online or s.ogn_local or s.flarm_enabled):
            messagebox.showerror(self.t("error"), self.t("nothing"))
            return
        self.s = s
        self.save_settings()
        if self.preview_cam:
            self.toggle_preview()
        self._starting = True
        self.start_btn.configure(text=self.t("starting"))
        self.rec = SessionRecorder(s)

        def worker():
            try:
                self.rec.start()
                err = None
            except Exception as e:
                log.exception("start failed")
                err = str(e)
            self.root.after(0, lambda: self.started(err))
        threading.Thread(target=worker, daemon=True).start()

    def started(self, err: str | None) -> None:
        self._starting = False
        if err:
            messagebox.showerror(self.t("error"), err)
            self.rec = None
        self.set_running_ui()

    def stop_recording(self) -> None:
        rec = self.rec
        folder = rec.stop()
        st = rec.stats()
        self.set_running_ui()
        frames = st.get("camera", {}).get("written", 0)
        ac = st.get("traffic", {}).get("aircraft", 0)
        messagebox.showinfo(self.t("done_title"), self.t("done_msg").format(
            folder=folder, dur=fmt_duration(st["duration_s"]), frames=frames, ac=ac))

    def set_running_ui(self) -> None:
        running = bool(self.rec and self.rec.running)
        self.start_btn.configure(text=self.t("stop") if running else self.t("start"))
        state = "disabled" if running else "normal"
        self.preview_btn.configure(state=state)

    def open_folder(self) -> None:
        if self.rec and self.rec.dir:
            path = self.rec.dir
        else:
            path = Path(os.path.expanduser(self.vars["output_dir"].get()))
            path.mkdir(parents=True, exist_ok=True)
        open_in_explorer(path)

    # -- periodic update --

    def tick(self) -> None:
        try:
            self.update_status()
        except Exception:  # never let the UI timer die
            log.exception("status update failed")
        self.root.after(500, self.tick)

    def show_preview(self, img) -> None:
        if img is None:
            return
        h, w = img.shape[:2]
        scale = min(self.PREVIEW_W / w, self.PREVIEW_H / h)
        small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        ok, png = cv2.imencode(".png", small)
        if not ok:
            return
        self._photo = tk.PhotoImage(data=base64.b64encode(png.tobytes()).decode())
        self.canvas.delete("all")
        self.canvas.create_image(self.PREVIEW_W // 2, self.PREVIEW_H // 2, image=self._photo)

    def camera_line(self, cam: CameraRecorder, s: SessionSettings) -> list[str]:
        c = cam.status
        line = (f"{self.t('camera')}: {c.width}×{c.height} {c.format}  {c.fps:4.1f} fps  "
                f"{self.t('frames')} {c.frames}")
        if cam.out_dir is not None:
            line += f" ({c.dropped} {self.t('dropped')}, {c.bytes / 1e9:.2f} GB)"
        if cam.smart:
            total = c.kept_s + c.discarded_s
            pct = 100 * c.kept_s / total if total else 0
            active = cam.triggers.active(time.time(), 2.0)
            trig = ", ".join(self.t(f"trig_{a}") for a in active) or self.t("trig_none")
            kept = (f"{self.t('kept')} {fmt_duration(c.kept_s)} {self.t('of')} "
                    f"{fmt_duration(total)} ({pct:.0f} %)   ") if total else ""
            line += f"\n{kept}{self.t('trigger')}: {trig}"
        self.cam_var.set(line + f"\n{self.t('sharpness')} {c.sharpness:6.1f}   "
                                f"{self.t('brightness')} {c.brightness:5.1f}   "
                                f"{self.t('clipped')} {c.clipped_pct:4.1f} %")
        warn = []
        if c.width and (c.width, c.height) != (s.width, s.height) and not s.camera_source.startswith("file:"):
            warn.append(self.t("warn_res").format(w=c.width, h=c.height, ww=s.width, hh=s.height))
        if c.frames > 3 * s.fps and c.fps < 0.8 * s.fps:
            warn.append(self.t("warn_fps").format(fps=c.fps, want=s.fps))
        if c.clipped_pct > 2:
            warn.append(self.t("warn_clip"))
        if c.dropped > max(5, 0.02 * c.frames):
            warn.append(self.t("warn_drop"))
        if c.error:
            warn.append(c.error)
        return warn

    def update_status(self) -> None:
        warn: list[str] = []
        if self.preview_cam and not (self.rec and self.rec.running):
            self.show_preview(self.preview_cam.preview)
            warn += self.camera_line(self.preview_cam, self.s)
            warn.append(self.t("focus_hint"))
            self.status_var.set(self.t("preview"))
        rec = self.rec
        if rec and rec.running:
            rec.check()
            el = time.time() - rec.t_start
            free = rec.disk_free_gb()
            self.status_var.set(f"● {self.t('recording')}   {self.t('elapsed')} {fmt_duration(el)}   "
                                f"{self.t('disk')} {free:.0f} GB")
            if rec.camera:
                self.show_preview(rec.camera.preview)
                warn += self.camera_line(rec.camera, rec.s)
            lines = []
            now = time.time()
            for f in rec.feeds:
                ok = f.connected and (now - f.last_rx < 60 or f.lines == 0)
                state = self.t("connected") if f.connected else self.t("offline")
                dot = "●" if ok else "○"
                lines.append(f"{dot} {f.name:<13} {state:<16} {f.lines:7d} {self.t('lines')}"
                             + (f"  ({f.error})" if f.error and not f.connected else ""))
            self.feed_var.set("\n".join(lines))
            if rec.error:
                warn.append(rec.error)
            self.update_traffic(rec)
        elif not self.preview_cam:
            self.status_var.set(self.t("idle"))
            self.cam_var.set("")
            self.feed_var.set("")
        self.warn_var.set("\n".join(warn))

    def update_traffic(self, rec: SessionRecorder) -> None:
        if not rec.logger:
            return
        s = rec.s
        own = rec.logger.table.ownship()
        if s.mode == "aircraft" and own and own.lat is not None:
            obs = (own.lat, own.lon, own.alt_m or s.elev_m)
        else:
            obs = (s.lat, s.lon, s.elev_m)
        now = time.time()
        rows = []
        for r in rec.logger.table.active(120, now):
            alt = r.alt_m if r.alt_m is not None else obs[2]
            az, el, rng = look_angles(*obs, r.lat, r.lon, alt)
            in_view = False
            if s.camera_enabled and s.mode == "ground":
                d = (az - s.cam_heading_deg + 180) % 360 - 180
                in_view = abs(d) <= s.hfov_deg / 2
            rows.append((rng, r, az, in_view))
        rows.sort(key=lambda x: x[0])
        self.tree.delete(*self.tree.get_children())
        for rng, r, az, in_view in rows[:40]:
            alt = f"{r.alt_m:.0f}{'*' if r.alt_ref == 'baro' else ''}" if r.alt_m is not None else "–"
            self.tree.insert("", "end", tags=("view",) if in_view else (), values=(
                r.addr, r.callsign, r.src, r.acft_type, f"{rng / 1000:.1f}", f"{az:.0f}", alt,
                f"{now - r.rx:.0f}"))

    def on_close(self) -> None:
        if self.rec and self.rec.running:
            self.stop_recording()
        if self.preview_cam:
            self.preview_cam.stop()
        self.save_settings()
        self.root.destroy()


def self_test(out: str) -> int:
    """Used by the build pipeline: open the window briefly, then write 'ok' to ``out``."""
    import tempfile
    root = tk.Tk()
    tmp = Path(tempfile.mkdtemp())
    app = RecorderApp(root, tmp / "settings.json")
    for lang in ("it", "en", "de"):
        app.change_language(LANGS[lang])
    root.after(1500, root.destroy)
    root.mainloop()
    Path(out).write_text(f"ok {__version__} opencv {cv2.__version__}\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "--self-test":
        return self_test(argv[1])
    log_dir = settings_path().parent
    handlers: list[logging.Handler] = []
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / "recorder-app.log", encoding="utf-8"))
    except OSError:
        pass
    if sys.stderr is not None:  # the windowed .exe has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = tk.Tk()
    try:  # sharper text on high-DPI Windows screens
        if platform.system() == "Windows":
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    RecorderApp(root)
    root.minsize(1000, 640)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

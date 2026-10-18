# Optical Traffic Alert

**Optisches Kollisionswarnsystem für Kleinflugzeuge** – Kameras im Cockpit, eine kompakte
KI-Box und Sprachansagen ins Intercom:

> *„Traffic, traffic, one o'clock, level, closing.“*

Referenzflugzeug ist eine Beechcraft V35A Bonanza, das System ist aber für Hoch- und Tiefdecker
ausgelegt (Profile in `config/aircraft/`).

> ⚠️ **Nicht zertifiziert. Experimentelles Hilfsmittel.** Ersetzt weder Luftraumbeobachtung
> („see and avoid“) noch zugelassene Systeme wie FLARM, ADS-B oder TAS. Siehe
> [docs/sicherheit-und-recht.md](docs/sicherheit-und-recht.md).

---

## Warum optisch?

FLARM und ADS-B sehen nur **kooperativen** Verkehr – Luftfahrzeuge, die selbst senden.
Segelflugzeuge ohne FLARM, Paraglider, Ultraleichte, Ballone und Drohnen bleiben unsichtbar.
Eine Kamera sieht alles, was sich gegen den Himmel abhebt, und sie schaut nie weg – auch nicht
während Checkliste, Funk oder Navigation.

Die eigentliche Herausforderung ist die **Distanz**: Ein Kleinflugzeug frontal (~3 m Querschnitt)
ist auf 2 km bei einer 4K-Kamera mit 50° Bildwinkel nur **etwa 5 Pixel** groß. Bei 100 m/s
Annäherung (zwei Kleinflugzeuge frontal) bleiben ab 2 km nur 20 Sekunden.

## Wie es funktioniert

```
Kamera(s) ──► Kandidaten ──► Klassifikator ──► Tracker ──► Bedrohungs- ──► Sprachansage
              (OpenCV,        (kleines CNN,     (über viele   bewertung      (Intercom)
               Echtzeit)       nur Ausschnitte)  Frames)      (Peilung + τ)
                                                     ▲
                                  IMU (Eigendrehung) ┘   ADS-B/FLARM (Abgleich, später)
```

1. **Kandidaten** – ein morphologischer Filter (Black-Hat/Top-Hat) hebt kleine, kontrastreiche
   Punkte gegen den Himmel hervor. Läuft auch in 4K in Echtzeit auf einer Laptop-CPU.
2. **Klassifikator** – ein kleines neuronales Netz bewertet nur 64×64-px-Ausschnitte um die
   Kandidaten: Flugzeug, Vogel oder Hintergrund. Läuft per **OpenVINO** auf Intel-GPUs (Iris Xe)
   und später per TensorRT auf dem Jetson.
3. **Tracker** – nur was über viele Frames konsistent gesehen wird, ist ein Ziel.
   Rauschen und Insekten verschwinden, Flecken auf der Scheibe bewegen sich nie.
4. **Bedrohungsbewertung** – die Regel, die jeder Pilot lernt: **konstante Peilung und wachsende
   Größe = Kollisionskurs**. Aus dem Größenwachstum wird die Zeit bis zur Begegnung (τ) geschätzt.
5. **Ansage** – im TCAS/FLARM-Stil, mit Regeln gegen Ansage-Flut (siehe
   [docs/architektur.md](docs/architektur.md#ansagen)).

| Stufe | Bedeutung | Ansage |
|---|---|---|
| `TRAFFIC` | bestätigtes, sich bewegendes Ziel, keine Kollisionsgeometrie | „Traffic, two o'clock, high.“ |
| `ADVISORY` | konstante Peilung, wird größer, τ < 40 s | „Traffic, two o'clock, high, closing.“ |
| `WARNING` | konstante Peilung, wird größer, τ < 20 s | „Traffic, traffic, two o'clock, high, closing.“ |

## Schnellstart (Windows-Laptop, ohne Kamera)

```powershell
git clone https://github.com/<user>/optical-traffic-alert.git
cd optical-traffic-alert
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -e .[dev,tts]

# 1. Synthetisches Testvideo erzeugen (Kollisionsziel, kreuzendes Ziel, Scheibenflecken)
ota synth data/synthetic/test.mp4

# 2. Pipeline darauf laufen lassen – mit Bild und Windows-Sprachausgabe
ota run --video data/synthetic/test.mp4 --show --audio pyttsx3

# 3. Tests
pytest
```

Erwartete Ausgabe (gekürzt):

```
Highest alert level: WARNING
Callouts:
  - Traffic, eleven o'clock, high.
  - Traffic, traffic, one o'clock, level, closing.
```

Details zur Einrichtung inkl. USB-Kamera, RTL-SDR und Intercom:
[docs/setup-windows.md](docs/setup-windows.md).

## Befehle

| Befehl | Zweck |
|---|---|
| `ota synth [datei.mp4]` | synthetisches Testvideo + Ground-Truth-JSON erzeugen |
| `ota run --video datei.mp4 [--show] [--record out.mp4]` | Pipeline auf einem Video |
| `ota run --aircraft v35a` | Live mit den Kameras aus dem Flugzeugprofil |
| `ota record --camera usb:0 --out data/raw/flug1.mp4` | Rohvideo aufzeichnen (Datensammlung) |
| `ota cameras` | angeschlossene USB-Kameras auflisten |

Wichtige Optionen von `ota run`: `--audio print|pyttsx3|piper`, `--language en|de`,
`--realtime` (Video in Echtzeit abspielen), `--stride 2` (nur jeden 2. Frame, für schwache Hardware),
`--hfov 50` (Bildwinkel der Kamera, mit der das Video aufgenommen wurde).

## Projektstruktur

```
config/
  default.yaml              Verarbeitungsparameter (alle Werte dokumentiert)
  aircraft/v35a.yaml        Kameraeinbau V35A (Tiefdecker)
  aircraft/high-wing-generic.yaml
src/ota/
  sources/                  Bildquellen: Videodatei, USB-Kamera, (GMSL2 auf Jetson)
  detection/candidates.py   Stufe 1: Kandidaten (OpenCV)
  detection/classifier.py   Stufe 2: Ausschnitt-Klassifikator (OpenVINO)
  tracking/tracker.py       Multi-Target-Tracker (Alpha-Beta-Filter)
  tracking/threat.py        Kollisionslogik: Peilungsrate, Wachstum, τ
  audio/announcer.py        Ansagen + Sprachausgabe (Konsole, Windows SAPI, Piper)
  sensors/adsb.py           ADS-B über dump1090/readsb (SBS-1, Port 30003)
  sensors/imu.py            IMU (BNO085 über USB-Mikrocontroller)
  geometry.py               Pixel → Azimut/Elevation → Uhrzeit, high/low
  pipeline.py               Verkettung, Overlay, Ereignisprotokoll
  synth.py                  Testvideo-Generator
  cli.py                    Kommandozeile `ota`
tools/
  extract_aot_crops.py      Trainingsdaten aus dem AOT-Datensatz schneiden
  train_classifier.py       Klassifikator trainieren + nach OpenVINO exportieren
tests/                      pytest, inkl. End-to-End-Test auf synthetischem Video
docs/                       Dokumentation (siehe unten)
```

## Dokumentation

| Dokument | Inhalt |
|---|---|
| [docs/architektur.md](docs/architektur.md) | Aufbau, Algorithmen, Parameter, bekannte Grenzen |
| [docs/setup-windows.md](docs/setup-windows.md) | Einrichtung auf dem Laptop: Python, Kamera, Audio, SDR |
| [docs/training.md](docs/training.md) | Klassifikator trainieren (AOT-Datensatz, GPU-Rechner) |
| [docs/kalibrierung.md](docs/kalibrierung.md) | Kameras einbauen und einmessen, Flugzeugprofile |
| [docs/hardware.md](docs/hardware.md) | Hardware, Stromversorgung, EMV, Stückliste |
| [docs/datensammlung.md](docs/datensammlung.md) | Eigene Aufnahmen am Boden und im Flug |
| [docs/roadmap.md](docs/roadmap.md) | Phasen und nächste Schritte |
| [docs/sicherheit-und-recht.md](docs/sicherheit-und-recht.md) | Grenzen, Haftung, EASA-Einordnung |

## Stand

Phase 1 (Bodenprototyp auf dem Laptop): Pipeline läuft durchgängig auf Videodateien und USB-Kameras,
erkennt im synthetischen Szenario das Kollisionsziel als `WARNING` und unterdrückt Scheibenflecken.
Der Klassifikator ist vorbereitet, aber noch nicht trainiert (`classifier.backend: none`).
Siehe [docs/roadmap.md](docs/roadmap.md).

## Lizenz

MIT – siehe [LICENSE](LICENSE).

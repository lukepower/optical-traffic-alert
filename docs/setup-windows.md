# Einrichtung auf einem Windows-Laptop

Ziel: Entwicklung und Bodentests auf einem normalen Laptop – auch **ohne NVIDIA-GPU**
(z. B. Intel Iris Xe).

## 1. Python und Projekt

1. Python 3.11 oder 3.12 von [python.org](https://www.python.org/downloads/windows/) installieren
   („Add python.exe to PATH“ anhaken).
2. Git installieren: [git-scm.com](https://git-scm.com/download/win).
3. Projekt holen und virtuelle Umgebung anlegen:

```powershell
cd F:\Programming
git clone https://github.com/<user>/optical-traffic-alert.git
cd optical-traffic-alert
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e .[dev,tts,openvino,serial]
```

Extras im Überblick:

| Extra | Inhalt | wann nötig |
|---|---|---|
| `dev` | pytest | Tests |
| `tts` | pyttsx3 | Sprachausgabe mit Windows-Stimmen |
| `openvino` | OpenVINO Runtime | sobald ein trainiertes Modell vorliegt |
| `serial` | pyserial | IMU über USB |
| `train` | Ultralytics | nur auf dem GPU-Rechner zum Trainieren |

4. Funktionstest:

```powershell
pytest
ota synth data\synthetic\test.mp4
ota run --video data\synthetic\test.mp4 --show --audio pyttsx3
```

Im Fenster `q` drücken zum Beenden.

## 2. Intel-Grafik (Iris Xe) mit OpenVINO

Die Iris Xe hat keinen eigenen Grafikspeicher – die oft angezeigten 128 MB sind nur die feste
Zuteilung, der Rest wird dynamisch aus dem Arbeitsspeicher genommen. Für den kleinen Klassifikator
reicht das völlig.

* Aktuellen Intel-Grafiktreiber installieren (Intel Driver & Support Assistant).
* Prüfen, welche Geräte OpenVINO sieht:

```powershell
python -c "import openvino as ov; print(ov.Core().available_devices)"
```

Erwartet: `['CPU', 'GPU']`. In `config/default.yaml` dann `classifier.device: GPU` oder `AUTO`.

## 3. USB-Kamera

Empfohlen für Phase 1: Waveshare **IMX678 8MP USB Camera** (siehe Stückliste). Sie ist ein UVC-Gerät
und braucht unter Windows keinen Treiber.

```powershell
ota cameras                          # findet usb:0, usb:1, ...
ota record --camera usb:0 --width 3840 --height 2160 --out data\raw\test.mp4 --seconds 30
ota run --video data\raw\test.mp4 --hfov 50 --show
```

Hinweise:

* Über USB 2.0 kommen hohe Auflösungen nur als **MJPEG** – das ist eingestellt. Die Kompression
  kostet etwas Kontrast bei sehr kleinen Zielen; für erste Tests in Ordnung.
* `--hfov` muss zum Objektiv passen, sonst stimmen Uhrzeit und Peilungsrate nicht
  (siehe [kalibrierung.md](kalibrierung.md)).
* Belichtung: möglichst kurz und fest (kein Auto-Exposure-Pumpen). Das lässt sich im Kameradialog
  von Windows bzw. über die Treibereinstellungen festlegen.

## 4. Sprachausgabe ins Intercom

**Schnell:** `--audio pyttsx3` nutzt die eingebauten Windows-Stimmen. Unter
*Einstellungen → Zeit und Sprache → Sprache* eine englische Stimme installieren (für `en`).

**Besser:** [Piper](https://github.com/rhasspy/piper) – neuronales Offline-TTS, klingt deutlich
klarer und läuft identisch später auf dem Jetson.

1. Windows-Release von Piper herunterladen und entpacken, z. B. nach `C:\tools\piper\`.
2. Eine Stimme herunterladen (`.onnx` + `.onnx.json`), z. B. `en_US-lessac-medium`, nach
   `models\tts\`.
3. In `config/default.yaml`:

```yaml
audio:
  backend: piper
  piper_executable: C:/tools/piper/piper.exe
  piper_model: models/tts/en_US-lessac-medium.onnx
```

**Anschluss ans Intercom:** Kopfhörerausgang bzw. USB-Audioadapter → Aux-Eingang des Intercoms
(Music-In). Bei Brummen einen Massetrennfilter (Ground-Loop-Isolator) dazwischen.
Lautstärke so einstellen, dass Ansagen deutlich über dem Motorgeräusch, aber unter dem Funk liegen.

## 5. ADS-B mit RTL-SDR (optional)

1. RTL-SDR Blog V4 einstecken.
2. Mit [Zadig](https://zadig.akeo.ie/) den WinUSB-Treiber für „Bulk-In, Interface (Interface 0)“
   installieren.
3. Einen dump1090-Build für Windows starten, der SBS-Ausgabe auf Port 30003 bietet, z. B.:

```powershell
dump1090.exe --interactive --net
```

Alternativ readsb in WSL2 (USB-Durchreichung mit `usbipd-win`).

4. In `config/default.yaml` `adsb.enabled: true`. (Die Fusion in der Pipeline folgt in einer späteren
   Version, der Parser ist fertig und getestet.)

## 6. IMU (optional, für Flugtests)

BNO085 an einem kleinen USB-Mikrocontroller (RP2040 oder ESP32), der Zeilen im Format

```
IMU,<gierrate>,<nickrate>,<rollrate>,<rollwinkel>,<nickwinkel>
```

mit 50–100 Hz ausgibt. Port im Gerätemanager nachsehen (z. B. `COM5`).

## Fehlersuche

| Problem | Lösung |
|---|---|
| `ota` wird nicht gefunden | virtuelle Umgebung aktiv? `.venv\Scripts\activate` |
| Kamera öffnet nicht | andere Anwendung (Teams, Kamera-App) schließen; `ota cameras` |
| nur 1920×1080 statt 4K | Kamera kann 4K evtl. nur mit niedriger fps; `--fps 15` versuchen |
| sehr langsam | `--stride 2`, `pyramid_scales: [1.0]`, kleinere Auflösung zum Testen |
| keine Sprachausgabe | mit `--audio print` prüfen, ob Ansagen erzeugt werden; dann Audio-Backend |

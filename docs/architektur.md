# Architektur

Dieses Dokument beschreibt, wie die Pipeline aufgebaut ist, warum sie so aufgebaut ist und welche
Parameter was bewirken. Alle Parameter stehen mit Standardwert in `config/default.yaml` und
`src/ota/config.py`.

## Überblick

```
             ┌────────────────────────── pro Kamera ───────────────────────────┐
FrameSource ─┤ CandidateDetector → Classifier → Tracker → ThreatAssessor ──────┼─► Announcer ─► Intercom
             └─────────────────────────────────────────────────────────────────┘        │
                         IMU (Eigendrehung) ───────────────┘                            └─► Ereignisprotokoll (JSONL)
```

Jede Stufe ist eine kleine Klasse mit klarer Schnittstelle. Dadurch läuft dieselbe Pipeline

* auf dem Windows-Laptop mit einer Videodatei oder USB-Kamera (Phase 1) und
* später auf dem Jetson mit drei GMSL2-Kameras (Phase 2),

ohne dass sich etwas außer der Bildquelle und dem Klassifikator-Backend ändert.

## Designprinzipien

1. **Billige Stufen zuerst.** Klassische Bildverarbeitung reduziert ein 8-Megapixel-Bild auf eine
   Handvoll Kandidaten; nur diese gehen durchs neuronale Netz. So reicht ein Laptop mit Intel-Grafik.
2. **Zeit schlägt Einzelbild.** Ein Ziel auf 2 km ist ein paar Pixel groß. Einzelbilder sind
   unzuverlässig, Konsistenz über viele Frames nicht.
3. **Fehlalarme sind teurer als ein verpasstes Ziel.** Ein System, das ständig falsch warnt, wird
   abgeschaltet. Deshalb: Hysterese, Mindestbeobachtungszeit, Unterdrückung statischer Objekte,
   Ansage-Regeln.
4. **Alles messbar.** Jedes Ereignis landet im JSONL-Protokoll, jeder Frame kann mit Overlay
   aufgezeichnet werden.

## Stufe 0 – Bildquellen (`src/ota/sources`)

| Quelle | Konfiguration | Hinweise |
|---|---|---|
| Videodatei | `file:pfad.mp4` | Zeitstempel aus der Bildrate der Datei; `--realtime` spielt in Echtzeit ab |
| USB-Kamera | `usb:0` | OpenCV, unter Windows DirectShow; hohe Auflösungen meist nur als MJPEG |
| GMSL2 (Jetson) | `gmsl:0` | geplant, über GStreamer |

Alle Quellen liefern `Frame(camera, index, t, image)`. `t` ist die Zeit in Sekunden – bei
Videodateien die **Videozeit**, nicht die Wanduhr. Alle zeitabhängigen Regeln (Tracker,
Bedrohung, Ansagen) arbeiten mit dieser Zeit. Ein Video wird deshalb gleich bewertet, egal
ob der Rechner es schneller oder langsamer als Echtzeit verarbeitet.

## Stufe 1 – Kandidaten (`detection/candidates.py`)

**Idee:** Ein entferntes Flugzeug ist ein kleiner Fleck, dunkler (oder bei Sonnenreflex heller)
als seine Umgebung. Eine morphologische *Closing*-Operation mit einem Kernel von
`kernel_px` entfernt alle Objekte, die kleiner als der Kernel sind – übrig bleibt der Hintergrund.
Die Differenz Hintergrund − Bild (Black-Hat) zeigt genau die kleinen dunklen Objekte;
für helle Objekte entsprechend Opening/Top-Hat.

Ablauf pro Frame:

1. **Himmelsmaske** (`sky_only`): Ein unscharfes Vorschaubild (1/8) wird mit Otsus Verfahren
   in hell (Himmel) und dunkel (Gelände) getrennt. Der Rand wird um `sky_margin_px` erodiert, damit
   die Horizontkante selbst – eine starke, texturierte Kante – nicht durchkommt. Funktioniert auch
   bei geneigtem Horizont in der Kurve.
2. **Bildpyramide** (`pyramid_scales: [1.0, 0.25]`): Die volle Auflösung findet ferne Ziele
   (wenige Pixel). Nahe Ziele sind größer als der Kernel und zerfallen dort in Teile (Rumpf,
   Flügel); die Viertel-Auflösung findet sie als Ganzes. Jede Ebene liefert nur die Objektgrößen,
   die sie gut kann; Fragmente der feinen Ebene, die in einer groben Detektion liegen, werden verworfen.
3. **Schwelle**: Mittelwert + `sigma_k` × Standardabweichung der Filterantwort, mindestens
   `min_contrast`. Die Schwelle passt sich so automatisch an Helligkeit und Rauschen an.
4. **Zusammenhangskomponenten** mit 3×3-Dilatation (verbindet Teile eines Objekts), Filter auf
   `min_area_px`/`max_area_px`.
5. **Hintergrundprüfung** (`max_background_std`): Ist der Hintergrund um den Kandidaten unruhig
   (Horizont, Wolkenkante, Gelände), wird er verworfen.
6. **Effektive Größe**: Die integrierte Filterantwort geteilt durch den Spitzenwert ergibt eine
   effektive Fläche. Sie ändert sich mit der Entfernung **stetig**, während die Pixelzahl eines
   Blobs zwischen wenigen Werten springt. Das ist entscheidend für die τ-Schätzung.

Statische Masken pro Kamera (Propellerkreis, Glareshield, Fensterrahmen) kommen aus dem
Flugzeugprofil (`masks`, normierte Rechtecke 0..1).

**Grenze Phase 1:** Mit `sky_only: true` werden nur Ziele gegen den Himmel gesucht. Ziele unter
dem Horizont vor Gelände sind deutlich schwerer (viel kleinteilige Struktur) und brauchen den
trainierten Klassifikator sowie im Flug die Bewegungskompensation. In den Bergen (Dolomiten!) ist
das relevant – siehe Roadmap.

## Stufe 2 – Klassifikator (`detection/classifier.py`)

Ein kleines CNN bewertet 64×64-px-Ausschnitte um jeden Kandidaten.

* Klassen (alphabetisch, wie Ultralytics sie sortiert): `aircraft` (0), `background` (1), `bird` (2)
* Eingabe: 1×3×64×64, RGB, 0..1; Ausgabe: Klassenwerte (Logits oder Wahrscheinlichkeiten)
* Backends: `none` (alle Kandidaten durchlassen, Standard bis ein Modell trainiert ist),
  `openvino` (Intel CPU/iGPU), später TensorRT auf dem Jetson
* Schwelle: `min_probability`

Training: [training.md](training.md).

## Stufe 3 – Tracker (`tracking/tracker.py`)

* Ein **Alpha-Beta-Filter** (konstante Geschwindigkeit) pro Track in Bildkoordinaten
  (`alpha`, `beta`).
* **Zuordnung**: gierig nach Distanz innerhalb eines Gates (`gate_px`), das mit jedem verpassten
  Frame wächst.
* Ein Track wird nach `min_hits` Zuordnungen **bestätigt** und nach `max_misses` Frames ohne
  Detektion gelöscht.
* Jeder Track speichert seine Historie (`history_s` Sekunden): Zeit, Position, Größe, Kontrast.

Bewusst einfach und ohne Abhängigkeiten. Wenn sich kreuzende Ziele zum Problem werden, kann die
Zuordnung durch den Ungarischen Algorithmus (scipy) ersetzt werden – die Schnittstelle bleibt gleich.

## Stufe 4 – Bedrohungsbewertung (`tracking/threat.py`)

Für jeden bestätigten Track über gleitende Fenster:

| Größe | Berechnung | Bedeutung |
|---|---|---|
| Peilungsrate | Steigung von Azimut und Elevation (3 s, kleinste Quadrate), minus Eigendrehung aus der IMU | ≈ 0 → konstante Peilung |
| Wachstumsrate | Theil-Sen-Steigung der Größe / mittlere Größe (5 s) | relative Größenzunahme pro Sekunde |
| Trend | Kendalls Rangkorrelation Größe ↔ Zeit | 1 = wächst stetig, ≈ 0 = Rauschen |
| τ | 1 / Wachstumsrate | geschätzte Zeit bis zur Begegnung |

**Warum robuste Statistik?** Blobgrößen von 2–5 px springen zwischen wenigen Werten. Eine normale
Regression findet darin zufällig „Wachstum“ – im Test mit Geländeclutter führte das zu ständigen
Fehlwarnungen. Theil-Sen und Kendall sind gegen Ausreißer und Sprünge unempfindlich.

τ wird nur akzeptiert, wenn

* der Trend ≥ `min_growth_trend` ist **und**
* die Größe im Fenster um mindestens `min_size_change_px` zugenommen hat **und**
* die Wachstumsrate > `min_growth_rate` ist.

**Stufen:**

* `NONE` – zu jung (`min_track_age_s`) oder **statisch**: bewegt sich nicht
  (`static_max_rate_dps`) und ist nie gewachsen. Das sind Scheibenflecken, Insekten-Reste,
  tote Pixel.
* `TRAFFIC` – bestätigtes, sich bewegendes Ziel.
* `ADVISORY` – konstante Peilung (≤ `max_bearing_rate_dps`) und τ ≤ `advisory_tau_s`.
* `WARNING` – konstante Peilung und τ ≤ `warning_tau_s`.

**Hysterese:** Eine Kollisionsgeometrie muss `threat_hold_s` lang bestehen, bevor sie gemeldet
wird, und eine gemeldete Bedrohung bleibt `threat_release_s` lang stehen, auch wenn einzelne
Frames schwächer sind.

**Physikalische Grenze:** Ein Ziel auf Kollisionskurs wächst lange Zeit kaum messbar. Bei 3 px
Größe und τ = 30 s wächst es um 0,1 px pro Sekunde. Die Warnung kommt deshalb typischerweise
10–20 s vor der Begegnung – genug zum Ausweichen, aber nicht früher. Längere Brennweite
(schmaleres FOV) verschiebt das nach vorn.

### Eigendrehung (IMU)

Dreht das eigene Flugzeug, wandern alle Ziele im Bild. Ohne Korrektur würde jede Kurve die
„konstante Peilung“ zerstören bzw. vortäuschen. Die IMU liefert Gier- und Nickrate; sie werden
zur gemessenen Peilungsrate addiert (`ego_rates_dps`). Am Boden mit fester Kamera ist die IMU
nicht nötig (`NullImu`).

## Geometrie (`geometry.py`)

Lochkameramodell: Brennweite in Pixeln aus `hfov_deg` und Bildbreite; Einbaulage aus `yaw_deg`,
`pitch_deg`, `roll_deg`. Ergebnis: Azimut (0° = voraus, + = rechts) und Elevation.

* Uhrzeit: 30° pro Stunde, 12 = voraus, 3 = rechts, 9 = links
* high/low/level: Elevation außerhalb ±`level_band_deg` (derzeit relativ zur Flugzeuglängsachse;
  mit IMU-Lage später relativ zum Horizont)

Linsenverzeichnung wird ignoriert. Bei 40–60° Objektiven ist der Fehler klein gegenüber der
Auflösung einer Uhrzeit (30°).

## Ansagen

`audio/announcer.py` entscheidet, **was** und **wann** gesagt wird; gesprochen wird in einem eigenen
Thread, damit die Bildverarbeitung nie auf Audio wartet.

Regeln:

* höchstens eine Ansage alle `min_gap_s` Sekunden – außer bei `WARNING`
* derselbe Track nur erneut, wenn die Stufe steigt, die Uhrzeit wechselt oder `repeat_s` vergangen ist
* derselbe **Sektor** (Uhrzeit + high/level/low) nicht erneut innerhalb `repeat_s`, außer die Stufe
  steigt – ein Ziel, das kurzzeitig in zwei Tracks zerfällt, erzeugt so keine Doppelansage
* bei mehreren Kandidaten gewinnt die höchste Stufe, dann das kleinste τ

Sprachausgabe:

| Backend | Plattform | Hinweis |
|---|---|---|
| `print` | überall | nur Konsole |
| `pyttsx3` | Windows | nutzt die eingebauten SAPI-Stimmen, `pip install -e .[tts]` |
| `piper` | Windows, Linux, Jetson | neuronales Offline-TTS, gute Verständlichkeit, braucht Programm + Stimme |

Sprachen: `en` (Standard, Luftfahrtphraseologie) und `de`.

## ADS-B / FLARM (`sensors/adsb.py`)

dump1090 oder readsb liefern über TCP-Port 30003 das SBS-1-Format. `SbsParser` hält den letzten
Zustand pro ICAO-Adresse, `relative_position()` rechnet Peilung relativ zum eigenen Kurs, Distanz
und Höhendifferenz.

Geplante Nutzung:

1. **Abgleich**: Optischer Track und ADS-B-Ziel innerhalb `match_tolerance_deg` → kooperativer
   Verkehr, steht vermutlich schon auf dem EFB/FLARM-Display. Ansage ggf. abschwächen.
2. **Wahrheit für Tests**: ADS-B liefert echte Distanzen – damit lässt sich die optische
   τ-Schätzung in Testflügen validieren.

FLARM (868 MHz) kommt mit einem zweiten SDR und OGN-Werkzeugen in dieselbe `TrafficTarget`-Struktur.

## Ereignisprotokoll

`output.event_log` (Standard `out/events.jsonl`), eine JSON-Zeile pro Ereignis:

```json
{"kind": "threat", "t": 17.7, "track_id": 21, "camera": "front", "level": "WARNING",
 "azimuth_deg": 20.0, "elevation_deg": 1.0, "clock": 1, "vertical": "level",
 "bearing_rate_dps": 0.002, "growth_rate": 0.18, "tau_s": 5.6, "size_px": 3.2}
{"kind": "callout", "t": 17.7, "text": "Traffic, traffic, one o'clock, level, closing."}
```

## Leistung

Gemessen in der Cloud-Testumgebung (CPU, 1920×1080, ohne Klassifikator): ca. 60 ms pro Frame
(≈ 16 fps). Auf einem Laptop mit Intel Core i5/i7 der 11.–13. Generation ist ein ähnlicher Wert zu
erwarten; 4K kostet etwa das Vierfache. Stellschrauben: `frame_stride`, kleinerer `kernel_px`,
`pyramid_scales: [1.0]`, Masken.

## Bekannte Grenzen (Stand Phase 1)

* nur Ziele gegen den Himmel (`sky_only`)
* Klassifikator noch nicht trainiert → Vögel lösen `TRAFFIC` aus
* keine Linsenverzeichnung, keine Lagekorrektur für high/low
* Kameras werden in einem Thread nacheinander verarbeitet
* gegen die Sonne und bei starkem Dunst sinkt die Reichweite deutlich

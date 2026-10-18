# Roadmap

## Phase 1 – Bodenprototyp auf dem Laptop

- [x] Projektgerüst, Konfiguration, Flugzeugprofile (V35A, Hochdecker)
- [x] Bildquellen: Videodatei, USB-Kamera
- [x] Kandidatendetektor (Morphologie, Bildpyramide, Himmelsmaske)
- [x] Tracker (Alpha-Beta, Bestätigung, Verlust)
- [x] Bedrohungsbewertung (Peilungsrate, robustes Wachstum, τ, Hysterese, statische Objekte)
- [x] Ansagen (EN/DE, Rate-Limits, Sektor-Deduplizierung), Windows-SAPI und Piper
- [x] Synthetischer Testvideo-Generator, End-to-End-Test
- [x] ADS-B-Parser (SBS-1), IMU-Parser
- [ ] Eigene Bodenaufnahmen am Flugplatz sammeln
- [ ] Klassifikator auf AOT + eigenen Daten trainieren, OpenVINO auf der Iris Xe
- [ ] ADS-B-Abgleich in der Pipeline (kooperativ / nicht kooperativ)
- [ ] Auswerte-Skript: Ereignisprotokoll gegen ADS-B-Wahrheit

## Phase 2 – Flugsystem auf dem Jetson

- [ ] GMSL2-Quelle über GStreamer, ein Capture-Thread pro Kamera
- [ ] Klassifikator nach TensorRT
- [ ] IMU-Integration: Eigendrehung, high/low relativ zum Horizont
- [ ] Automatische Kalibrierung der Kameralage aus Horizont + IMU
- [ ] Stromversorgung, Gehäuse, EMV-Test
- [ ] Erste Flugtests als reiner Rekorder, dann mit Ansagen

## Phase 3 – Erweiterungen

- [ ] Ziele unter dem Horizont (Gelände als Hintergrund, Bewegungskompensation)
- [ ] Temporaler Klassifikator (Bewegungsmuster: Vogel vs. Flugzeug)
- [ ] FLARM-Empfang (868 MHz, OGN) in die Fusion
- [ ] Entfernungsschätzung aus Flugzeugtyp/Spannweite
- [ ] Linsenverzeichnung, Sonnen-/Gegenlichterkennung

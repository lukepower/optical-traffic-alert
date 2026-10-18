# Datensammlung

Eigene Aufnahmen sind das wertvollste Material: Sie zeigen genau die Kamera, die Scheibe, das
Licht und die Landschaft, mit denen das System später arbeiten muss.

## Am Boden

**Ort:** Vorfeld oder Platzrand eines Flugplatzes mit Verkehr (z. B. in Richtung Endanflug),
mit Genehmigung des Platzhalters. Anfliegender Verkehr ist ideales Testmaterial: ein Ziel, das
direkt auf die Kamera zukommt und dabei wächst – genau die Kollisionsgeometrie.

```powershell
ota record --camera usb:0 --width 3840 --height 2160 --fps 30 --out data\raw\lipb_2026-10-20_rwy19.mp4 --seconds 600
```

Zu jeder Aufnahme notieren (z. B. in `data/raw/README.md`, nicht im Repo):

* Datum, Uhrzeit, Ort, Blickrichtung
* Objektiv / Bildwinkel
* Wetter, Sonnenstand
* bekannte Flugbewegungen (Uhrzeit, Kennzeichen) – ADS-B-Aufzeichnung parallel ist ideal

## Im Flug (Phase 1: Laptop als Rekorder)

* Kamera hinter der Scheibe fixieren, Gegenlichtblende.
* Laptop sicher verstauen, nichts darf bei Turbulenz lose sein.
* Nur aufzeichnen, nicht warnen – die Auswertung erfolgt am Boden.
* Parallel ADS-B aufzeichnen (dump1090 Logs) für Wahrheitsdaten.
* Die Bedienung darf die Flugdurchführung nicht beeinträchtigen: Aufnahme vor dem Anlassen
  starten, nach dem Abstellen beenden.

## Auswerten

```powershell
ota run --video data\raw\flug1.mp4 --hfov 50 --record out\flug1_overlay.mp4
```

* `out\flug1_overlay.mp4` ansehen: Wurden die bekannten Flugzeuge gefunden? Wann?
* `out\events.jsonl` mit ADS-B-Daten vergleichen: Stimmt die Uhrzeit-Angabe, wie gut ist τ?
* Fehlkandidaten (Reflexe, Wolkenkanten) als `background`-Ausschnitte für das Training sichern.

## Datenschutz

Aufnahmen können Personen, Kennzeichen und Grundstücke zeigen. Rohvideos bleiben lokal
(`data/` ist in `.gitignore`), veröffentlicht werden höchstens ausgewählte Ausschnitte.

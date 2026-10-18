# Hardware

Die vollständige Stückliste mit Preisen liegt als Tabelle in
[hardware/Stueckliste_Traffic_Alert.xlsx](hardware/Stueckliste_Traffic_Alert.xlsx) (Stand Oktober 2026).

## Phase 1 – Laptop (Bodentests)

| Komponente | Beispiel | ca. Preis |
|---|---|---|
| Laptop | vorhanden (Intel Iris Xe reicht) | – |
| USB-Kamera | Waveshare IMX678 8MP USB Camera (A) | 60 € |
| Objektive M12, schmal (8 mm, 12 mm) | diverse | 2 × 30 € |
| SDR für ADS-B/FLARM | RTL-SDR Blog V4 (+ Antennen) | 2 × 45 € |
| Audio | USB-Audioadapter, Intercom-Kabel, Massetrennfilter | 65 € |

Damit lässt sich die komplette Pipeline entwickeln und am Boden testen, für rund 150–250 €.

## Phase 2 – Flugsystem

| Komponente | Beispiel | Hinweis |
|---|---|---|
| Rechner | NVIDIA Jetson Orin NX 16 GB (Carrier mit 9–20 V Eingang) | 15–40 W je nach Modus |
| Kameras | 3 × GMSL2, z. B. Basler ace 2 a2A3536-37mgm (IMX676, 12,5 MP, mono) | Versorgung über Koax (PoC) |
| Deserializer | GMSL2-Board für bis zu 4 Kameras (z. B. Forecr DSADDON-GMSL-NX) | |
| Kabel | FAKRA-Koax, 3 m | |
| IMU | Adafruit BNO085 | über USB-Mikrocontroller |
| GNSS | u-blox M10 | eigene Position für ADS-B-Fusion |
| Speicher | NVMe 1 TB | Aufzeichnung |
| Gehäuse | Alu mit Lüfter | Cockpit wird im Sommer heiß |

**Warum GMSL2?** MIPI-Kabel funktionieren nur auf ~30 cm. Kameras an verschiedenen Stellen im
Cockpit brauchen lange, störfeste Leitungen – GMSL2 überträgt Bild und Strom über ein Koaxkabel.

**Warum monochrom?** Ein Mono-Sensor hat ohne Farbfilter mehr Licht pro Pixel und mehr effektive
Auflösung – beides zählt bei Zielen von wenigen Pixeln mehr als Farbe.

**Global Shutter vs. Rolling Shutter:** Global Shutter vermeidet Propeller-Artefakte und
Vibrationsverzerrungen, ist bei hoher Auflösung aber teuer. Rolling Shutter mit kurzer Belichtung
funktioniert in der Praxis; die Propellerartefakte werden maskiert bzw. vom Tracker überbrückt.

## Stromversorgung (14-V-Bordnetz)

* Bordnetz real 12–14,5 V, beim Anlassen kurz unter 9 V, Spitzen beim Schalten großer Verbraucher.
* **Immer über einen DC/DC-Wandler** mit weitem Eingangsbereich (9–36 V), isoliert, mit EMV-Filter.
  Er liefert stabile 12 V bzw. 19 V und passt auch für 28-V-Flugzeuge.
* Leistungsbedarf gesamt ca. 40–60 W → 3–4,5 A bei 14 V.
* Anschluss über eine Bordsteckdose mit ausreichender Absicherung hält das Gerät portabel.
  Eine feste Verkabelung am Bus ist eine Änderung am Luftfahrzeug.
* Für erste Testflüge: LiFePO4-Akku (12 V) – komplett getrennt vom Bordnetz.

## EMV

Vor dem ersten Flug am Boden bei laufendem Motor prüfen:

* COM: Rauschen/Pfeifen auf mehreren Frequenzen, Squelch-Öffnen?
* NAV/GPS: Signalverlust, Flaggen?
* Kompass: Ablenkung durch Gehäuse und Kabel?

Typische Störquellen: Schaltregler des Jetson und des Wandlers, GMSL-Leitungen, USB 3.
Gegenmittel: Ferrite, kurze Leitungen, Abstand zu Antennenkabeln, metallisches Gehäuse.

# OTA Recorder – Kurzanleitung für Vereinsmitglieder

<img src="images/logo-icon.png" width="96" align="right">

Der **OTA Recorder** zeichnet ein Kameravideo auf und gleichzeitig die Positionen aller
Luftfahrzeuge in der Umgebung (ADS-B, FLARM, OGN). Mit diesen Aufnahmen messen wir, aus welcher
Entfernung die Kamera Flugzeuge erkennt. Weil die Positionen mitgeschrieben werden, muss niemand
etwas von Hand markieren.

Ausführliche Anleitung (Englisch): [recorder.md](recorder.md).

## Installation

1. `OTA-Recorder-windows.zip` von der
   [Releases-Seite](https://github.com/lukepower/optical-traffic-alert/releases) herunterladen.
2. Entpacken, z. B. nach `C:\OTA-Recorder`.
3. `OTA-Recorder.exe` starten. Falls Windows warnt: **Weitere Informationen → Trotzdem ausführen**.

Python oder andere Programme sind nicht nötig.

## Am Flugplatz

1. **Erlaubnis** des Platzhalters einholen. Einen Standort mit freier Sicht auf Anflug oder
   Platzrunde wählen. Nicht im Weg von rollendem Verkehr stehen.
2. Kamera **fest auf ein Stativ**. Die Sonne darf nicht im Bild sein.
3. Kamera und SDR-Stick anstecken, dump1090 starten und Internet verbinden (Handy-Hotspot genügt).
4. Programm starten und die Einstellungen prüfen:
   * **Aufnahme:** Name, Ort, Breite/Länge (Google Maps: Rechtsklick auf den Standort), Höhe der
     Kamera über Meer, **QNH** aus dem METAR.
   * **Kamera:** **Blickrichtung** in Grad (Handykompass plus etwa 3–4°; ungefähr genügt, die
     Auswertung korrigiert sie), **Neigung** nach oben, **Bildwinkel**.
   * **Verkehrsdaten:** ADS-B `127.0.0.1:30003`, „FLARM/OGN aus dem Internet“ ein, FLARM-Gerät
     falls vorhanden (z. B. `COM4@19200`).
   * **Aufnahme:** „Nur bei Verkehr/Bewegung“ lassen (spart 80–90 % Speicher). Häkchen bei
     „Auch bei Bewegung im Bild“ lassen, damit auch Flugzeuge ohne FLARM erfasst werden.
5. **Vorschau** drücken. Auf etwas weit Entferntes scharf stellen, bis der Wert **Schärfe**
   maximal ist. Ist der Himmel überbelichtet, unter *Belichtung* einen Wert eintragen
   (z. B. `-8`, kleiner = dunkler).
6. **Aufnahme starten.** Nach wenigen Sekunden müssen die Bildrate stimmen und die Quellen
   *verbunden* anzeigen. Gelb markierte Luftfahrzeuge sind gerade im Bild.
7. 30–60 Minuten laufen lassen. Danach **Aufnahme beenden**.

## Abgeben

Bitte den **ganzen Sitzungsordner** (z. B. `20261020-074512Z_LIPB`) auf USB-Stick oder über
eine Cloud weitergeben. Im Standardmodus speichert das Programm nur die Abschnitte mit Verkehr
oder Bewegung (je 20 s davor und danach) und sonst nur ein Bild alle 3 Sekunden. Eine Stunde
ergibt so meist 5–20 GB statt 60–120 GB. Besonderheiten gern
dazuschreiben, etwa Segelflieger ohne FLARM, Gleitschirme, Drohnen, Vögel oder Wetter.

## Datenschutz

Videos können Personen und Kennzeichen zeigen und werden nicht veröffentlicht. Luftfahrzeuge mit
gesetztem *No-Tracking*-Flag werden nicht aufgezeichnet.

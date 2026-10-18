# Sicherheit, Grenzen und rechtliche Einordnung

## Was dieses System ist – und was nicht

* Ein **experimentelles, nicht zertifiziertes** Hilfsmittel.
* **Kein Ersatz** für Luftraumbeobachtung, FLARM, ADS-B, TAS/TCAS oder Fluginformationsdienst.
* Es kann Ziele **übersehen** (Gegenlicht, Dunst, Ziele vor Gelände, außerhalb der Kamerasektoren,
  im toten Winkel von Fläche und Rumpf).
* Es kann **falsch warnen** (Vögel, Reflexe, Wolkenränder).

Das Fehlen einer Ansage bedeutet **niemals**, dass kein Verkehr da ist.

## Risiken im Betrieb

| Risiko | Gegenmaßnahme |
|---|---|
| Ablenkung durch Fehlalarme | konservative Schwellen, Ansageregeln, Lautstärke unter Funk |
| Verlassen auf das System („Automation Bias“) | bewusst als Ergänzung verstehen; Scan-Technik beibehalten |
| Störung von COM/NAV (EMV) | EMV-Test am Boden, Wandler mit Filter, Abschaltbarkeit |
| Lose Gegenstände im Cockpit | feste Halterungen, Kabelführung, nichts im Bereich der Steuerung |
| Wärme, Akku | Gehäuse mit Lüfter; LiFePO4 statt Li-Ion für Testakkus |
| Sichtbehinderung | Kameras außerhalb des primären Sichtfelds, kleine Gehäuse |

## Rechtliche Einordnung (EASA, ohne Gewähr)

> Keine Rechtsberatung. Vor Einsatz im Flugzeug mit einer zuständigen Stelle bzw. einem
> Prüfer/CAMO klären.

* **Portabel, im Cockpit befestigt, eigene Stromversorgung oder Bordsteckdose:** wird in der Praxis
  meist wie ein tragbares elektronisches Gerät (PED) behandelt. Der verantwortliche Pilot muss
  sicherstellen, dass es die Flugsicherheit nicht beeinträchtigt (Störungen, Befestigung).
* **Fest eingebaute Kameras, Außenmontage, Anschluss an den Bus oder an das Audio-Panel durch
  Eingriff in die Verkabelung:** Änderung am Luftfahrzeug → je nach Umfang Minor Change bzw.
  Standard Change (CS-STAN) mit entsprechender Freigabe.
* **Aufnahmen**: Datenschutz und ggf. Regeln von Flugplatzbetreibern beachten
  (siehe [datensammlung.md](datensammlung.md)).

## Haftung

Die Software wird unter der MIT-Lizenz „wie besehen“ bereitgestellt, ohne jede Gewährleistung.
Verantwortlich für die Flugdurchführung bleibt der Pilot.

# Kameras einbauen und einmessen

Damit die Ansage „two o'clock, high“ stimmt, muss das System wissen, wohin jede Kamera schaut und
wie groß ihr Bildwinkel ist. Das steht im Flugzeugprofil `config/aircraft/<typ>.yaml`.

## Felder pro Kamera

| Feld | Bedeutung |
|---|---|
| `name` | frei wählbar, erscheint in Protokoll und Overlay |
| `source` | `usb:0`, `file:…`, später `gmsl:0` |
| `width`, `height`, `fps` | angeforderte Auflösung |
| `hfov_deg` | horizontaler Bildwinkel des Objektivs |
| `yaw_deg` | Blickrichtung horizontal: 0 = voraus, + = rechts, − = links |
| `pitch_deg` | Blickrichtung vertikal: + = nach oben |
| `roll_deg` | Verdrehung um die optische Achse (+ = rechte Seite nach unten) |
| `masks` | Rechtecke in normierten Koordinaten (0..1), die ignoriert werden |

## Platzierung

**Tiefdecker (V35A):**

* Front: mittig auf dem Glareshield, 0 bis +5° nach oben, schmales Objektiv (~50°).
  Die Propellerscheibe ist im Bild – bei kurzer Belichtung erscheinen Blätter als stehende Balken
  in einzelnen Frames. Der Tracker überbrückt das; bei Bedarf Maske setzen.
* Seiten: an den Seitenscheiben, 45–60° seitlich nach vorne, leicht nach oben (+5°), weil die
  Fläche den Blick nach unten verdeckt.

**Hochdecker:**

* Front wie oben.
* Seiten eher leicht nach unten (−3°), weil die Fläche den Blick nach oben verdeckt; Flügelwurzel
  und Türrahmen oben maskieren.

**Gegen Reflexionen:** Gegenlichtblende direkt an die Scheibe, matt schwarz; ggf. Polfilter.
Saubere Scheibe – Flecken werden zwar unterdrückt, kosten aber Kontrast.

## Bildwinkel bestimmen

Herstellerangaben sind oft diagonal oder ungenau. Einfache Messung:

1. Kamera auf ein Objekt mit bekannter Breite `B` in bekannter Entfernung `D` richten
   (z. B. Hangartor), Objekt füllt einen Teil des Bildes.
2. Breite im Bild in Pixeln messen: `b`.
3. Brennweite in Pixeln: `f = b * D / B`
4. `hfov = 2 * atan((Bildbreite / 2) / f)`

Beispiel: 4K-Bild (3840 px), Tor 12 m breit in 100 m Entfernung erscheint 1000 px breit →
f = 1000 · 100 / 12 = 8333 px → hfov = 2 · atan(1920 / 8333) ≈ 26°.

## Einbaulage bestimmen

**Am Boden:** Das Flugzeug in Fluglage zu bringen ist bei Bugradflugzeugen schwierig; für Phase 1
reicht: Kamera so ausrichten, dass die Mittellinie (z. B. Spinner bzw. Nase) auf der Bildmitte
liegt → `yaw_deg: 0`. Seitenkameras: Flächenspitze oder ein Peilpunkt in bekannter Richtung.

**Im Flug (später automatisch):** Der Horizont im Bild gibt Nick- und Rollwinkel der Kamera
relativ zur IMU. Geplant ist eine Selbstkalibrierung in ruhigem Geradeausflug.

## Masken

Ein Overlay-Video zeigt, wo Kandidaten auftreten:

```powershell
ota run --video data\raw\boden1.mp4 --record out\boden1_overlay.mp4
```

Wo dauerhaft graue Kreise (unbestätigte Tracks) auf Rahmen, Glareshield oder Propeller sitzen,
ein Rechteck in `masks` eintragen. Koordinaten sind Anteile der Bildbreite/-höhe:
`{x0: 0.0, y0: 0.88, x1: 1.0, y1: 1.0}` = unterste 12 % des Bildes.

## Neues Flugzeugprofil

`config/aircraft/v35a.yaml` kopieren, umbenennen, Werte anpassen, dann:

```powershell
ota run --aircraft mein-flugzeug --show
```

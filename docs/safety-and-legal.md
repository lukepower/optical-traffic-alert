# Safety, limitations and legal considerations

## What this system is – and what it is not

* An **experimental, uncertified** aid.
* **No replacement** for see-and-avoid, FLARM, ADS-B, TAS/TCAS or flight information services.
* It can **miss** targets (against the sun, in haze, in front of terrain, outside the camera sectors,
  in the blind spots of wing and fuselage).
* It can **false-alarm** (birds, reflections, cloud edges).

The absence of a callout **never** means there is no traffic.

## Operational risks

| Risk | Mitigation |
|---|---|
| Distraction by false alarms | conservative thresholds, callout rules, volume below radio |
| Over-reliance ("automation bias") | treat it as a supplement; keep your scan technique |
| Interference with COM/NAV (EMC) | EMC test on the ground, filtered converter, easy to switch off |
| Loose items in the cockpit | rigid mounts, cable routing, nothing near the controls |
| Heat, batteries | enclosure with fan; LiFePO4 instead of Li-ion for test batteries |
| Obstructed view | cameras outside the primary field of view, small enclosures |

## Legal considerations (EASA, no warranty)

> Not legal advice. Clarify with the competent authority or your inspector/CAMO before using it in
> an aircraft.

* **Portable, secured in the cockpit, own power or cabin power socket:** in practice usually treated
  like a portable electronic device (PED). The pilot-in-command must ensure it does not affect flight
  safety (interference, securing).
* **Permanently installed cameras, external mounting, connection to the bus or to the audio panel by
  modifying the wiring:** a modification of the aircraft → depending on scope a minor change or a
  standard change (CS-STAN) with the corresponding release.
* **Recordings:** observe privacy rules and any aerodrome operator rules
  (see [data-collection.md](data-collection.md)).

## Liability

The software is provided under the MIT license "as is", without any warranty. The pilot remains
responsible for the conduct of the flight.

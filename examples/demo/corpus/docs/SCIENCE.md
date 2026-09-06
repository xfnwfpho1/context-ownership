# Aurora Station — Science Program

The science program runs continuously through the season, structured
so that no single instrument failure can take down the whole
observation record. Instruments draw ~1.2 kW average (POWER.md's load
budget) and produce ~1.2 GB of data per day, which fits one 08:00
contact window with room to spare at 2 Mbit/s (COMMS.md owns the
bandwidth budget).

## Instrument table

| Instrument | Cadence / duty | Owner |
|---|---|---|
| Meteorological station | 10-minute logging cadence | met lead |
| All-sky aurora imager | 30% duty cycle, dark hours only | aeronomy lead |
| VHF riometer | Continuous, 1 Hz sampling | aeronomy lead |
| GPS glaciology stakes (2) | Daily read at the 12:00 round | glaciology lead |
| Fluxgate magnetometer | 1 Hz, continuous | aeronomy lead |

## Data pipeline

1. Instruments buffer locally for 24 hours minimum (48 hours for the
   riometer, whose data is small).
2. The 12:00 midday round (MISSION.md owns the daily-cycle timing)
   collects stake reads and checks every buffer's freshness.
3. The 08:00 contact window downlinks the previous day's payload; the
   00:00 autopilot flushes whatever the 08:00 left behind (COMMS.md's
   queue discipline).
4. At closing (March), the full season archive is checksummed and
   duplicated into the physical mail sack on the final convoy
   (LOGISTICS.md's closing manifest).

## Duty-cycle policy

During a forecast multi-day storm, the imager's 30% duty cycle may be
reduced first — the station leader approves the reduction on POWER.md's
load-shed order (imager before heat and comms, never after). The
meteorological station and the riometer are never duty-cycled: they
are the two instruments whose gaps cannot be reconstructed.

## Quality flags

Every downlink carries a per-instrument quality flag: green (fresh
and complete), amber (buffer gaps under 2 hours), red (gap over 2
hours or stale read). Amber trends lasting more than 3 days trigger a
spares review on the next convoy request to the logistics coordinator.

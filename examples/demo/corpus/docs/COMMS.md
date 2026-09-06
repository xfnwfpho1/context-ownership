# Aurora Station — Communications

Primary link: polar-orbiting store-and-forward satellites. **The
station gets 3 satellite passes per day** (cadence authority:
MISSION.md). Each pass gives 40 minutes of usable contact at 2 Mbit/s,
which is the planning number for every queue budget below.

## Pass discipline

| Window (UTC) | Role |
|---|---|
| 08:00 | Overnight science data + ops/safety telemetry downlink |
| 16:00 | Instructions uplink, weather forecasts, telemedicine |
| 00:00 | Autopilot: backlog flush, data-mirror sync |

The comms operator runs a radio check at the top of every pass and a
full link test (loopback + BER) once per day on the 16:00 window. If a
pass is missed, its queue keeps priority into the next window — the
queues never reset, they only drain.

## Priority ladder (when the uplink queue overflows)

1. Distress and flight-request traffic
2. Medical (telemedicine, consults)
3. Flight planning and weather
4. Science data
5. Routine ops, logistics, personal traffic

Any injury beyond first-aid jumps the queue to tier 2 automatically
(SAFETY.md's medical rules); a floor breach or safety incident rides
tier 1 with the distress flag set (SAFETY.md).

## Bandwidth budget

- Overnight science payload: ~1.2 GB/day (SCIENCE.md owns the volume;
  it fits comfortably in one 08:00 pass at 2 Mbit/s).
- Ops telemetry: ~80 MB/day.
- Mirror sync (00:00): whatever the day left behind — the autopilot
  sends until the pass ends, then hands the residue to the next 08:00.

## Spares and failure

Two spare LNB units and one spare modulator are held on station,
rotated into the spares pool by each convoy (LOGISTICS.md owns the
spares manifest). If both the primary and backup links fail, the
station switches to HF voice check-ins at the same window times — the
cadence never changes, only the medium does.

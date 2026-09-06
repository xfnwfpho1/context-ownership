# Aurora Station — Safety

## Hard limits (non-negotiable)

1. **Battery reserve floor of 25% is a hard safety limit.** POWER.md
   states it as operating discipline; this document elevates it to a
   safety limit. Below 25% state of charge the station cannot guarantee
   heat through a generator-start failure in a storm — POWER.md works
   out the 4-hour full-load margin that makes 25% the number. If the
   floor is breached: start the generator immediately, log the breach
   before the next contact window closes, and run the cold-start
   procedure below.
2. Minimum interior temperature **+4°C** in all habitable modules, at
   all times. Heat is the last load ever shed (POWER.md's load-shed
   order ends before it).
3. Evacuation muster completes within **10 minutes** of alarm, with
   headcount called on the muster line.

## Cold-start procedure (after a floor breach)

1. Generator on, all modules on generator feed.
2. Verify interior temperature trend is positive within 20 minutes.
3. Recharge battery to 55% before returning the station to
   turbine-first operation.
4. Station leader files the incident report on the next contact
   window; the window cadence itself is owned by MISSION.md.

## Fire

- Detection: optical and heat sensors in all modules, 60-second
  self-test at the 05:30 power-up check.
- Suppression: FM-200 in the power module, dry-powder elsewhere.
- Post-fire: full evacuation; re-entry only under the two-person rule
  after atmosphere check.

## Medical

- Med kit class: remote-advanced; restocked each convoy (LOGISTICS.md
  owns the convoy calendar).
- Telemedicine consults ride the 16:00 contact window; any injury
  beyond first-aid automatically takes flight-request priority over
  all other traffic (COMMS.md's priority ladder).

## Storm rules

- Wind above 25 m/s: turbine braked, outdoor work suspended, tether
  rule in effect on all inter-module movement.
- Whiteout: rope-lines only, no solo movement, ever.
- Every storm, near-miss, and rule bend is logged before the next
  contact window closes — the safety log leaves the station on the
  08:00 downlink without exception.

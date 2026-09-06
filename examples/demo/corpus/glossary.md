# Glossary — Aurora Station (SHARED)

> This file is shared by ALL owners. Term meanings are authoritative
> here; value owners are listed per term. On any conflict between a
> doc and this glossary, the glossary's meaning wins and the listed
> value owner's numbers win.

## Canonical terms

| Term | Meaning | Value owner |
|---|---|---|
| **contact window** | A store-and-forward satellite contact long enough to drain the day's queues. Cadence and timing are owned by the mission charter. | MISSION.md |
| **pass** | Same as a contact window, counted per day by the comms plan; the pass schedule and queue budgets are owned by the comms doc. | COMMS.md |
| **reserve floor** | Minimum battery state of charge below which the station cannot guarantee heat through a generator-start failure. A hard safety limit. | SAFETY.md |
| **battery discipline** | The operating rules for turbine-first, battery-shaped, generator-last-resort power management. | POWER.md |
| **rotation** | Crew change cycle with a hand-over overlap; the on-ice limit is a charter limit. | MISSION.md |
| **deep season** | November–February, full science program and peak instrument duty. | MISSION.md |
| **convoy** | Over-ice resupply run with sled payload and fuel uplift limits. | LOGISTICS.md |
| **fresh-food floor** | Minimum days of fresh food that must be on station at all times. | LOGISTICS.md |
| **tether rule** | Inter-module movement rule in effect whenever winds exceed the turbine cut-out. | SAFETY.md |
| **cold-start procedure** | The recovery ladder run after any reserve-floor breach. | SAFETY.md |
| **queue discipline** | Uplink queues keep priority across missed passes; they drain, never reset. | COMMS.md |
| **priority ladder** | The ordering of uplink traffic when the queue overflows; medical and safety jump tiers automatically. | COMMS.md |
| **midday round** | The instrument round that collects stake reads and checks buffer freshness. | SCIENCE.md |
| **quality flags** | Per-instrument freshness grading carried on every downlink. | SCIENCE.md |
| **load-shed order** | The fixed sequence in which loads may be shed when turbine output is forecast offline; heat and comms are never shed. | POWER.md |
| **spares manifest** | The rotation list for consumable spares carried by each convoy. | LOGISTICS.md |

## Precedence

1. Safety limits (SAFETY.md) outrank every other consideration.
2. Cadence values follow MISSION.md even when subsystem documents
   restate them in local units.
3. Subsystem numbers (power, comms bandwidth, payload, instrument
   duty) follow their value owner as listed above.
4. The glossary's term meanings are authoritative over any doc's
   shorthand.

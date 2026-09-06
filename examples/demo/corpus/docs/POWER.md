# Aurora Station — Power and Heat

The power plant is a 12 kW horizontal-axis wind turbine, a 240 kWh
lithium-iron-phosphate battery bank, and a 40 kVA diesel generator for
top-up and backup. The design philosophy is turbine-first,
battery-shaped, generator as the last resort: every litre of fuel
flown in costs convoy payload (see LOGISTICS.md for fuel uplift
limits), so the station burns fuel only when the weather forces it.

## Battery discipline

- **Never draw the battery below 25% reserve.** The floor exists
  because station heat draws from the battery overnight while the
  turbine is iced or braked; at 25% state of charge the station holds
  roughly 4 hours of full-load margin to bring the generator online
  during a storm. SAFETY.md elevates this same floor to a hard safety
  limit and owns the cold-start procedure that follows a floor breach.
- Generator top-up: 4 hours per day nominal, timed to the midday wind
  lull so the turbine carries the evening peak.
- A battery discharge below 40% sustained for more than 6 hours
  triggers a station-leader review — that is a trend signal, not a
  threshold violation.

## Turbine

Cut-in at 3.5 m/s, rated at 12 m/s, cut-out at 25 m/s. During blizzard
events expect a 30% output derate from rim icing even before the
brake engages. The technician inspects the blades at every rotation
changeover, and after any storm that sustained winds above 20 m/s.

## Load budget (design, deep-season average)

| Load | Average draw |
|---|---|
| Station heat (interior +4°C minimum, SAFETY.md) | 6.0 kW |
| Science instruments (SCIENCE.md) | 1.2 kW |
| Comms equipment during a pass (COMMS.md) | 0.4 kW |
| Galley, lighting, misc. | 1.1 kW |

If the turbine is forecast offline for more than 18 hours, the station
leader may approve a controlled load shed in this order: galley
second-heating, non-critical lighting, then imager duty-cycle reduction
(SCIENCE.md) — never heat, never comms.

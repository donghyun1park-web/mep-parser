# G2 thermal acceptance benchmark

- Room: 4.0 m × 3.0 m × 2.8 m
- Column: 0.4 m × 0.4 m × 2.8 m
- Heat-source equipment: 0.5 m × 0.5 m × 1.0 m, 1.0 kW, convection 80%
- Supply/exhaust: 500/500 CMH, ceiling terminals, radius 0.2 m
- Expected OCC air volume: 32.902 m³
- Thermal mesh-uncertainty window: at least 3.0 flow-through times; statistics
  use the final 0.1 flow-through window
- v3 gate: Eca-Hoekstra LSR uncertainty at or below 5% and final-window drift
  at or below 2% for volume-weighted mean temperature rise, temperature-rise
  p95, and velocity p95

This file is the persistent source for the G2 three-grid acceptance run.  It
must not be edited after a job starts because the resumable job records and
checks its SHA-256 hash.

## 2026-07-20 actual acceptance

- Study: `gci-c7ceb31f21f2`
- Widths: 0.420 / 0.350 / 0.292 m
- Actual cells: 15,563 / 20,377 / 32,919
- Effective refinement ratios: 1.0940 / 1.1734
- All three mesh, isothermal, and buoyant runs: PASS at 59.2236 s and 0.25 flow-through
- End-to-end wall time: 5,074.7 s (84 min 34.7 s)
- Checkpoint rerun: 1.405 s without repeating OCC, mesh, or solver stages
- Final GCI gate: FAIL; maximum temperature rise GCI 5.8502%, temperature-rise
  p95 and velocity p95 non-monotonic

The follow-up defaults therefore shift the two proven finer widths upward and
add a new 0.243 m fine level: 0.350 / 0.292 / 0.243 m. Compatible completed
widths are reused from earlier studies after validating their saved artifacts.

## 2026-07-20 refined follow-up

- Study: `gci-7f318b32beb7`
- Actual cells: 20,377 / 32,919 / 47,960
- Effective refinement ratios: 1.1734 / 1.1336
- The first two levels were reused from `gci-c7ceb31f21f2`; only 0.243 m ran
- New-level wall time including OCC, mesh, isothermal, thermal and GCI: 3,457.328 s
- All three solver levels: PASS at 59.2236 s and 0.25 flow-through
- Final GCI gate: FAIL; maximum temperature-rise GCI 28.9152%, temperature-rise
  p95 and velocity p95 non-monotonic

An audit of the raw `T`, `U`, and cell-volume `V` fields showed that the legacy
p95 metrics are cell-count-unweighted. Volume weighting makes the temperature
mean stable (GCI 0.0011%) but does not make the velocity mean or p95 snapshot
metrics grid-independent. The next acceptance contract must therefore use
volume-weighted statistics over a retained late-time window, not one final
URANS snapshot. Continuation recovery now retains a bounded sparse history for
that purpose.

## 2026-07-21 one-flow-through v2/v3 evidence

- Three-grid v2 study `gci-eccb43f3cd5f`: 20,377 / 47,960 / 107,991 cells,
  effective ratios 1.3302 / 1.3107, all solvers PASS at 236.8944 s
- v2 result: FAIL. Temperature p95 GCI was 3.8899%, while mean temperature
  rise and velocity p95 were non-monotonic.
- Four-grid v3 study `gci-8cea82d4f11b` added a 9,374-cell 0.504 m level.
  Its terminal-only minimum refinement preserved exhaust/supply patch area to
  1.43% / 1.31%; the next effective refinement ratio was 1.2954.
- Eca-Hoekstra v3 result at 1.0 flow-through: FAIL. Fine-grid uncertainty was
  5.4844% for mean temperature rise, 56.9682% for temperature p95, and
  30.3042% for velocity p95.
- Snapshot audit found mean temperature still rose about 0.10--0.12 K during
  the final 0.1 flow-through window. This is a transient-stationarity failure,
  so the v3 acceptance duration was raised to 3.0 flow-through times and a 2%
  final-window drift gate was added. Study `gci-aca6a016b2e1` resumes all four
  existing results rather than restarting them.

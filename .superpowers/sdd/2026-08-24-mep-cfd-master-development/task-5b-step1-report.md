# Task 5b Step 1 report — serial environment acceptance

## Outcome

- Status: `PASS`
- Execution date: 2026-08-27 (Asia/Seoul)
- Scope: synthetic 2 m × 2 m × 2 m, 64-cell environment-acceptance case only
- Evidence contract: `local_usability_acceptance.v1`
- Independent evaluation: `local_usability_acceptance_evaluation.v1`, blockers 0
- Evidence location: ignored runtime tree under `cfd_projects/`; no runtime artifact is source-controlled

This closes Task 5b Step 1 only. It does not close the working-room, exact-heat,
limited numerical spot-check, confirmed-DXF GUI, restart, usability, M1, design
citation, or release gates.

## Implementation

- Added `scripts/produce_local_usability_acceptance.py` as the stateful producer.
- Kept `scripts/local_usability_acceptance.py` as the pure, fail-closed validator.
- The producer stages a complete candidate tree, invokes the pure validator in
  isolation, and publishes the manifest last only after candidate PASS.
- A failed external runtime or legacy builder `SystemExit` returns a sanitized
  blocker and preserves the prior canonical authority.
- Each run binds one run ID across FreeCAD staged diagnostics, Studio launch
  observations, actionable diagnostic fixtures, OpenFOAM case evidence, runtime
  capability, and the final manifest.
- Publication moves any prior authority to ignored history and rolls back if a
  canonical publish operation fails.

## Actual execution evidence

- Locked interpreter: Python 3.12.10 x64, authenticated by the repository lock.
- FreeCAD: 1.1.1; OCC: 7.8.1; staged import/Boolean/tessellation diagnostics PASS.
- OpenFOAM: v2606 under Ubuntu 24.04 WSL2; serial runtime ready.
- Mesh: independently parsed `cells: 64`; `Mesh OK` present.
- Solver: `buoyantBoussinesqPimpleFoam`, latest physical time 1.0 s, clean `End`.
- Runtime baseline: PASS; runner wall time 6.656 s; peak RSS 63,832 KiB.
- Studio: 3/3 process launches reached HTTP and required DOM marker in about
  1–2 seconds, then completed bounded background diagnostics and clean shutdown.
- Actionable Korean diagnostic observations: 5/5 present and hash-bound.
- Independent post-publication validator: PASS, blockers 0.
- MPI tools are statically available, but the execution smoke remains `NOT_RUN`;
  this Step is intentionally serial-only.

## Defect found and corrected

The first real candidate was correctly blocked because the fatal-log detector
matched OpenFOAM's normal startup banner, `Floating point exception trapping
enabled`, as though it were a crash. A regression test reproduced the false
positive. The matcher now excludes that exact enabled-trapping banner while
continuing to reject actual `FOAM FATAL ERROR`, segmentation fault, floating
point exception, killed, and disk-full signatures.

## Verification recorded before commit

```text
producer tests: 14 passed
fatal/banner regression selection: 4 passed
actual producer execution: PASS, blockers 0
independent canonical evaluation: PASS, blockers 0
focused producer/validator/capability/Studio/physics: 289 passed, 7 skipped
Python compile: PASS
git diff --check: PASS
cfd_projects tracked paths: 0
public code commit: 9e625e2dfd6c05b03e0d6efdffbcbf6b8fc5cb35
Windows CI run 33028118325: 1194 passed, 14 skipped, 7 warnings
JUnit artifact 9629255682: sha256:be01310ff1ca7d9dee6eb78234c8822ec84a819694630ded2c03f8d24e5c78fb
```

The code commit is published on `origin/codex/case-evidence-review-gate`, and
the exact-commit Windows CI run completed successfully.

## Remaining gates

1. Task 5b Step 2 — working-room anchor/repeat actual runs.
2. Task 5b Step 3 — exact heat and limited numerical spot-check actual runs.
3. Task 4.5 — accountable MEP confirmation and confirmed geometry.
4. Task 5c — confirmed-DXF GUI E2E, restart integrity, and reduced usability.
5. M1 — remains `NO-GO` until every independent exit condition passes.

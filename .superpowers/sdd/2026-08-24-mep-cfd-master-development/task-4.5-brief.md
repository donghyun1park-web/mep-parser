# Task 4.5 — 실제 DXF geometry 확정 준비 및 MEP 검토 게이트

## Authority

- Master plan: `docs/superpowers/plans/2026-08-24-mep-cfd-master-development.md`, Task 4.5.
- Companion plan: `docs/superpowers/plans/2026-08-14-mep-cfd-validation-vv-release.md`, Task P7.1.
- This is an MEP-engineer judgment task, not a code-completion task.

## Required outcome

One source DXF must produce a separate `*.confirmed.geometry.json` with all of
the following recomputed from the saved bytes:

- `contract=geometry.v2`;
- `review.ready=true`;
- blocker count `0`;
- body-fitted issue count `0`;
- retained source identity plus terminal and heat-source evidence.

Until those conditions are met, Task 4.5 remains open and Task 5c/M1 Exit stay
blocked.

## In-scope source and isolation

- Inspect the canonical SGI lobby DXF and its adjacent unconfirmed
  `*.geometry.json` from the original checkout read-only.
- Keep all exact coordinates, fingerprints, copied source data, candidate
  mappings, and generated runtime artifacts under ignored `cfd_projects/` in
  the isolated `codex/case-evidence-review-gate` worktree.
- Do not modify the original checkout or its source DXF/geometry.
- Do not commit or publish real-site DXF, geometry, coordinates, screenshots,
  or confirmed geometry without a separate publication decision for those new
  artifacts.

## Required technical preparation

1. Audit source-unit state, zone candidates, terminal candidates, equipment,
   and the current body-fitted blockers.
2. Produce a local-only MEP review package that states every proposed value as
   `CANDIDATE_NOT_APPROVED`, records missing evidence, and gives the reviewer an
   explicit approve/correct/reject field.
3. Verify the existing Studio confirmation path and its fail-closed tests.
4. Record a tracked, sanitized Task 4.5 progress report without publishing
   project coordinates or source artifacts.
5. If accountable MEP evidence is absent, stop before confirmation and report
   `READY_FOR_MEP_REVIEW`, not completion.

## Human decisions required by P7.1

The accountable reviewer must confirm or correct, with evidence references:

1. source drawing units are millimetres despite the header conflict;
2. the proposed A-ELE04 extent is the single closed lobby air zone;
3. the clear/analysis height is 10.0 m and its basis;
4. all 30 terminal identities, 15 supply + 15 exhaust assignment, 444 CMH per
   terminal, ceiling/host direction, and total imbalance no greater than 1%;
5. whether 15.5 kW is the actual total heat load, its real spatial allocation,
   equipment heights, convective fraction, and evidence source.

Detected values are candidates only. No individual heat source may be created
without location-specific evidence. A uniform floor heat assumption remains
`SCREENING_ONLY` until source-location sensitivity and field validation.

## Acceptance and verification

- Existing source files retain their original hashes.
- Local review package clearly distinguishes detected facts, proposed inputs,
  missing inputs, and reviewer decisions.
- Targeted confirmation/validator tests pass under an available interpreter.
- A saved confirmation, if and only if human evidence is supplied, is
  independently reopened and validated for the four required zero-blocker
  properties above.
- The tracked progress record names the remaining human owner/input blocker.

## Controller rulings

1. The SDD task extractor accepts integer headings only, so this decimal Task
   is manually extracted here; the two plan sections above remain authoritative.
2. Prior SGI case metadata that labels airflow or heat as assumptions is
   candidate context, never approval evidence.
3. Server-derived normals from reviewed host/role inputs are acceptable only
   after the reviewer confirms those host and role inputs.
4. Startup or test success is not geometry acceptance and is not solver
   validation.
5. No fake reviewer identity, inferred approval, or auto-filled evidence is
   permitted.

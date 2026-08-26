# Task 4.5 report — technical preparation only

## Outcome

- State: `READY_FOR_MEP_REVIEW`
- Task 4.5: `OPEN`
- MEP approval: `NOT_APPROVED`
- Confirmed geometry: not created
- Task 5c / M1 Exit: blocked

The technical review package is prepared under the isolated worktree's ignored
`cfd_projects/_task45_review/` directory. Exact source fingerprints, project
coordinates, and candidate mappings exist only in that local package; none are
included in this tracked report.

## Completed technical preparation

1. Audited the canonical unconfirmed source and retained its before-work
   fingerprints locally.
2. Confirmed that the geometry contract is current but fails closed for
   body-fitted work.
3. Classified one inferred zone, 30 unconfirmed terminal candidates, and three
   unconfirmed equipment candidates.
4. Recorded all current review blockers and separated detected facts from
   proposed values.
5. Produced row-level `APPROVE` / `CORRECT` / `REJECT` fields plus accountable
   reviewer and evidence fields.
6. Kept all plan-proposed height, role, airflow, host, direction, and heat-load
   values labelled `CANDIDATE_NOT_APPROVED`.
7. Did not create or simulate a reviewer identity, approval, or confirmed
   geometry.

## Fail-closed findings

The current geometry has `review.ready=false` with 128 blockers: source-unit
confirmation (1), closed-space selection (1), four missing decisions for each
of 30 terminals (120), plus confirmation and height for three equipment
candidates (6).

The prior local SGI case cannot serve as accountable evidence. Its own metadata
states that airflow and heat were assumptions. It is retained only as clearly
labelled candidate context in the ignored package.

## Verification

Required interpreter:

```powershell
& 'C:\Program Files\Python314\python.exe' -B -m pytest -q `
  'tests/test_studio_workflow.py::StudioWorkflowTests::test_body_gci_selection_rejects_unconfirmed_geometry' `
  'tests/test_studio_workflow.py::StudioWorkflowTests::test_body_gci_selection_revalidates_changed_geometry' `
  'tests/test_studio_workflow.py::StudioWorkflowTests::test_body_gci_semantic_confirmation_saves_ready_copy' `
  'tests/test_studio_workflow.py::StudioWorkflowTests::test_body_gci_confirmation_preserves_dxf_terminal_traceability' `
  'tests/test_studio_workflow.py::StudioWorkflowTests::test_body_gci_confirmation_can_promote_a_reviewed_bbox_candidate' `
  'tests/test_studio_workflow.py::StudioWorkflowTests::test_body_gci_semantic_confirmation_rejects_flow_imbalance' `
  'tests/test_cfd_safety.py::GeometrySafetyTests::test_confirmed_heat_source_keeps_dxf_identity_and_heat_evidence' `
  'tests/test_cfd_safety.py::GeometrySafetyTests::test_missing_heat_source_type_stays_unreviewed_in_v3_adapter' `
  'tests/test_cfd_safety.py::GeometrySafetyTests::test_dxf_detected_heat_source_stays_unreviewed_in_v3_adapter'
```

Implementer result: `9 passed in 1.13s`.

Controller rerun: `9 passed in 0.62s`. Independent review rerun:
`9 passed in 1.00s`.

The selected nodes cover rejection of unconfirmed geometry, revalidation after
bytes change, separate-copy confirmation, terminal source traceability,
unit-gated promotion of a reviewed bbox candidate, the 1% flow-balance gate,
retention of confirmed heat evidence, and rejection of heat inputs whose
source type has not been reviewed.

An initial `unittest` node-name invocation produced six import errors because
this repository's studio test imports a sibling test module as a top-level
module. The repository-standard pytest path invocation was then verified with
one node and used for the nine-node run above. This was a test-discovery error,
not a product assertion failure; no source change was made.

Package validation also confirmed 30 terminal rows (15 supply candidates and
15 exhaust candidates), three equipment rows, all proposed semantic values
explicitly labelled `CANDIDATE_NOT_APPROVED`, all reviewer decisions pending,
and zero confirmed geometry files. Final source re-hashing matched the initial
local fingerprints for both the DXF and unconfirmed geometry; the exact
fingerprints remain only in the ignored review package.

Limitations: these tests use synthetic temporary geometry. They do not supply
MEP judgment, validate the real-site candidate, start Studio interactively, or
run FreeCAD/OpenFOAM/solver work.

## Remaining accountable inputs

An authorized MEP reviewer must confirm or correct, with evidence:

1. drawing units;
2. the single closed lobby air-zone boundary;
3. analysis height and basis;
4. every terminal identity, role, design airflow, host, and resulting balance;
5. actual heat-source locations, equipment heights, total load, convective
   fractions, and evidence.

Until those inputs are complete, no `*.confirmed.geometry.json` may be created
and downstream body-fitted execution remains blocked.

## Independent review

No Critical or Important technical/privacy finding remains after force-tracking
only this sanitized report and its brief. Exact source artifacts, fingerprints,
coordinates, and reviewer worksheets remain ignored under `cfd_projects/`.

## Localhost review import status

After the review screen was ready, an authorized controller-only localhost
import created an ignored, byte-verified review copy and unconfirmed geometry.
The import returned successfully and left the original artifacts unchanged.
The loaded geometry remains fail-closed with the existing 128 review blockers;
there are zero confirmed geometries, meshes, solver runs, and cases. The
browser remains in terminal review mode with all confirmation and save actions
unperformed. This is preparation for human MEP review, not approval or solver
validation.

## 2026-08-27 partial user input registration

The user stated mm units, closed-boundary intent, a 10 m height, 30 terminals
at 444 CMH each, and a 15.5 kW approximate transient-occupant/lobby-flow heat
estimate. This is recorded as `PARTIAL_USER_INPUT_RECORDED`, not accountable
MEP approval. Unit, boundary, and height require evidence; terminal
supply/exhaust roles, hosts, and directions remain unassigned; and the heat
estimate remains `SCREENING_ONLY` pending spatial distribution, people count/
schedule or calculation basis, convective fraction, and evidence. Reviewer
identity, evidence, all row decisions, and final authorization remain blank or
pending, so Task 4.5 remains `OPEN` and no confirmed geometry, case, mesh, or
solver artifact is authorized.

## 2026-08-27 second user confirmation and fail-closed attempt

The user accepted the candidate terminal configuration: 15 supply and 15
exhaust terminals, 444 CMH each, ceiling installation, supply down, and exhaust
up. This is `SECOND_USER_CONFIRMATION_RECORDED_NOT_ACCOUNTABLE_APPROVAL`; it
does not replace row-level accountable review, reviewer identity/role/authority,
or evidence. The user supplied `NONE_PROVIDED` heat basis. The 15.5 kW estimate
therefore remains `SCREENING_ONLY` and cannot be allocated to detected equipment
or a uniform source. Separate-copy authorization is conditional on the existing
technical confirmation contract.

One authorized real-data confirmation call used the ignored unconfirmed geometry,
the candidate 10 m/bounding-zone/30-terminal configuration, actual terminal
identities, and zero heat-source obstacles. It returned `ok=false` with the
heat-source-required error. No confirmed geometry, case, mesh, solver, GCI, or
field job was created. Source and runtime input hashes were unchanged before and
after, and the confirmed-geometry count remained zero.

Before that call, an implementer-added blank CSV row caused a pre-call payload
construction error. It was recorded as attempt 0, removed without altering the
30 candidate rows or their user-input fields, and is not treated as the product
confirmation attempt. The actual function call was attempt 1 and was not retried.

Task status: `CONFIRMATION_AUTHORIZED_BUT_BLOCKED_HEAT_INPUT`. Task 4.5 is not
complete; Task 5c and M1 Exit remain blocked.

The original partial-input receipt remains preserved as the first registration.
The latest second-confirmation receipt references it as history; the local audit
and review package point to that latest receipt. This preserves the distinction
between the original unapproved parser candidates and the later user-accepted
terminal configuration.

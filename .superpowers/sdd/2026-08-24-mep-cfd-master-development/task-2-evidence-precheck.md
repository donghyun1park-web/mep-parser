# Task 2 evidence precheck (read-only, 2026-08-24)

## Decision summary

Task 2 can implement a fail-closed `cfd_evidence.py`, but it must treat every
pre-existing manifest as *an input to recomputation*, never as a trust anchor.
The current manifest schemas are mostly open (`additionalProperties: true`) and
only partly constrain hashes/cross-links.  Existing `cfd_result_gate` supplies
valuable semantic checks, but it does not replace a root-contained,
schema-validated, path-aware inventory.

`projects_root` is the authority root.  Store every `artifact_refs.*.path` as a
normalized POSIX path relative to this root; resolve it with `strict=True`, then
verify resolved containment before reading.  `case_dir` must resolve beneath
`projects_root/_body_solver`; do not infer a case from a result/report path.

## Canonical artifact-key mapping

| Evidence key | Current producer / raw contract | Canonical location under `projects_root` | Required recomputation and link checks |
|---|---|---|---|
| `geometry` | `dxf_parser.py` emits geometry then `geometry_v2.migrate_geometry`; contract `geometry.v2` | Imported project geometry; current producers permit a root-contained path, rather than one fixed directory | SHA current bytes; `geometry_v2.validate_geometry_v2`; require `review.ready` for body-fitted purpose; bind legacy identity's geometry link. |
| `surface` | FreeCAD/OCC worker via `cfd_occ.py`; `surface_manifest.v1` | `_occ_geometry/<occ-output>/surface_manifest.json` | SHA manifest; Draft 2020-12 schema; semantic `air_volume.valid`, one solid, watertight/zero topology defects; `source.geometry_path` resolves to `geometry` and `source.geometry_sha256` equals current geometry SHA; rehash declared STL/BREP outputs. |
| `mesh` | `cfd_mesh.run_mesh_case`; `mesh_manifest.v1` | `_body_mesh/<mesh-case>/mesh_manifest.json` (a copy of `surface_manifest.json` is in the same case) | SHA manifest; schema; `status == PASS`, mesh/strict checks; manifest input hashes current local copied surface and `mesh_input.json`; copied surface bytes/semantic identity must match canonical `surface`; reject a merely equal-looking copied JSON if it does not hash to the canonical artifact selected by the chain. |
| `run` | `cfd_physics` thermal run builder/finalizer; `run_manifest.v1` | `_body_solver/<thermal-case>/run_manifest.json` | SHA manifest; schema; only `body_fitted_buoyant_urans` can satisfy design-facing solver evidence; rehash `thermal_input.json`, numerical provenance system files, restart input when selected, and canonical effective settings/numerics; retain `body_fitted_numerical_provenance_issues()` semantic recomputation. |
| `thermal_input` | `cfd_physics.build_buoyant_case`; `thermal_input.v1` (no standalone schema presently) | sibling of `run`: `_body_solver/<thermal-case>/thermal_input.json` | JSON/object/contract and current SHA; its `mesh_manifest_sha256` must equal the copied solver-case mesh manifest; run `input.thermal_input_sha256` must equal it.  This is profile-gated in Case Evidence but required for a buoyant `run`. |
| `thermal_progress` | `cfd_physics._attach_thermal_progress`; `thermal_progress.v1` | sibling of `run`: `thermal_progress.json`; also embedded in current run | SHA standalone current bytes; schema; the embedded run payload and standalone payload must be canonical-JSON equal (or controller must explicitly rule a one-way authority); recompute/reject impossible duration/time/history values rather than trusting its status. |
| `result` | `cfd_post.build_result_artifacts`; `result_manifest.v1` | sibling of `run`: `_body_solver/<thermal-case>/result_manifest.json` | SHA manifest; schema; rehash source VTU, summary and x/y/z slices; validate summary/slice structure; result's current run/mesh/thermal-input hashes must equal the selected raw artifacts.  Never accept `case_summary()` cache or body report HTML as this key's source. |
| `numerical_sensitivity` | `cfd_numerical_sensitivity_job.py` + runner; numerical-sensitivity contracts | Study-owned output, not a fixed case child (currently preparation trees / job artifacts) | **No stable final result contract/path is identified in the Task-2 inputs.** Accept only after a controller-selected final contract specifies the study root, child identity, hash fields and how it binds the selected solver case.  Do not use a preparation/frozen-pair `FROZEN_INPUTS` record as PASS evidence. |
| `gci` | `cfd_gci.build_grid_convergence` invoked by `cfd_gci_job`; `grid_convergence.v3` for design path | `_body_gci/<study>/grid_convergence.json` (`gci_root` defaults here) | SHA manifest; schema chosen by declared contract; require `PASS` and `design_ready`; find the selected solver case by resolved path and require all four current provenance hashes (run/result/mesh/thermal input) to match.  Do not select "any PASS GCI". |
| `benchmark` | not yet a single M1 producer/contract | controller-defined future root | Keep absent/`NOT_EVALUATED` in all core v1 Task-2 paths; do not promote the standalone radiation two-plate result or copied benchmark-shaped JSON. |
| `field_evidence` | `field_acceptance.build_field_acceptance`; `field_dxf_acceptance.v1` | `_release_evidence/field_dxf/<source>-<sha12>.json` | SHA manifest and call `field_acceptance.validate_evidence`; it reopens DXF/geometry/surface/mesh/run/result and calls the body-fitted result gate.  Require the recomputed field artifact links to resolve to the exact selected artifacts, not just equal hashes in an unrelated case. |

### Important root/identity facts

* The same surface and mesh manifests are physically copied along the pipeline;
  content equality alone is insufficient.  The authoritative links are the
  selected canonical artifact plus the downstream manifest's current hash.
* Current `field_pipeline_job` persists absolute input/result paths in its own
  job manifest.  It is operational metadata, not a Case Evidence artifact ref.
* Existing `cfd_result_gate._find_passing_gci_manifest()` locates a current
  body-fitted GCI by resolved selected case path and four hashes.  Preserve this
  invariant; Task 2 must additionally validate the GCI manifest/schema and
  root-contained candidate path.

## Authoritative recomputation sequence

1. Canonicalize and validate `projects_root`, `case_dir`, optional `gci_root`,
   optional `field_evidence_path`, and output.  Reject non-files, symlinks or
   resolved paths outside their permitted roots.  Reject reparse/symlink
   traversal at every declared file and while walking any tree needed for a
   validator.
2. Reopen JSON from disk.  Validate the actual raw schemas using
   `Draft202012Validator`; raw schema validity is necessary but never PASS.
   Add small local semantic validators for raw contracts with no schema
   (`geometry.v2`, `thermal_input.v1`, numerical/GCI contract selected by
   controller).
3. Recompute SHA-256 from bytes for every selected manifest and every raw
   artifact its contract hashes: geometry; surface outputs; mesh input and
   copied surface; thermal input/restart/system files; run; result source,
   summary and slices; standalone thermal progress; selected GCI; field
   evidence and its referenced raw chain.
4. Recompute cross-links in dependency order:

   `geometry -> surface -> mesh -> thermal_input -> run -> result -> GCI`

   The selected result also binds to run/mesh/thermal input; GCI binds the same
   four current provenance hashes.  Field evidence is a second, independent
   traversal of that chain, not its replacement.
5. Derive each of the eight Task-1 source check statuses from these results;
   a manifest `status`, `design_ready`, `citation_status`, `ok`, report copy,
   or caller-provided error list may only be corroborating data.  Unknown or
   future-owned validators must remain `NOT_EVALUATED` (or `BLOCKED` if an
   artifact that is required by the declared purpose is unsafe/unreadable).
6. Persist exact root-relative path plus current digest in the new evidence.
   `validate_case_evidence()` repeats steps 1-5 and compares the stored
   artifact refs and derived check/error results with current recomputation;
   schema-valid evidence that differs is invalid/stale.

## Tamper and regression matrix

| Test mutation | Expected Task-2 result |
|---|---|
| Write `{"status":"PASS"}` as case evidence | Build ignores it; cannot become PASS without raw chain. |
| Delete required geometry/surface/mesh/run/result | Missing-artifact error; required check not PASS. |
| Artifact path is absolute, drive-qualified, uses `\\`, `.`/`..`, or a symlink/reparse point escapes root | Reject before open/hash (`PATH_ESCAPE`-class error). |
| Use correct file under `_release_evidence`/report cache as a core artifact | Reject wrong root/contract/role; reports and evidence output are not sources. |
| Change geometry bytes after surface created | geometry SHA and `surface.source.geometry_sha256` mismatch. |
| Change surface copy or `mesh_input.json` | mesh input/copy hash chain mismatch. |
| Change thermal input, restart input, controlDict/fvSchemes/fvSolution, or semantic scheme while refreshing claimed hashes | run numerical provenance fails; semantic validator catches self-consistent upwind claim. |
| Change run/mesh/thermal input after result | result cross-hash mismatch. |
| Change VTU/summary/slice or remove an axis | result artifact/hash/shape failure. |
| Modify standalone progress while retaining embedded progress | progress cross-artifact mismatch (pending controller authority ruling below). |
| Select a PASS GCI for another case, modify GCI selected child hashes, or copy a benchmark-shaped GCI | GCI root/path/schema/provenance mismatch; no grid PASS. |
| Pass numerical-preparation/frozen-pair JSON as final numerical sensitivity | `NOT_EVALUATED` until final contract/authority is defined. |
| Edit/copy a field acceptance JSON or point it to other case artifacts | `field_acceptance.validate_evidence()` fails or recomputed links disagree. |
| Evidence output modified after build | `validate_case_evidence()` emits `ARTIFACT_HASH_MISMATCH`/stale evidence error. |
| Output equals input or output/report is recursively discovered on next build | explicit self/generated exclusion; no self-hashing. |

## Atomic publish and inventory exclusions

Use same-parent `mkstemp`/write/flush/`os.fsync`/`os.replace`, following the
existing `field_acceptance._atomic_json` and `cfd_post._atomic_json` pattern,
with cleanup on all exceptions.  Do not create staging beneath an artifact
directory that a future directory walk might treat as input.

Exclude from all source discovery and from hash graphs except as the *object
being validated*:

* `output_path` (normally a `case_evidence.v1.json` in a dedicated evidence
  destination), its dot-prefixed staging sibling, and any prior case-evidence
  outputs;
* generated HTML reports, `case_summary()` cache/JSON, body-result report
  assets, and presentation-only images;
* `_release_evidence/**` generally, except an explicitly supplied and
  independently validated `field_dxf_acceptance.v1` path;
* logs, recovery/staging/backup/temp directories, `.pytest_cache`, Python
  caches, and solver working output unless a selected raw contract explicitly
  requires a particular hashed file.

The inventory must use an allow-list of exact raw artifacts/contract-declared
children, not a broad recursive `glob` of the project root.  This prevents a
previous evidence/report from satisfying a missing producer artifact.

## Legacy identity bridge

Task 1's controller ruling is binding:

* Evidence has exactly one identity branch: either future
  `case_identity={contract:"case_identity.v1",path,sha256}` or M1
  `legacy_case_ref`; never both/neither.
* For M1, recompute `legacy_case_ref` from the selected `geometry` and `run`
  artifact refs: closed five-field form `case_id`, root-relative
  `geometry_path`, `geometry_sha256`, root-relative `run_manifest_path`, and
  `run_manifest_sha256`.
* `case_id` must be derived from the canonical solver case identity (chosen
  case-child name under `_body_solver` after a conservative allowed-name
  check), not copied from a field/GCI job label.  A collision strategy is not
  specified; see controller rulings.
* Legacy evidence is screening-only by rule, even if every raw artifact and
  old `cfd_result_gate` says design-citable.  It cannot claim
  `DESIGN_CITABLE`; no human review or field artifact may upgrade it before
  Task 6 identity exists.

## Stable helpers appropriate for `cfd_result_gate.py`

Keep the module's current public `evaluate_body_fitted_case()` behaviour.
`body_fitted_numerical_provenance_issues()` is already deliberately public and
is the right semantic sub-check to reuse without a GCI cycle.

If factoring is needed, expose narrow read-only helpers with documented return
data (not permissive booleans), for example:

```python
def validate_body_fitted_result_artifacts(case_dir: Path) -> list[str]: ...
def current_body_fitted_case_provenance(case_dir: Path) -> dict[str, str] | None: ...
def find_current_passing_gci(case_dir: Path, *, gci_root: Path) -> Path | None: ...
```

They should retain the existing result source/summary/slice validation and the
four-hash GCI equality exactly.  Do **not** expose a helper that simply accepts
manifest `PASS`, raw path strings, or caller-supplied hash values.  Task 2
still owns strict projects-root-relative lexical validation, symlink containment
and raw JSON-schema validation; making those responsibilities implicit inside a
gate helper would make future callers accidentally bypass them.

## Controller rulings needed before implementation dispatch

1. **Numerical sensitivity authority:** name the final executable artifact
   contract, root, selected-child relation and exact PASS/recomputation
   validator.  Current frozen/preparation artifacts are intentionally not
   execution evidence.
2. **Thermal-progress authority:** decide whether the embedded
   `run_manifest.thermal_progress` and `thermal_progress.json` must be byte/
   canonical-JSON identical, or which is canonical and how the other is bound.
   Current producer writes the standalone progress before the run manifest is
   republished, so equality needs an implementation/protocol decision.
3. **Geometry location/name rule:** Task 1 permits any safe projects-root
   relative reference, while current producers put geometry alongside imported
   DXFs.  Confirm whether to allow any root-contained `.json` or limit it to
   an import namespace; do not infer it from `surface.source.geometry_path`
   without independently binding the requested case.
4. **Legacy `case_id` collision rule:** clarify allowed character set and
   whether `case_id` is solver directory name, root-relative solver directory,
   or deterministic hash.  The bridge must be stable across root relocation
   and resist two similarly named solver cases.
5. **GCI selector policy:** where several valid studies contain the same
   current case provenance, specify deterministic selection (e.g., exact
   explicit ref only versus canonical sorted ref).  Avoid a silent first-glob
   winner; default build should either require an unambiguous single match or
   record a controller-defined selection rule.
6. **Optional artifact status policy:** spell out whether unavailable
   numerical sensitivity/GCI/field evidence yields `NOT_EVALUATED` versus
   `BLOCKED` for each Task-1 purpose.  Task-1 profile rules decide citation
   outcome, but Task 2 needs stable source-check status/error codes.
7. **`case_evidence` output placement:** confirm dedicated default path and
   whether overwrite is allowed.  Atomic replacement is safe for a selected
   output, but an append-only historical evidence policy would require a new
   immutable filename rather than replacing a prior validation artifact.

## Pre-dispatch conclusion

Implement Task 2 only after the seven rulings are fixed (at minimum 1, 2, 4,
and 5).  The build may emit a structurally valid legacy screening record before
future modules exist, but it must fail closed for incomplete or ambiguous raw
chains and must never turn status strings, caches, reports, or copied manifests
into evidence.

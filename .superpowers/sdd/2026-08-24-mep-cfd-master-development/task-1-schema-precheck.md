# Task 1 schema pre-implementation check

**Scope:** read-only review of the master plan, Task 1 brief/controller rulings, current JSON Schemas, fixture idioms, and current manifest consumers.  No source, test, Git, or evidence/ledger artifact was changed.

## 1. Compatible patterns to retain

### Artifact references, paths, and hashes

- The closest reusable reference shape is `{ "path": "...", "sha256": "...", "contract": "..."? }`.  `io_acceptance.authoritative_case_inventory.v1` uses this closed `link` definition; its `contract` is optional.  `result_manifest.v1` and `field_dxf_acceptance.v1` use the same `path`/`sha256` pair in less-closed objects.
- SHA-256 must be the existing strict lowercase form: `^[a-f0-9]{64}$`.  This is used by run, inventory, working-validation, field-DXF, and current runtime re-hash code.  Do not use `minLength: 64`/`maxLength: 64` for the new trust contracts: `result_manifest.v1` has that weaker legacy form, whereas evidence must reject non-hex values.
- Current result artifact paths are **case-relative** (`cfd_post.py` writes `results/...`, `summary...`, and slice paths); authoritative-inventory paths are **projects-root-relative** (`_body_solver/<case>/...`).  `cfd_result_gate._case_artifact()` and Studio both enforce containment with `resolve().relative_to(...)` at runtime.
- Task 1 should standardize all new evidence/health/review references on normalized POSIX, **projects-root-relative** paths (for example `_body_solver/room-001/run_manifest.json`).  Prohibit empty, absolute, drive-qualified, backslash, and `.`/`..` segment paths in the schema; Task 2 must still resolve the joined path and reject symlink/root escapes.  JSON Schema alone cannot prove real-path containment.
- Existing producer fields such as `field_pipeline_job.input.geometry_path` and surface `source.geometry_path` may be absolute.  They are upstream provenance, not an acceptable replacement for new `artifact_refs` or either evidence identity branch.
- Current integrity checks re-hash the file currently on disk and compare cross-links (`result → run/mesh/thermal`, GCI → current case provenance).  Task 2 must continue this pattern; a schema-valid caller-authored `PASS`, cached `case_summary()`, or report HTML is not authoritative evidence.

### Schema and test conventions

- Existing schemas declare JSON Schema Draft 2020-12 with a local `$id` (usually `https://mep-cfd-studio.local/schemas/<name>.json`), `schema_version: {"const": 1}`, and a `contract` constant such as `case_evidence.v1`.
- Most older manifests are permissive (`additionalProperties: true`), but the newer trust/I/O contracts are closed at the root and at reusable `$defs`.  Task 1's explicit rule is stronger: use `additionalProperties: false` by default at every new object, then deliberately list extension points (prefer none in v1).
- Reusable nested shapes belong under `$defs` and local `$ref`s.  `working_validation.v1` demonstrates fixed ordered checks using `prefixItems`; the inventory demonstrates closed reference definitions.
- Tests import `from jsonschema import validate`, load the schema with `json.loads(...)`, and validate generated dict fixtures.  `validate()` selects the declared Draft 2020-12 validator.  No current test uses a dedicated `Draft202012Validator`, `FormatChecker`, or schema compilation pass.  Therefore do not rely on `format: date-time` alone for rejection; use explicit pattern/semantic validation if timestamp syntax is part of the Task 1 contract.

## 2. Minimal fixture matrix

Keep fixtures in `tests/test_case_evidence_schema.py` as small in-memory dictionaries plus a single `_sha256`/`_link` helper patterned after `tests/test_io_acceptance.py`.  Do not make a future evaluator test pass merely by loading a JSON file.

| Area | Positive fixture | Negative fixture(s) that must be rejected |
|---|---|---|
| Catalog | `MESH_QUALITY_BLOCKED` has `status=BLOCKED`, non-empty Korean `impact`/`next_action`; fixed nine-check tuple order | unknown status code raises `ValueError`; incomplete/no Korean actionable copy; reordered/duplicated/missing fixed check ID |
| `case_evidence.v1` | valid closed evidence with one Task-6-compatible `case_identity` link, required `contract`, `created_at`, status, checks, artifact refs, and errors | missing artifact `sha256`; non-lowercase/non-hex hash; unknown evidence status; absolute/drive/backslash/`..` path; extra property; duplicate check ID |
| F3 identity bridge | valid M1 legacy evidence with **only** closed `legacy_case_ref`: `case_id`, root-relative `geometry_path`, `geometry_sha256`, root-relative `run_manifest_path`, and `run_manifest_sha256` | both branches; neither branch; legacy missing any of its five required bindings; legacy extra key; legacy claiming `DESIGN_CITABLE` (where citation appears); current-identity link with no `path` or hash |
| `case_health.v1` | fixed checks plus required versioned `citation_decision_table` exactly matching the catalog's exported version/table | unknown/duplicate/missing check; missing decision-table version/table; unknown citation status; extra property; table/version differing from the normative catalog value |
| F4 precedence (table-driven contract fixture; implementation lands in Task 3) | fixtures yielding each: `CITATION_BLOCKED`, `NOT_EVALUATED`, `SCREENING_ONLY`, and `DESIGN_CITABLE` | invalid/stale evidence or applicable invalid/stale review must beat all later rows; required `FAIL`/`BLOCKED` must beat `NOT_EVALUATED`/screening; required `NOT_EVALUATED` must beat screening; legacy identity must never yield `DESIGN_CITABLE`; optional-module status must not change core state unless purpose makes it required |
| `case_review.v1` | closed approved/rejected review with root-relative target ref and current 64-char target hash | missing target hash; path escape; unknown decision/status; extra property; target hash mismatch is a runtime `INVALIDATED` result, not a silently accepted approval |

For F4 the exact normative ordering is already controller-ruled and should be emitted as one ordered table, highest precedence first:

1. invalid/stale evidence, or invalid/stale review when referenced/required by purpose -> `CITATION_BLOCKED`;
2. any purpose-required `FAIL`/`BLOCKED` -> `CITATION_BLOCKED`;
3. any purpose-required `NOT_EVALUATED` -> `NOT_EVALUATED`;
4. screening purpose or `legacy_case_ref` -> `SCREENING_ONLY`;
5. only current `APPROVED` review plus all purpose-required `PASS` -> `DESIGN_CITABLE`.

The Task 1 schema suite should verify that the table/version is present and closed.  The full table-driven derivation/precedence tests belong in Task 3 (the health evaluator), not in a schema-only assertion.

## 3. Backward-compatibility risks for Tasks 2–4

1. **Root convention mismatch.**  Task 2 receives `projects_root`, while current result links are case-relative and several input manifests are absolute.  If Task 1 permits unspecified relative roots, Task 2 cannot safely re-hash evidence and Task 4 may point the UI at a different file.  Make every new reference projects-root-relative; let Task 2 translate existing case-relative raw links while rebuilding the inventory.
2. **Schema validation is not artifact validation.**  A permissive artifact-ref map or status supplied by a caller would recreate the prior false-PASS problem.  Task 2 must derive evidence status from current raw files, contracts, cross-hashes, and containment; it must exclude its own output/report from source inventory.
3. **Checks representation/order.**  Task 1 prose requires duplicate-ID rejection and fixed order, but Task 3's planned consumer examples access `health["checks"]["design_ready"]`.  Use an ordered array with fixed IDs for raw evidence if duplicate/order validation is required, and a named closed object for the Task-3 health read model; do not use one ambiguous structure for both.
4. **Citation vocabulary drift.**  Current `cfd_result_gate`/report know `SCREENING_ONLY`, `NOT_EVALUATED`, and `DESIGN_CITABLE`, but not controller-required `CITATION_BLOCKED`; current report labels default unknown states to failure styling.  Task 4 must consume the catalog (including a Korean label/action for `CITATION_BLOCKED`) rather than maintain another mapping.  Existing legacy response fields must remain intact.
5. **Review is not a promotion mechanism.**  `field_pipeline_job` currently uses the existing result gate and publishes terminal citation fields.  Task 3 must add current evidence path/hash and blockers without treating a completed job or approved review as an override of failed/blocked evidence; Task 4's POST must compare the supplied target hash and return the planned `409 REVIEW_TARGET_CHANGED` on mismatch.
6. **Early M1 identity cannot masquerade as Task 6 identity.**  The legacy bridge is valid only for the M1 actual-DXF screening chain.  It needs a discriminated `oneOf`, must remain closed, and must be deliberately rejected by the `DESIGN_CITABLE` branch even if every legacy artifact hash is current.

## 4. Remaining dispatch blockers / rulings to record

F3 and F4 themselves are resolved by the controller rulings in the Task 1 brief.  The following concrete shapes still need to be fixed in the Task 1 implementation decision before dispatch; otherwise independently written schemas/tests will diverge.

1. **Define `purpose` vocabulary and required-check profile.**  The controller says "purpose-required", and Task 3 says grid verification varies by purpose, but no closed purpose enum or check-to-purpose table is specified.  Add this to the normative citation decision table (including review-required purposes).  Do not leave it as free text.
2. **Fix the exact `checks` shape per artifact.**  Adopt the recommended evidence ordered array versus health named-object split above, or explicitly choose another representation.  This resolves the duplicate-ID requirement without breaking Task 3's keyed health API.
3. **Fix `artifact_refs` keys and minimum contract.**  Task 2 must inspect geometry, surface, mesh, run, thermal input/progress, result, numerical sensitivity, GCI, and optional field evidence, but Task 1 currently only names `artifact_refs`.  Specify a closed core set and which entries are optional/profile-gated; otherwise a closed v1 schema will either exclude legitimate Task-2 evidence or permit underspecified PASS evidence.
4. **Fix the current-identity reference discriminator.**  The controller requires at least `{path, sha256}`.  Record whether `contract: "case_identity.v1"` is also mandatory and whether the path is an identity JSON artifact (recommended) rather than a case directory.  `oneOf` must be structurally unambiguous without depending on a later Task-6 schema.
5. **Fix the decision-table equality rule.**  `case_health.v1` must require a version and table, while the catalog exports the normative pair.  State that Task 3 compares both values exactly to the catalog export (recommended); schema-only presence validation cannot guarantee that a consumer did not invent precedence.  If a schema `const` is desired, define the canonical literal once before coding so catalog and schema tests use the same ordered rows.
6. **Fix review target scope and status lifecycle.**  Choose whether a review targets `case_evidence.v1`, `case_health.v1`, a Task-6 identity, or a typed union; also enumerate persisted `APPROVED`/`REJECTED` and derived `INVALIDATED`/supersession semantics.  The Task 4 POST payload already includes `target_sha256`, so its schema and Task-3 create/validate API must agree on who supplies versus recomputes it.
7. **Fix `errors`/reason object form.**  Existing manifests usually use string arrays, but Task 3 requires stable `reason_codes`, impacts, actions, and evidence refs.  Set a closed error/reason definition now (at minimum `code`, with optional catalog-derived Korean text) rather than mixing strings and objects across evidence, health, and reviews.

## Read-only conclusion

Task 1 can safely start once the seven shape decisions above are recorded with the controller rulings.  The core safety direction is compatible with this checkout: Draft 2020-12, closed new trust contracts, lowercase SHA-256 links, and runtime `resolve().relative_to()` re-validation.  Do not infer validity from an existing manifest's `PASS`/`design_ready` field or from a schema-only pass.

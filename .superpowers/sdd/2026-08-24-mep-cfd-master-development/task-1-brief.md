### Task 1: 상태 catalog와 Case Evidence schemas를 고정한다

**Files:**

- Create: `cfd_status_catalog.py`
- Create: `case_evidence.v1.schema.json`
- Create: `case_health.v1.schema.json`
- Create: `case_review.v1.schema.json`
- Create: `tests/test_cfd_status_catalog.py`
- Create: `tests/test_case_evidence_schema.py`

**Interfaces:**

- Produces: `status_descriptor(code: str) -> dict[str, str]`.
- Produces: four evidence states and the fixed case-health check IDs.
- Consumed by: `cfd_evidence.py`, `cfd_case_health.py`, `cfd_review.py`, Studio, reports.

- [ ] **Step 1: 상태 단일 소스의 실패 tests를 작성한다**

  ```python
  def test_status_catalog_has_actionable_korean_copy():
      row = status_descriptor("MESH_QUALITY_BLOCKED")
      assert row["status"] == "BLOCKED"
      assert row["impact"]
      assert row["next_action"]

  def test_design_ready_checks_are_fixed_order():
      assert CASE_HEALTH_CHECKS == (
          "geometry_valid", "bc_reviewed", "mesh_checked",
          "solver_converged", "numerics_verified", "grid_verified",
          "benchmark_validated", "field_calibrated", "design_ready",
      )
  ```

- [ ] **Step 2: tests가 import failure로 실패하는지 확인한다**

  Run: `& $Python -B -m pytest -q tests/test_cfd_status_catalog.py tests/test_case_evidence_schema.py`

  Expected: FAIL because module/schema files do not exist.

- [ ] **Step 3: 최소 catalog와 JSON Schemas를 구현한다**

  ```python
  EVIDENCE_STATUSES = ("PASS", "FAIL", "BLOCKED", "NOT_EVALUATED")
  CITATION_STATUSES = ("SCREENING_ONLY", "NOT_EVALUATED", "DESIGN_CITABLE")

  def status_descriptor(code: str) -> dict[str, str]:
      try:
          return STATUS_CATALOG[code].copy()
      except KeyError as exc:
          raise ValueError(f"Unknown status code: {code}") from exc
  ```

  Schema는 `contract`, `created_at`, `case_identity`, `checks`, `artifact_refs`, `status`, `errors`를 필수로 하고 `additionalProperties: false`를 기본으로 한다.

- [ ] **Step 4: schema positive/negative tests를 통과시킨다**

  Missing hash, unknown status, duplicate check ID, root 밖 경로, review target hash 누락을 각각 거부한다.

- [ ] **Step 5: focused tests를 실행한다**

  Run: `& $Python -B -m pytest -q tests/test_cfd_status_catalog.py tests/test_case_evidence_schema.py`

  Expected: PASS.

- [ ] **Step 6: commit한다**

  ```powershell
  git add cfd_status_catalog.py case_evidence.v1.schema.json case_health.v1.schema.json case_review.v1.schema.json tests/test_cfd_status_catalog.py tests/test_case_evidence_schema.py
  git commit -m "feat: define case evidence status contracts"
  ```

## Controller rulings that supersede conflicting task prose

These are mandatory Task 1 acceptance requirements from the pre-flight scan.

1. `CITATION_STATUSES` is exactly `("SCREENING_ONLY", "NOT_EVALUATED", "CITATION_BLOCKED", "DESIGN_CITABLE")`. `CITATION_BLOCKED` is a citation state, distinct from evidence `BLOCKED`.
2. Evidence identity uses an exclusive `oneOf`: either a Task-6-compatible `case_identity` reference (`path`, `sha256`) or an M1 bridge `legacy_case_ref`. Never accept both or neither.
3. `legacy_case_ref` is closed (`additionalProperties: false`) and minimally binds `case_id`, root-relative `geometry_path`, `geometry_sha256`, root-relative `run_manifest_path`, and `run_manifest_sha256`. A legacy reference is never eligible for `DESIGN_CITABLE`.
4. The case-health contract carries a required, versioned citation decision table. Export the same normative data from `cfd_status_catalog.py` as `CITATION_DECISION_TABLE_VERSION` and `CITATION_DECISION_TABLE`; the JSON Schema must require the version and table in health artifacts so consumers cannot silently invent precedence.
5. Normative precedence, in highest-first order:
   - invalid/stale evidence, or an invalid/stale review when one is referenced or required by the declared purpose -> `CITATION_BLOCKED`;
   - any purpose-required check `FAIL` or `BLOCKED` -> `CITATION_BLOCKED`;
   - a current `REJECTED` review for a review-required purpose -> `CITATION_BLOCKED` with `REVIEW_REJECTED`;
   - any purpose-required check `NOT_EVALUATED` -> `NOT_EVALUATED`;
   - benchmark purpose -> `NOT_EVALUATED` because design citation is not applicable;
   - screening purpose or `legacy_case_ref` -> `SCREENING_ONLY`;
   - only a current unambiguous `APPROVED` review plus all purpose-required checks `PASS` -> `DESIGN_CITABLE`;
   - a review-required purpose with no current approval -> `NOT_EVALUATED` with `REVIEW_REQUIRED`.
6. Optional modules do not raise or lower the core citation state unless the declared purpose/profile makes them required.
7. Add table-driven contract tests covering every row, precedence order, all four citation states, and the identity `oneOf`, including rejection of a legacy artifact that claims `DESIGN_CITABLE`.
8. The original sentence requiring root `case_identity` is superseded by the exclusive identity-reference rule above. Preserve closed schemas, root-relative path validation, 64-character lowercase SHA-256 validation, fixed check IDs, and actionable Korean status copy.

## Exact contract-shape decisions

These close the remaining pre-dispatch ambiguities. Keep Task 1 structural: artifact recomputation and decision evaluation land in Tasks 2 and 3.

1. **Schema convention:** JSON Schema Draft 2020-12, local `$id`, `schema_version: {"const": 1}`, closed root and closed `$defs`. Every new reference path is normalized POSIX and projects-root-relative; reject empty, absolute, drive-qualified, backslash, `.` and `..` segments. Every digest matches `^[a-f0-9]{64}$`. Task 2 must still perform resolved-path/symlink containment checks.
2. **Purpose enum/profile:** export `CASE_PURPOSES` and a closed `PURPOSE_PROFILES` mapping for `screening`, `design_review_candidate`, `benchmark`, and `field_validation`.
   - `screening`: requires geometry, BC, mesh, solver, and numerics; no review required; citation ceiling `SCREENING_ONLY`.
   - `design_review_candidate`: requires all eight source checks; current approval required; ceiling `DESIGN_CITABLE`.
   - `benchmark`: requires geometry, BC, mesh, solver, numerics, grid, and benchmark; case review not required; design-citation result is `NOT_EVALUATED` because design citation is not applicable.
   - `field_validation`: requires all eight source checks; current approval required; ceiling `DESIGN_CITABLE`.
   Optional future modules are absent from these core v1 profiles.
3. **Checks:** `case_evidence.v1.checks` is an ordered array of exactly the eight source checks (`geometry_valid` through `field_calibrated`), enforced with fixed `prefixItems`. `case_health.v1.checks` is a closed named object with those eight keys plus derived `design_ready`, preserving `health["checks"][id]` access. Each check has evidence status, unique `reason_codes`, and unique `evidence_refs` artifact-key strings. Health check rows additionally require catalog-derived `impact` and `next_actions`.
4. **Artifact refs:** `artifact_refs` is a closed object. Core required keys are `geometry`, `surface`, `mesh`, `run`, and `result`. Allowed profile-gated keys are `thermal_input`, `thermal_progress`, `numerical_sensitivity`, `gci`, `benchmark`, and `field_evidence`. Every value is a closed `{path, sha256, contract?}` link. Schema presence is not proof; Task 2 decides profile completeness from current artifacts.
5. **Identity discriminator:** `case_identity` is a closed `{contract: "case_identity.v1", path, sha256}` link to the future identity JSON artifact. `legacy_case_ref` has the five fields already ruled. Structural `oneOf` is enforced by distinct root properties, and legacy requires `purpose: "screening"` and forbids `DESIGN_CITABLE`.
6. **Decision-table authority:** use version literal `citation_decision_table.v1`. `case_health.v1` requires `citation_decision_table_version` and the ordered `citation_decision_table`; its schema contains the exact literal `const`. `cfd_status_catalog.py` exports the identical eight-row data above and the schema tests compare both exactly. Task 3 must also compare both values to the catalog before evaluation.
7. **Review target/lifecycle:** `case_review.v1` targets only a `case_evidence.v1` artifact through closed `{contract, path, sha256}`. Persist `APPROVED` or `REJECTED`, reviewer, reason, creation time, immutable review ID, and optional unique `supersedes_review_ids: string[]`; an array is required so one append-only resolution record can close all leaves of a concurrent review fork. `INVALIDATED` and `SUPERSEDED` are derived validation states, never in-place mutations. The Task 4 client supplies an expected target hash; the endpoint compares it to current bytes, and Task 3 recomputes/stores the authoritative target hash.
8. **Errors/reasons:** root `errors` is an array of closed `{code, detail?, evidence_ref?}` objects; do not persist duplicate UI copy. `reason_codes` remain stable catalog codes. Korean impact/action copy comes from the catalog, with health snapshots carrying `impact: string` and `next_actions: string[]` for deterministic API/report rendering.
9. **Mandatory cross-task codes:** the actionable Korean catalog and tests must include at least `CASE_EVIDENCE_NOT_FOUND`, `REVIEW_TARGET_CHANGED`, `REVIEW_REQUIRED`, `REVIEW_REJECTED`, and `REVIEW_HISTORY_AMBIGUOUS`, in addition to the Task-1 mesh/example and core evidence error codes. Later UI/report code may not invent copy for them.

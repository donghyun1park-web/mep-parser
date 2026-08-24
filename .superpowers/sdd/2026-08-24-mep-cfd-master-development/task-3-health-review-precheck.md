# Task 3 health/review pre-implementation check

**Scope:** read-only integration review of Task 3 against the master plan, Task 1/2 briefs, controller rulings, current field pipeline, and its schema/tests.  No implementation or test execution was performed.

## Outcome

Task 3 can safely begin only after treating `case_evidence.v1` as the sole source input to health and replacing the current field-pipeline shortcut.  The current implementation permits a legacy `cfd_result_gate` response with `citation_status=DESIGN_CITABLE` to write field-job terminal status `complete` (`field_pipeline_job.py:41-109`, `288-385`; regression at `tests/test_field_pipeline_job.py:123-174`).  That conflicts with the Task-3 requirement that calculation completion neither implies a human approval nor `design_ready`.

The Task-1 and Task-2 contract producers do not yet exist in this worktree (`cfd_status_catalog.py`, `case_evidence.v1.schema.json`, `case_health.v1.schema.json`, `case_review.v1.schema.json`, and `cfd_evidence.py` are absent).  This is expected before Tasks 1/2 land, but Task 3 must not recreate their authority or add permissive fallbacks.

## Required source-of-truth flow

```text
authoritative raw artifacts --Task 2 recomputation--> case_evidence.v1 (bytes/hash)
                                                |             |
                                                |             +--> immutable review target
                                                v
                               Task 3 health projection + citation table
                                                |
                                                +--> field manifest snapshot only
```

`cfd_result_gate.evaluate_body_fitted_case()` remains a legacy/raw-artifact semantic evaluator, not the Task-3 citation authority.  It currently returns `DESIGN_CITABLE` directly when its older conditions pass (`cfd_result_gate.py:428-624`); retain it for Task-2 regression, but do not consume its `design_ready`, `citable`, or citation string as a field terminal truth after the new evidence contract exists.

## Evidence -> health mapping

Health must preserve the Task-1 exact ordered eight source checks as a closed keyed object and add only derived `design_ready`.  Copy the evidence check's `status`, unique `reason_codes`, and unique `evidence_refs`; derive Korean `impact` and `next_actions` only from the Task-1 catalog.  Do not manufacture a PASS from a run-manifest flag, report, cache, UI request, or review.

| Health check | Task-2 authoritative basis | Required fail-closed behavior |
|---|---|---|
| `geometry_valid` | Independently selected safe `geometry.v2`, then current surface source path/hash binding | Unsafe, generated, invalid, stale, or unbound geometry is `BLOCKED`; never trust a surface path alone. |
| `bc_reviewed` | Current surface/patch/terminal and thermal-input/run bindings, as recomputed by Task 2 | Missing, malformed, or stale cross-reference is `BLOCKED`; `run.design_ready` is not evidence. |
| `mesh_checked` | Current schema-valid `mesh_manifest.v1`, quality status, and current surface binding | Missing/unsafe core mesh is `BLOCKED`; manifest `FAIL` maps `FAIL`; warnings do not self-promote. |
| `solver_converged` | Current run, canonical standalone `thermal_progress.json`, result, and preserved semantic result-gate checks | Thermal-progress copies must canonical/deep-equal; mismatch is `BLOCKED`; solver failure is `FAIL`; incomplete evaluation is `NOT_EVALUATED`. |
| `numerics_verified` | Current run/system numerical provenance only | Numerical-sensitivity preparation/frozen pair cannot produce PASS (Task2-A). Missing or non-final provenance stays `NOT_EVALUATED` or `BLOCKED` if supplied/unsafe. |
| `grid_verified` | Exactly one valid current GCI provenance match | No match is `NOT_EVALUATED`; more than one is `BLOCKED` / `AMBIGUOUS_GCI_EVIDENCE`; never choose first glob/PASS. |
| `benchmark_validated` | Independently validated supplied benchmark evidence bound to this selected chain | Absent optional/profile-gated evidence is `NOT_EVALUATED`; supplied unreadable/stale/tampered/wrong-root/wrong-case evidence is `BLOCKED`. |
| `field_calibrated` | Independently validated supplied field evidence bound to this selected chain | Same absent-vs-supplied-invalid distinction as benchmark. |
| `design_ready` | Derived; no artifact reference of its own | Must never be made PASS by review. Recommended invariant: PASS only if all eight source checks are PASS; otherwise propagate the highest-severity required source result and source references. See controller question 4 for confirmation. |

The review is a human decision about `case_evidence.v1` bytes, not a ninth source check.  A valid approval may satisfy the citation decision table's review condition; it cannot repair an evidence check or alter `design_ready`.

## Citation truth table (F4, exact highest-first precedence)

Task 3 must first compare the health artifact's version/table byte-for-value to `CITATION_DECISION_TABLE_VERSION` and `CITATION_DECISION_TABLE` exported by the Task-1 catalog.  A mismatch is a contract failure, never a locally substituted table.  Evaluate the following rows in order; first match wins.

| Order | Condition | Citation status |
|---:|---|---|
| 1 | Evidence invalid/stale; or a review that is referenced or required by the purpose is invalid/stale | `CITATION_BLOCKED` |
| 2 | Any purpose-required source check is `FAIL` or `BLOCKED` | `CITATION_BLOCKED` |
| 3 | Any purpose-required source check is `NOT_EVALUATED` | `NOT_EVALUATED` |
| 4 | Purpose is `benchmark` (design citation is not applicable) | `NOT_EVALUATED` |
| 5 | Purpose is `screening`, or identity is `legacy_case_ref` | `SCREENING_ONLY` |
| 6 | Current persisted review is `APPROVED` and every purpose-required source check is `PASS` | `DESIGN_CITABLE` |
| 7 | Otherwise (notably a non-screening case with no current approval) | **controller decision needed**; recommended `NOT_EVALUATED`, because a required review has not been evaluated, not because raw evidence failed. |

Purpose requirements are fixed by Task1-B: screening = geometry/BC/mesh/solver/numerics, no review, ceiling `SCREENING_ONLY`; design-review candidate and field validation = all eight + current approval; benchmark = first seven without field/review and is not applicable for design citation.  Future optional modules may not change any row unless a declared profile requires them.  The benchmark/not-applicable row must precede the screening row as Task1-F requires.

## Review ID, hash, invalidation, and append-only lifecycle

Persist exactly one immutable record per review file, with schema-approved `decision` only `APPROVED` or `REJECTED`, reviewer, non-empty reason, creation timestamp, immutable `review_id`, typed target `{contract: "case_evidence.v1", path, sha256}`, and optional `supersedes_review_id`.  The target SHA-256 is the SHA-256 of the exact evidence file bytes read by `create_review`, after containment and `case_evidence.v1` validation.

1. **Create:** safely resolve and validate the target; compare the client expected hash (see ambiguity 1) with freshly read bytes; re-hash immediately before persistence.  Mint an unpredictable review ID (UUIDv4/32 lowercase hex is suitable) rather than deriving it only from mutable/display fields.  Persist the target hash and never rewrite the record.
2. **Validate:** re-resolve the root-relative target path, reject escape/reparse/symlink and contract/schema errors, read current target bytes, and recompute hash.  A mismatch or unreadable target derives `INVALIDATED`; it never edits `decision`.
3. **Supersede:** a new valid review references an existing valid review ID with the same typed target *and identical target hash*.  The old record derives `SUPERSEDED`; do not mutate it.  A review must not supersede a different case/evidence revision or an already-invalid target.
4. **Current:** only a valid, not-superseded `APPROVED` record for the current evidence hash can satisfy row 6.  `REJECTED` is a current human decision but cannot satisfy approval; whether it should produce `NOT_EVALUATED` or `CITATION_BLOCKED` is controller ambiguity 3.

Safe persistence is **immutable-file publication**, not a read/modify/write JSON array: write a new review file in the chosen safe output directory with a collision-free staging sibling; write canonical UTF-8 JSON; `flush()` + `os.fsync()` the file; close; atomically `os.replace()` the staging file into the final unique review-ID path; best-effort fsync the directory where supported; clean the staging sibling on every exception.  Never overwrite an existing review ID.  The existing `cfd_gci_job._atomic_json()` only uses `temporary.replace()` (`cfd_gci_job.py:132-141`) and does not flush/fsync, so it is insufficient for the controller's Task-2 publish rule and should not be reused unchanged for review records.

Concurrent review creation needs a small per-output-directory lock or exclusive final-name creation plus retry.  Revalidate the target after lock acquisition and before publish.  This preserves append-only history even if reviewers create records concurrently; a resulting unresolved pair of same-revision reviews must be surfaced rather than silently choosing by filesystem order.

## Field-pipeline compatibility change

Keep the v1 terminal vocabulary: `complete` and `analysis_complete_not_citable` (`field_pipeline_job.py:24-25`; schema enum at `field_pipeline_job.v1.schema.json:15-20`).  Do not add a terminal `design_citable`, `approved`, or `design_ready` status.  `complete` must remain a raw pipeline completion terminal for backward compatibility; it must no longer mean that the case is design-citable.

For every finished job, safely recompute/currently load health from the selected current `case_evidence.v1`; then snapshot only these additive fields into the job manifest:

```json
{
  "case_evidence_path": "_body_solver/<case>/case_evidence.v1.json",
  "case_evidence_sha256": "<64 lowercase hex>",
  "case_health_path": "_body_solver/<case>/case_health.v1.json",
  "case_health_sha256": "<64 lowercase hex>",
  "citation_status": "SCREENING_ONLY|NOT_EVALUATED|CITATION_BLOCKED|DESIGN_CITABLE",
  "citation_blockers": ["<stable reason code>"],
  "review_summary": {"status": "...", "review_id": "..."}
}
```

The fields must be snapshots, never authoritative input.  On terminal refresh, recompute evidence/health and replace stale snapshot values; if recomputation fails, retain raw terminal completion and expose `analysis_complete_not_citable` plus fail-closed blockers.  `run_job()` must still avoid rerunning a terminal analysis.  Preserve existing consumers by accepting old manifests without the new keys and by continuing to map all non-`DESIGN_CITABLE` health states to `analysis_complete_not_citable` at the UI/claim layer.  The v1 schema currently has `additionalProperties: true` (`field_pipeline_job.v1.schema.json:27`), so additions are mechanically compatible; do not use that permissiveness to accept arbitrary citation values—validate them in code and tighten/add v2 only when Task 7 owns migration.

## Focused test matrix

| Area | Must prove |
|---|---|
| Health projection | Fixed nine-key order/shape; source reason/evidence refs preserved; catalog copy deterministic; health schema/table equals catalog exactly. |
| Evidence validity | Valid evidence then mutate each target artifact, evidence file, path, or hash: health/citation blocks rather than using cached evidence. Include Task-2 canonical thermal-progress mismatch and zero/multiple GCI behavior. |
| F4 table | One table-driven fixture for every row and precedence collision: stale evidence beats everything; `FAIL/BLOCKED` beats `NOT_EVALUATED`; `NOT_EVALUATED` beats screening; benchmark row precedes screening; legacy never citable; optional module cannot alter result. |
| Design readiness | Approved review + failed mesh remains derived non-PASS; review never changes any source check; all-eight policy and purpose-specific citation are independently tested. |
| Review creation | Reject non-evidence target, escape/symlink target, schema-invalid evidence, wrong expected hash, missing reviewer/reason, and output-root escape. Stored hash matches bytes actually approved. |
| Review lifecycle | Target mutation => `INVALIDATED`; new review superseding same target/hash => old `SUPERSEDED`; cross-target/hash supersession rejected; immutable old bytes unchanged; concurrent/collision path never overwrites. |
| Field compatibility | Existing v1 fixtures still load; raw solve ends terminal even without citation; terminal refresh does not relaunch; a forged legacy result-gate `DESIGN_CITABLE` cannot produce health/design citation; stale evidence updates blockers/path/hash. |
| Regression boundary | `tests/test_cfd_case_health.py`, `tests/test_cfd_review.py`, `tests/test_field_pipeline_job.py`, and `tests/test_cfd_result_gate.py`; retain Task-2 evidence tests in the focused run because health relies on their authority. |

## Controller rulings still needed before coding

1. **Expected-hash API:** `create_review()` lacks the Task-4 `expected_target_sha256` parameter even though Task1-G requires client comparison before authoritative rehash.  Add it to Task 3's API (recommended) or define an equivalent caller-owned atomic protocol; the latter leaves a TOCTOU gap.
2. **Review discovery/currentness:** `build_case_health(evidence_path, projects_root)` has no review path/root parameter, while design/field citation requires a current approval.  Rule the canonical safe review directory and discovery/index policy, including how health identifies the current review without recursive glob/first-file selection.
3. **No approval/rejection outcome:** Rule row 7 explicitly. Recommended: missing required approval and a valid `REJECTED` review are `NOT_EVALUATED`; only invalid/stale review is `CITATION_BLOCKED`. If rejection is intended as an explicit citation block, use `CITATION_BLOCKED` with a stable `REVIEW_REJECTED` reason and add that as an ordered decision-table row.
4. **`design_ready` semantics:** Rule whether it is (recommended) raw-evidence-only/all eight PASS, or a purpose-specific derived check. It must be independent of human approval, but the plan does not state its exact all-eight aggregation/status precedence.
5. **Multiple unsuperseded records:** With only singular `supersedes_review_id`, rule whether multiple valid same-target reviews are allowed and how a current approval/rejection is selected. Recommended: preserve all, mark an incompatible set `REVIEW_HISTORY_AMBIGUOUS`/citation blocked until an explicit superseding review resolves it; never select newest or first by filesystem order.
6. **Field `complete` semantics:** Confirm the compatibility policy above: `complete` remains raw-analysis completion while citable claims are solely the additive health snapshot. Renaming/redefining this status would break the current terminal-state contract and existing test expectation.

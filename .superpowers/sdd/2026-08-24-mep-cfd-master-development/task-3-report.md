# Task 3 report — Case Health and immutable human review

## Scope and baseline

- Worktree: `C:\Users\User\Documents\MEP CFD Studio\.worktrees\case-evidence-review-gate`
- Baseline HEAD: `458b66f5cf0c007ecbef75f77caace0cab4aa9b6`
- Created `cfd_case_health.py`, `cfd_review.py`, `tests/test_cfd_case_health.py`, and `tests/test_cfd_review.py`.
- Modified only `field_pipeline_job.py` and `tests/test_field_pipeline_job.py` beyond those new files.
- No Task 4 API/UI/report producer, schema, catalog, Task 2 evidence, or legacy result-gate file was changed.

## RED evidence

The complete required Task 3 test matrix was written before either production module existed. The authenticated command was:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_case_health.py tests/test_cfd_review.py tests/test_field_pipeline_job.py
```

The expected initial RED was:

```text
ERROR tests/test_cfd_case_health.py
ERROR tests/test_cfd_review.py
ERROR tests/test_field_pipeline_job.py
3 errors in 1.55s
```

All three collection failures were caused by the intentionally missing `cfd_case_health` module. The first implementation run then produced:

```text
3 failed, 43 passed in 18.67s
```

Those failures exposed two stale test expectations and one fixture rule: changing evidence bytes correctly invalidates the prior hash-bound rejection, and incomplete field fixtures correctly map to fail-closed `CITATION_BLOCKED` rather than a legacy result-gate status. After correcting those fixtures, the Task 3 matrix reached:

```text
46 passed in 15.80s
```

Self-review added two further test-first corrections:

1. A `DESIGN_CITABLE` health decision code must not be copied into field `citation_blockers`.

   RED:

   ```text
   1 failed, 13 deselected in 1.18s
   ```

   GREEN:

   ```text
   1 passed, 13 deselected in 0.87s
   ```

2. A schema-invalid direct review with a malformed target spelling must block instead of being ignored as another target.

   RED:

   ```text
   1 failed, 13 deselected in 2.20s
   ```

   GREEN:

   ```text
   1 passed, 13 deselected in 1.32s
   ```

## Implemented health authority

- `build_case_health()` resolves one contained schema-valid `case_evidence.v1` file, calls `validate_case_evidence()` against current raw artifacts, and never reads citation truth from `cfd_result_gate`.
- The health projection has the exact ordered eight Task 1 source checks plus `design_ready`. Source status, reasons, and evidence references are copied; Korean impact and next actions come only from the Task 1 catalog.
- `design_ready` aggregates all eight source checks independently of purpose and review using `FAIL`, then `BLOCKED`, then `NOT_EVALUATED`, then `PASS` precedence. Review never mutates a source check or this aggregation.
- The implementation compares the live Task 1 decision-table version/table and nine-key order to `case_health.v1.schema.json`; drift raises `CITATION_DECISION_TABLE_MISMATCH` instead of substituting local rules.
- Citation evaluation follows all eight controller-ordered rows, including evidence/review invalidity, source failure, current rejection, source not-evaluated, benchmark, screening/legacy ceiling, current approval, and missing approval.
- A schema-valid `case_health.v1.json` snapshot is atomically written beside the evidence file. Its evidence link binds the current evidence bytes by normalized path and SHA-256.

## Implemented immutable review lifecycle

- `create_review()` requires `projects_root` and `expected_target_sha256`, schema-validates the Case Evidence target, validates the optimistic hash, acquires a per-canonical-`_reviews` directory lock, then resolves and hashes the target again immediately before publish.
- Review IDs are `review-` plus UUIDv4 lowercase hex. Files are direct canonical `_reviews/<review_id>.case_review.v1.json` children.
- Publication uses same-directory staging, UTF-8 canonical JSON formatting, file flush/fsync, collision recheck, atomic replacement into a never-existing name, staging cleanup on every exception, and best-effort directory fsync.
- Direct matching children are all validated. No recursive glob, timestamp ordering, newest selection, or first selection exists.
- Supersession accepts only current valid leaf IDs for the identical target path and hash. Old records remain byte-for-byte unchanged. One-leaf supersession can leave a fork ambiguous; one new record superseding every current leaf resolves it.
- Multiple current leaves derive `REVIEW_HISTORY_AMBIGUOUS`. Target mutation derives `REVIEW_TARGET_CHANGED`. Cross-target/hash supersession and malformed graph edges are rejected.

## Field v1 compatibility

- Both `complete` and `analysis_complete_not_citable` remain terminal raw-analysis labels. A terminal refresh never reaches OCC/mesh/solver execution.
- Raw `stage=complete` remains independent of citation state.
- `complete` is now emitted only when freshly rebuilt and revalidated Case Evidence produces current Case Health `DESIGN_CITABLE`; all other citation states map to `analysis_complete_not_citable`.
- Terminal refresh persists only validated evidence/health path and SHA-256 snapshots, citation status/blockers, and review summary. If evidence cannot be revalidated, stale path/hash snapshots are removed and the raw terminal job remains available with `CASE_EVIDENCE_NOT_FOUND`.
- Old permissive v1 manifests without new fields still load. Legacy `result_trust.v1` output is not consulted, and a forged legacy `DESIGN_CITABLE` response cannot promote the field job.

## Final focused verification

Exact required authenticated command:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_case_health.py tests/test_cfd_review.py tests/test_field_pipeline_job.py tests/test_cfd_result_gate.py tests/test_cfd_evidence.py
```

Fresh final result after self-review fixes:

```text
132 passed, 7 warnings in 60.08s (0:01:00)
```

The seven warnings are existing `ezdxf`/`pyparsing` deprecation warnings emitted by the untouched Task 2 real-DXF fixture. No test failed or skipped in this focused boundary.

No repository-wide full-suite result is claimed; the controller will run it independently.

## Self-review and remaining concerns

- The intended source/test diff is exactly the six authorized files. Task 1 schemas/catalog and Task 2 evidence/result-gate sources remain untouched.
- Review concurrency is protected for cooperating writers by both a process-local thread lock and an OS file lock. UUID collision tests force a collision under concurrent creation and verify two immutable valid records survive without overwrite.
- Review history ignores old-hash records for current selection so a new evidence revision can be reviewed without an obsolete review permanently blocking it; `validate_review()` still reports the old record as `REVIEW_TARGET_CHANGED`.
- Current Task 2 evidence production is intentionally legacy screening evidence, so a real present-day field run remains non-citable. Tests use a schema-valid future `case_identity` fixture only to exercise the Task 1 design/field decision rows; this is not evidence that a future design-profile producer exists.
- The tests validate filesystem, schema, hash, concurrency, decision, and terminal-refresh behavior. They do not run FreeCAD, OpenFOAM, a real GCI study, or a live field-DXF workflow.

## Review fix round 1 — publication race closure

### Finding dispositions

1. **Health evidence/raw/review TOCTOU.** Health creation now builds a candidate into a flushed/fsynced same-directory staging file, then performs a second complete projection immediately before atomic replacement. The second projection re-resolves and re-hashes the evidence file, reruns `validate_case_evidence()` over the complete raw authority chain, and re-discovers current review leaves. If any authoritative projection differs, staging is removed and the operation retries from the new state; repeated instability fails closed with `CASE_HEALTH_CHANGED_DURING_PUBLISH`. Deterministic tests mutate both stored evidence and a raw VTU after the first projection, and add a rejecting review fork after an initially approved projection.
2. **Late review-target mutation.** `create_review()` now re-resolves, schema-validates, and re-hashes the Case Evidence target after review ID/record construction and directly before immutable publication while the directory lock remains held. Any path/schema/hash change raises stable `REVIEW_TARGET_CHANGED`, with no final record or staging file left behind.
3. **Never-overwrite review publication.** Review staging remains same-directory and fully flushed/fsynced, but final publication now uses exclusive `os.link(staging, final)` followed by staging unlink and best-effort directory fsync. A racing existing name raises `FileExistsError`; the caller retries a new UUID without modifying the existing bytes. The deterministic race injects different bytes at the first final name and verifies those bytes remain unchanged while a second unique valid review survives.
4. **Current target schema validation.** `validate_review()` now rereads the current target and validates it against `case_evidence.v1` with Draft 2020-12. A review whose stored hash is tampered to match a schema-invalid current target returns `REVIEW_TARGET_SCHEMA_INVALID` rather than validating successfully.

Field snapshot assembly was also tightened to bind one final byte pair: it freshly revalidates Case Evidence after health publication, hashes captured evidence/health bytes, requires the on-disk health to equal the returned health and reference the same evidence path/hash, checks that citable health still has an approved review summary, then revalidates evidence and compares both files' bytes again before returning snapshot hashes. Any late evidence, health, rejection, or ambiguity race clears the citable snapshot and maps the terminal job fail-closed without rerunning the solver.

### RED and GREEN evidence

The combined deterministic finding selection was written first and run with:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_case_health.py tests/test_cfd_review.py tests/test_field_pipeline_job.py -k "final_health or target_mutation_after_locked or atomic_publication or hash_matching_schema_invalid or field_snapshot_rejects"
```

Initial RED:

```text
7 failed, 47 deselected in 8.56s
```

Targeted GREEN after the four fixes:

```text
7 passed, 47 deselected in 7.20s
```

Self-review then added two later-boundary regressions:

- Post-health review ambiguity: `1 failed, 15 deselected in 0.76s` -> `1 passed, 15 deselected in 1.10s`.
- Health-byte mutation during review-summary assembly: `1 failed, 16 deselected in 0.94s` -> `1 passed, 16 deselected in 0.81s`.

The complete Task 3 matrix after updating one stale mock to provide a genuinely bound evidence/health pair was:

```text
54 passed in 27.65s
```

### Final review-fix verification

The fresh exact five-file focused command was:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_case_health.py tests/test_cfd_review.py tests/test_field_pipeline_job.py tests/test_cfd_result_gate.py tests/test_cfd_evidence.py
```

Final result:

```text
141 passed, 7 warnings in 53.21s
```

The seven warnings are the unchanged Task 2 real-DXF fixture's `ezdxf`/`pyparsing` deprecation warnings. No full-suite, CAD, solver, live field run, or real GCI execution result is claimed.

### Review-fix self-review and residual concern

- All health retry paths remove their staging file on mismatch or exception. Review collision, link failure, and fsync failure paths likewise clean staging and retain the directory lock until the attempt resolves.
- Containment, reparse rejection, canonical direct `_reviews` discovery, UUIDv4 validation, append-only supersession, and current-leaf ambiguity semantics were not weakened.
- Exclusive hard-link publication requires a filesystem supporting same-volume hard links; the required Windows-local NTFS worktree supports this. Unsupported filesystems fail closed instead of falling back to overwriting replacement.
- As with the original Task 3 delivery, these tests validate contracts and deterministic filesystem races, not real solver/CAD execution.

## Review fix round 2 — review-state serialization

### Remaining finding disposition

The canonical evidence `_reviews` directory now has one shared synchronization protocol exposed by `cfd_review.review_state_lock()`. It combines the existing process-local per-directory `RLock` with the OS lock file and tracks same-thread depth, so nested field -> health -> review acquisition retains one OS lock without deadlocking. `create_review()` uses the same public context manager and continues to enforce the canonical direct `_reviews` directory.

`build_case_health()` now holds that review-state lock across its final evidence/raw revalidation, complete review discovery and decision projection, staging verification, and atomic health replacement. A cooperating rejecting or forking review writer therefore cannot enter review discovery until the published health represents one serialized review state; the next health projection observes the new rejection/fork and blocks citation.

For terminal field refresh, `run_job()` holds the same outer lock across health rebuilding/reprojection, review-summary capture, evidence/health byte-pair verification, terminal status/snapshot update, and the actual atomic field-manifest `_publish()`. The nested health/review acquisitions are reentrant. A legacy terminal manifest whose case directory no longer exists uses a no-op lock and still fails closed without entering OCC, mesh, or solver execution.

The deterministic concurrency tests pause health replacement and field manifest publication while a valid `create_review()` call attempts a rejecting fork. In both cases the cooperating writer remains blocked until the authoritative publication finishes. The subsequent health/field refresh is `CITATION_BLOCKED` / `analysis_complete_not_citable`. An injected health fsync failure also proves staging cleanup and lock release by successfully creating a later review.

### RED and GREEN evidence

The new four-test concurrency selection was written before the synchronization implementation. Initial RED was:

```text
3 failed, 1 passed, 56 deselected in 2.98s
```

The failures were the absent shared lock API plus unblocked review writers during health and field publication. The failure-cleanup test already passed. After the minimal implementation, the same selection was GREEN:

```text
4 passed, 56 deselected in 3.71s
```

The complete Task 3 set then exposed one legacy missing-case regression (`59 passed, 1 failed in 31.01s`); the lock helper was restricted to real case directories, and the focused legacy/serialization/nesting regressions passed `3 passed in 4.11s`.

### Final focused verification

Exact authenticated command:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_case_health.py tests/test_cfd_review.py tests/test_field_pipeline_job.py tests/test_cfd_result_gate.py tests/test_cfd_evidence.py
```

Fresh final result:

```text
145 passed, 7 warnings in 73.39s (0:01:13)
```

The warnings remain the untouched Task 2 real-DXF fixture's `ezdxf`/`pyparsing` deprecations. `py_compile` for all three modified source modules and `git diff --check` both completed successfully. No repository-wide full-suite, CAD, solver, live field, or real GCI result is claimed.

### Self-review and residual concern

- The lock spans the actual field manifest atomic publication, not only snapshot construction.
- Same-thread nested acquisitions release depth and the OS lock in `finally` paths; health staging cleanup and later lock reuse are covered by deterministic failure injection.
- Canonical directory containment and reparse checks remain centralized in `cfd_review`; review writers cannot select a noncanonical output directory.
- The protocol serializes all cooperating review writers/read-publishers. An actor that bypasses `create_review()` and directly edits review-directory children is outside that cooperative protocol; the existing final reprojection and byte checks narrow and fail closed on detected authoritative mutation, while immutable review publication remains the supported write path.

## Review fix round 3 — review-lock containment hardening

### Finding dispositions

1. **Lexical terminal-case escape.** Field terminal locking no longer relies on lexical `Path.relative_to()`. The candidate case directory is now strict-resolved through the review module's project-directory safety boundary, which requires a real directory, physical containment beneath `projects_root`, and a non-reparse lexical ancestry. Missing, escaped, or reparse-backed legacy cases select a no-op lock context so the existing inner snapshot assembly can fail closed. A forged terminal `result_case` shaped as `root/inside/../../outside-case` now returns normally as `analysis_complete_not_citable`, exposes `CASE_EVIDENCE_NOT_FOUND`, clears stale evidence/health paths and hashes, marks the review summary invalid, and never invokes OCC/solver work.
2. **Unsafe review lock-file traversal.** The canonical `.case_review.lock` is no longer opened with following `Path.open("a+b")` semantics. Existing entries are checked with `lstat` and rejected unless they are regular, non-reparse files. An absent lock is created only with `O_CREAT|O_EXCL`; `O_NOFOLLOW` is included where the platform exposes it. After `os.open`, `fstat` and `lstat` identities must match before any lock-file write or OS lock, and identity is checked again immediately before and after OS lock acquisition. All rejected descriptors are closed. The lock file is persistent and is never unlinked/recreated, avoiding a split canonical lock among cooperating processes.

The symlink regression exercises both direct `review_state_lock()` acquisition and `create_review()`. Both reject with stable `REVIEW_LOCK_UNSAFE`, leave the external sentinel bytes unchanged, retain the symlink, and publish no review record.

### RED and GREEN evidence

The traversal test and the two-parameter unsafe-lock test were added before production changes. Initial targeted RED:

```text
3 failed in 2.11s
```

Observed failures were the uncaught terminal-refresh `ValueError`, direct review lock acquisition succeeding through the symlink, and review creation succeeding through the same unsafe lock path. After the minimal containment/open changes, targeted GREEN was:

```text
3 passed in 1.88s
```

The complete Task 3 matrix then passed:

```text
63 passed in 27.98s
```

### Final focused verification

Exact authenticated command:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_case_health.py tests/test_cfd_review.py tests/test_field_pipeline_job.py tests/test_cfd_result_gate.py tests/test_cfd_evidence.py
```

Fresh result:

```text
148 passed, 7 warnings in 62.47s (0:01:02)
```

The warnings remain the unchanged Task 2 real-DXF fixture's `ezdxf`/`pyparsing` deprecations. `py_compile` for both modified source modules and `git diff --check` completed successfully. No repository-wide full-suite, CAD, solver, live field, or real GCI result is claimed.

### Self-review and residual concern

- The safe lock path never opens a known symlink/reparse/non-regular entry and verifies that the opened descriptor is the same regular file currently named by the canonical path.
- Collision between absent-file observation and exclusive creation retries without unlinking anything. Other open/lstat races fail closed with `REVIEW_LOCK_UNSAFE`.
- Same-thread reentrancy and process serialization are unchanged after the outermost safe descriptor is established.
- A hostile actor with direct filesystem mutation rights can always replace a pathname after a completed identity check; the implementation rechecks after OS lock acquisition, while supported cooperating writers never replace or unlink the persistent canonical lock file.

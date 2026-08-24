### Task 3: Case Health와 human Review를 분리한다

**Files:**

- Create: `cfd_case_health.py`
- Create: `cfd_review.py`
- Create: `tests/test_cfd_case_health.py`
- Create: `tests/test_cfd_review.py`
- Modify: `field_pipeline_job.py`
- Test: `tests/test_field_pipeline_job.py`

**Interfaces:**

```python
def build_case_health(
    evidence_path: Path,
    *,
    projects_root: Path,
) -> dict: ...

def create_review(
    target_path: Path,
    *,
    reviewer_id: str,
    decision: Literal["APPROVED", "REJECTED"],
    reason: str,
    output_dir: Path,
) -> dict: ...

def validate_review(review_path: Path, *, projects_root: Path) -> list[dict]: ...
```

- [ ] **Step 1: design-ready derivation의 실패 test를 작성한다**

  ```python
  def test_human_approval_cannot_override_failed_mesh(tmp_path):
      evidence = evidence_with(tmp_path, mesh_checked="FAIL")
      review = approved_review(tmp_path, evidence)
      health = build_case_health(Path(evidence["path"]), projects_root=tmp_path)
      assert review["decision"] == "APPROVED"
      assert health["checks"]["design_ready"]["status"] == "FAIL"
  ```

- [ ] **Step 2: health prerequisite table tests를 추가한다**

  `field_calibrated=NOT_EVALUATED`인 screening case는 정상 조회 가능하지만 `DESIGN_CITABLE`로 승격하지 않는다. `grid_verified`가 필요한 목적과 필요하지 않은 목적을 `purpose`로 분리한다.

- [ ] **Step 3: Case Health read model을 구현한다**

  `geometry_valid`, `bc_reviewed`, `mesh_checked`, `solver_converged`, `numerics_verified`, `grid_verified`, `benchmark_validated`, `field_calibrated`를 고정 순서로 만들고, 각 check에 `status`, `reason_codes`, `impact`, `next_actions`, `evidence_refs`를 둔다.

- [ ] **Step 4: Review record를 target hash에 묶는다**

  Review 생성 시 target file의 현재 SHA-256을 저장한다. target 변경 시 review는 `INVALIDATED`이며 자동 이전하지 않는다. supersede는 새 review가 이전 review ID를 참조하는 append-only 방식으로 처리한다.

- [ ] **Step 5: field pipeline의 terminal 상태를 정리한다**

  `analysis_complete_not_citable`을 유지하고, health의 blocker list와 current evidence path/hash를 manifest에 추가한다. 계산 완료가 review 승인 또는 design-ready를 암시하지 않게 한다.

- [ ] **Step 6: focused regression을 실행한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests/test_cfd_case_health.py tests/test_cfd_review.py tests/test_field_pipeline_job.py tests/test_cfd_result_gate.py
  ```

  Expected: PASS.

- [ ] **Step 7: commit한다**

  ```powershell
  git add cfd_case_health.py cfd_review.py field_pipeline_job.py tests/test_cfd_case_health.py tests/test_cfd_review.py tests/test_field_pipeline_job.py
  git commit -m "feat: separate case health from human review"
  ```

## Controller rulings and exact health/review semantics

1. `case_evidence.v1` plus current raw-artifact revalidation is the only health input. The legacy `cfd_result_gate` remains a Task-2 semantic helper; its `design_ready`, `citable`, or citation string can never directly create Task-3 health or field terminal truth.
2. Extend the public API to require optimistic concurrency:

   ```python
   def create_review(
       target_path: Path,
       *,
       projects_root: Path,
       expected_target_sha256: str,
       reviewer_id: str,
       decision: Literal["APPROVED", "REJECTED"],
       reason: str,
       output_dir: Path | None = None,
       supersedes_review_ids: Sequence[str] = (),
   ) -> dict: ...
   ```

   Recompute the target hash after acquiring the review-directory lock and immediately before immutable publish; mismatch raises the stable `REVIEW_TARGET_CHANGED` error.
3. Canonical review discovery is the direct, non-recursive directory `evidence_path.parent/_reviews`. Files are unique `<review_id>.case_review.v1.json`; validate every matching direct child. Do not use recursive globbing, timestamps, or first-file selection. `build_case_health()` discovers this directory from the evidence path.
4. Review IDs are UUIDv4 lowercase hex (`review-` + 32 hex). Publish each closed record with same-directory staging, flush/fsync, atomic replace into a never-existing final name, and directory fsync where supported. A per-review-directory lock plus post-lock target revalidation is mandatory. Never overwrite a review record.
5. A review may supersede only valid current leaves for the identical evidence path/hash. `supersedes_review_ids` may list one or all leaf IDs. More than one unsuperseded valid leaf is `REVIEW_HISTORY_AMBIGUOUS` and citation-blocking for review-required purposes until one new record supersedes every current leaf; never choose newest/first. Old bytes never change.
6. Citation precedence is exactly:
   1. invalid/stale evidence, or invalid/stale applicable review -> `CITATION_BLOCKED`;
   2. any purpose-required source check `FAIL`/`BLOCKED` -> `CITATION_BLOCKED`;
   3. current `REJECTED` review for a review-required purpose -> `CITATION_BLOCKED` with `REVIEW_REJECTED`;
   4. any purpose-required source check `NOT_EVALUATED` -> `NOT_EVALUATED`;
   5. benchmark purpose -> `NOT_EVALUATED` (design citation not applicable);
   6. screening purpose or legacy identity -> `SCREENING_ONLY`;
   7. current unambiguous `APPROVED` review plus all purpose-required checks PASS -> `DESIGN_CITABLE`;
   8. missing required approval -> `NOT_EVALUATED` with `REVIEW_REQUIRED`.

   The Task-1 catalog/schema decision table must contain the same rows; Task 3 rejects any version/table mismatch instead of substituting local rules.
7. `design_ready` is raw-evidence-only and independent of purpose/review. Aggregate all eight source checks: any `FAIL` => `FAIL`; else any `BLOCKED` => `BLOCKED`; else any `NOT_EVALUATED` => `NOT_EVALUATED`; else `PASS`. Preserve the union of stable reasons/evidence refs. Human approval never changes a source check or `design_ready`.
8. Preserve field v1 compatibility and its existing safe meaning: both `complete` and `analysis_complete_not_citable` remain terminal raw-analysis states, so refresh never relaunches the solver. `complete` remains reserved for current recomputed health `DESIGN_CITABLE`; every other citation status maps to `analysis_complete_not_citable`. Raw `stage=complete` records calculation completion separately. Refresh may transition between the two terminal labels as evidence/review changes.
9. Add only validated snapshot fields to the permissive v1 field manifest: evidence path/hash, health path/hash, citation status/blockers, and review summary. They are display/recovery snapshots, never authority. Old manifests without them still load; a forged legacy result-gate `DESIGN_CITABLE` must not promote status.
10. Required tests are the complete matrix in `task-3-health-review-precheck.md`, including every decision row/precedence collision, all-eight `design_ready`, missing versus rejected approval, forked review ambiguity/resolution, concurrent collision safety, target mutation invalidation, old field fixtures, terminal refresh without rerun, and legacy-gate forgery.

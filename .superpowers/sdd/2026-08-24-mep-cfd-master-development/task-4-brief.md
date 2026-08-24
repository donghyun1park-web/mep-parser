### Task 4: Studio와 report에 Evidence & Review Gate를 노출한다

**Files:**

- Modify: `cfd_studio.py` around `body_result_payload()`, `StudioHandler.do_GET()`, `StudioHandler.do_POST()`, body results page.
- Modify: `cfd_report.py` in `generate_body_fitted_report()`.
- Modify: `cfd_advice.py` to group actions by input/model/evidence/field.
- Test: `tests/test_studio_workflow.py`
- Test: `tests/test_body_fitted_report.py`
- Test: `tests/test_cfd_advice.py`

**Interfaces:**

- GET `/api/case-health/<case-name>` → current recomputed `case_health.v1`.
- POST `/api/case-review` with `{case, reviewer_id, decision, reason, target_sha256}`.
- Existing `/api/body-results/<case>` remains backward compatible and adds `case_health` and `review_summary` fields.

- [ ] **Step 1: API contract tests를 먼저 작성한다**

  ```python
  def test_case_health_endpoint_never_uses_summary_cache(studio_client, case):
      mutate_authoritative_run_manifest(case)
      response = studio_client.get(f"/api/case-health/{case.name}")
      assert response.json()["checks"]["solver_converged"]["status"] == "BLOCKED"
  ```

- [ ] **Step 2: local-only POST와 stale review tests를 추가한다**

  기존 `_local_post_allowed()` 보호를 그대로 적용한다. 현재 target hash와 다른 승인 요청은 HTTP 409와 `REVIEW_TARGET_CHANGED`를 반환한다.

- [ ] **Step 3: thin endpoint를 구현한다**

  `cfd_studio.py`는 path validation과 HTTP 변환만 담당하고 모든 domain 판단을 새 service modules에 위임한다. 기존 single worker queue, solver lock, UAT mutex를 변경하지 않는다.

- [ ] **Step 4: 신뢰도 카드 UI를 구현한다**

  기본 카드에 사용자용 상태, 사용할 수 있는 범위, blocker, next action을 표시한다. residual/y+/phi/hash는 “근거 보기” 상세에 둔다. `SCREENING_ONLY`와 `CITATION_BLOCKED`는 성공 초록색으로 표시하지 않는다.

- [ ] **Step 5: report watermark와 evidence table을 구현한다**

  `SCREENING_ONLY`는 첫 페이지·상단에 “초기안 비교용·설계 인용 불가”를 고정한다. `DESIGN_CITABLE`만 검증범위, evidence IDs, reviewer를 포함한 설계 검토 문구를 허용한다.

- [ ] **Step 6: regression을 실행한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests/test_studio_workflow.py tests/test_body_fitted_report.py tests/test_cfd_advice.py tests/test_cfd_result_gate.py
  ```

  Expected: PASS, 기존 body-result URLs와 response fields 보존.

- [ ] **Step 7: commit한다**

  ```powershell
  git add cfd_studio.py cfd_report.py cfd_advice.py tests/test_studio_workflow.py tests/test_body_fitted_report.py tests/test_cfd_advice.py
  git commit -m "feat: show evidence and review gates in studio"
  ```

## Controller rulings and exact HTTP/presentation contract

1. For M1 routes, `case` means only the validated physical folder name at `projects_root/_body_solver/<case>`. Do not scan or map the Task-2 `legacy-...` ID to folders. Responses include both physical `case` and the evidence identity/legacy ID; Task 6 owns future identity routes/indexes.
2. Preserve `GET /api/body-results/<case>` status and every existing key. Add:
   - when evidence exists: current `case_health` and derived `review_summary`;
   - when it does not: `"case_health": null` and `"review_summary": {"status":"NOT_AVAILABLE","reason_codes":["CASE_EVIDENCE_NOT_FOUND"]}`.
   Missing evidence must never hide a valid legacy result or fall back to legacy result-gate health.
3. `GET /api/case-health/<case>` returns `200` with the raw freshly recomputed/validated health object. Invalid/nonexistent case is `404`; an existing case without evidence is `404` JSON `{ "ok": false, "code": "CASE_EVIDENCE_NOT_FOUND", "case": "<safe-name>" }`. Tampered/stale evidence that can be projected returns fail-closed health, never cached PASS. Unexpected service defects are `500`, not fabricated health.
4. `POST /api/case-review` accepts exactly `case`, `reviewer_id`, `decision`, `reason`, `target_sha256`, and optional unique `supersedes_review_ids: string[]`. It returns `201` with immutable review, fresh review summary, and fresh health. Use `400` for malformed/unknown/invalid input, `404` for safe missing case/evidence, `409` with code `REVIEW_TARGET_CHANGED` for either pre-lock or post-lock hash mismatch and no write, `403` for the existing local/Origin guard, and `500` only for infrastructure defects.
5. Keep `_local_post_allowed()` as the first mutation check, keep loopback binding and `Cache-Control: no-store`, and add no remote/CORS/cookie/token behavior. Handler validates HTTP shape and maps errors; Task-3 service owns path/hash/review correctness.
6. Review/currentness decisions are inherited, not remade: rejection => `CITATION_BLOCKED/REVIEW_REJECTED`; missing approval => `NOT_EVALUATED/REVIEW_REQUIRED`; multiple leaves => `CITATION_BLOCKED/REVIEW_HISTORY_AMBIGUOUS`. `supersedes_review_ids` lets one reviewed action resolve all same-target leaves.
7. Report signature is backward-compatible: `generate_body_fitted_report(case_dir, out_html=None, *, projects_root=None)`. Studio always passes the authoritative root. The function recomputes/validates health through Task 3; it does not accept caller-authored health. Without a safe root/evidence it preserves legacy metrics but emits a visible missing-evidence/non-citable banner.
8. For HTML, “first page” means an always-visible top-of-document banner plus explicit `@media print` first-content styling. `SCREENING_ONLY` text is exactly `초기안 비교용 · 설계 인용 불가`. `CITATION_BLOCKED`, `NOT_EVALUATED`, invalid/missing review/evidence are non-green and forbid design-review wording. Only authoritative `DESIGN_CITABLE` may show green/design-review language, validation scope, evidence IDs, reviewer ID, review ID, and target hash.
9. UI/report status, impact, and next actions come only from the Task-1 catalog/health snapshot. Keep existing result-gate/slices/residual/y+/phi/hash as clearly labelled diagnostic evidence under `근거 보기`; never use them as the new citation decision.
10. Advice changes are additive: keep the five legacy fields and priority behavior; add deterministic `group`/`group_label` for `evidence`, `input`, `model`, and `field`. Authoritative health, not legacy `trust.citable`, seals design advice. Missing field evidence remains `NOT_EVALUATED`.
11. Preserve Task-3 field semantics: `complete` still means current health is design-citable; `analysis_complete_not_citable` is terminal for every other state. If the field page is touched, its copy must use the same health snapshot/catalog, not status text alone.
12. Task 1 must include actionable catalog copy for `CASE_EVIDENCE_NOT_FOUND`, `REVIEW_TARGET_CHANGED`, `REVIEW_REQUIRED`, `REVIEW_REJECTED`, and `REVIEW_HISTORY_AMBIGUOUS` so Task 4 does not create a duplicate mapping.
13. Run the expanded focused command from `task-4-ui-gate-precheck.md`, and cover every positive/negative API, local-origin, concurrency, copy/color, watermark/escaping, legacy compatibility, advice grouping, and stale-artifact case in its test matrix.

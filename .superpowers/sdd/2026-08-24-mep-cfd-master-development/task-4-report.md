# Task 4 — Studio Evidence & Review Gate report

## Outcome

Implemented the Task 4 additive Studio/API/report/advice slice without changing Task 1–3 schemas, catalogs, or services and without changing worker, solver, UAT, remote-listener, CORS, cookie, or authentication behavior.

- Body-result responses preserve the legacy payload and add freshly recomputed `case_health` and `review_summary`, including the exact missing-evidence shape.
- The physical `_body_solver/<case>` routes reject traversal and reparse aliases. `GET /api/case-health/<case>` and local-only `POST /api/case-review` map the required 200/201/400/403/404/409/500 cases and retain `Cache-Control: no-store`.
- The body-results primary card is driven by Case Health. Raw evidence, review, legacy gate, numerical quality, and thermal progress are under `근거 보기`. `SCREENING_ONLY` gets the exact top/print watermark `초기안 비교용 · 설계 인용 불가`.
- The body report recomputes Case Health from `projects_root`, preserves metrics if evidence/root is missing, renders the fixed ordered evidence table with escaped dynamic content, and exposes reviewer/review/target binding only for authoritative `DESIGN_CITABLE`.
- Advice keeps the five legacy fields first, adds deterministic `group`/`group_label`, lets authoritative Case Health seal recommendations and digests, and leaves missing required field evidence `NOT_EVALUATED`.

## TDD evidence

The initial feature RED was captured with system Python 3.14 only because the sandboxed virtual-environment launcher could not reach its configured Python 3.12 runtime. It is a non-authoritative witness and is not used as final PASS evidence:

```text
19 failed, 104 passed, 7 warnings in 29.12s
```

Failure groups were body payload/health/review HTTP contracts (8), Studio health UI/report-root plumbing (2), report authority/watermark/escaping states (4), and advice health/group/field behavior (5).

Self-review added and captured two more RED cycles before implementation:

```text
3 failed
- stale pre-lock review created lock infrastructure
- in-root reparse alias was accepted
- digest did not accept authoritative Case Health

1 failed
- Studio lacked the exact SCREENING_ONLY top/print watermark and retained the legacy trust strip
```

Each focused RED was changed to GREEN before the authoritative run. The final authoritative command used the existing elevated `.venv-vv` Python 3.12 environment:

```powershell
& '.venv-vv\Scripts\python.exe' -B -m pytest -q tests/test_studio_workflow.py tests/test_body_fitted_report.py tests/test_cfd_advice.py tests/test_cfd_result_gate.py tests/test_cfd_evidence.py tests/test_cfd_case_health.py tests/test_cfd_review.py
```

```text
255 passed, 7 warnings in 85.14s (0:01:25)
```

The seven warnings are existing `ezdxf`/`pyparsing` deprecation warnings from the DXF import test. Final Python 3.12 `py_compile` for `cfd_studio.py`, `cfd_report.py`, and `cfd_advice.py` also passed, and `git diff --check` reported no whitespace errors.

## Review notes and limits

- The implementation session ran the expanded focused suite. Independent controller verification and the final full repository result are recorded below.
- No manual browser/print-preview session was run. The UI state/color/copy, top/print watermark, evidence disclosure boundary, and report HTML escaping are covered by deterministic tests.
- Test runtime directories matching `.test-studio-*` were cleaned; none are staged or left in the worktree.

## Review fix round 1 — 2026-08-24

Three Important review findings were addressed with a new Python 3.12 RED/GREEN cycle:

- The review endpoint now rejects missing, non-numeric, zero, negative, and over-64-KiB `Content-Length` before reading the request body. Its closed HTTP fields additionally enforce reviewer ID ≤128 characters, reason ≤4096 characters, at most 256 unique supersession IDs, and the exact bounded `review-[0-9a-f]{32}` grammar. These failures return 400 before the review service and leave no `_reviews` directory.
- Advice now accepts a supplied Case Health object as authority only after Draft 2020-12 validation against `case_health.v1.schema.json`, exact current Task-1 decision-table version/content comparison, exact nine-check key comparison, and schema-valid evidence linking. Any supplied incomplete or caller-authored substitute fails closed even if legacy trust says citable.
- The legacy report path passes one freshly recomputed project-root-bound Case Health projection to recommendations and both Markdown/JSON digest writers. Missing/unsafe evidence passes a sealed invalid sentinel, so legacy metrics remain readable but cannot unseal advice. Studio supplies its authoritative root to this report path.

Initial focused RED:

```text
4 failed
- invalid/unbounded Content-Length reached the review service
- overlong/cardinality-invalid fields reached the review service
- shallow caller health could unseal advice
- report advice/digests did not accept current Case Health
```

The report/root integration was separately observed RED as `2 failed` before its minimal implementation. Targeted GREEN checkpoints were HTTP `2 passed`, authoritative advice/grouping `9 passed`, report/root threading `2 passed`, and the three Task-4 suites `131 passed, 7 warnings`.

Final authoritative command remained the exact expanded seven-file command above:

```text
260 passed, 7 warnings in 104.26s (0:01:44)
```

Python 3.12 `py_compile` and `git diff --check` passed after the final run. No manual browser/print-preview claim is made. Task 4 now enforces bounds at the public HTTP boundary only; internal Task-3 service/schema cardinality bounds remain Task 3's own contract and were intentionally not changed in this round.

## Independent closure verification

- Final scoped reviewer verdict: `CLEAN`; no Critical or Important findings remained after commit `24d18aa`.
- Controller focused verification: `260 passed, 7 warnings in 130.62s`.
- Controller full authenticated suite: `808 passed, 14 skipped, 7 warnings in 582.17s`; exit code `0`.
- Full-suite JUnit artifact: `cfd_projects/_controller_verify/task4-final-full.xml`.
- The seven warnings are the known dependency deprecations already described above. The fourteen skips are runtime-gated tests and are not presented as executed solver/CAD validation.

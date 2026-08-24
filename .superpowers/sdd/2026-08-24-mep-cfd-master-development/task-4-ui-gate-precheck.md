# Task 4 UI / report Evidence & Review Gate pre-implementation check

**Scope:** read-only integration check of Task 4 against the master plan, Tasks 1--3 briefs and controller rulings, and the current Studio, body report, advice module, and focused tests. No source, test, git, or runtime artifact was modified and no tests were run.

## Outcome

Task 4 is an additive presentation/API slice.  It must replace neither the existing body-result route nor the serialized worker/solver/UAT locks.  The current code has only the older `cfd_result_gate` trust contract, so it is not an acceptable source for the new Case Health or citation claim:

- `body_result_payload()` currently returns `result_gate`, cached/result-manifest data, and `design_job` (`cfd_studio.py:108-130`).  It has no Case Evidence/Health/Review input.
- `/api/body-results/<case>` is already a 200/404 API and the body-results page expects `result_gate` (`cfd_studio.py:3413-3444`, `3843-3898`).  Both payload and route must survive unchanged.
- `generate_body_fitted_report()` currently calls legacy `evaluate_body_fitted_case()` and its Korean label map has no `CITATION_BLOCKED` (`cfd_report.py:171-201`, `310-327`).  The report cannot continue to use it as citation authority after Tasks 2--3 land.
- Task 2 explicitly forbids summary-cache/report/generated outputs as evidence sources.  The health GET and the added fields must read/recompute from the authoritative evidence chain on every request, not from `case_summary()` or a body-result cache.

Tasks 1--3 are still producers in this checkout rather than present source files.  Therefore Task 4 should import their public services/catalog; it must not add permissive local substitutes, duplicate the citation table, or promote `run_manifest.design_ready`/legacy `result_gate` values.

## Required authority and compatibility boundary

```text
safe Studio case-name -> Task 2 case_evidence.v1 bytes -> Task 3 health/review
                   |                                       |
existing summary/slices/result_gate ------------------------+-> additive API/UI/report
                                                               (catalog Korean copy only)
```

`case_evidence.v1` bytes and its current artifact validation are the input to Case Health. A human `APPROVED` review is hash-bound to those bytes; it may satisfy the approved-review condition for the citation table but can never change a source check or derived `design_ready`. The Task-1 catalog's exact `CITATION_DECISION_TABLE_VERSION` and ordered table remain the sole citation decision authority.

Preserve both existing case namespaces. `safe_case_dir()` is for legacy `ROOT/<case>` cases; `_body_solver_case()` validates a physical `_body_solver/<folder>` case and rejects traversal/reparse escapes (`cfd_studio.py:71-91`). Task 4 body routes must use the latter. Do not scan by display title, use a path from the HTTP body, or select a first glob result.

## HTTP contract to implement

The following is the compatible implementation contract. It keeps existing endpoints/fields while making all new domain decisions in `cfd_case_health`/`cfd_review`, not in `StudioHandler`.

| Route | Successful response | Required failures / status | Notes |
|---|---|---|---|
| `GET /api/body-results/<physical-case-name>` (existing) | Existing `200` JSON, byte-for-byte-compatible existing keys (`ok`, `case`, `manifest`, `run_manifest`, `result_gate`, `design_job`, `summary`, `slices`) plus additive `case_health` and `review_summary`. | Preserve present `404` for invalid/missing body result or manifest/slice read failure. Never turn a valid legacy body-result request into 404 merely because Case Evidence is unavailable. | `case_health` must be the current Task-3 projection, not an alias of `result_gate`; `review_summary` is a small derived/currentness projection, never unvalidated review JSON. See missing-evidence ruling below for exact nullable/error shape. |
| `GET /api/case-health/<physical-case-name>` | `200` with the raw current `case_health.v1` object; this follows Task-4's required `response.json()["checks"]` shape. It is recomputed/revalidated from the selected `case_evidence.v1`, so stale/tampered evidence is a `200` health response with its fail-closed `BLOCKED`/`CITATION_BLOCKED` state, not a cached PASS. | `404` for invalid/nonexistent physical case. `404` (recommended) with stable `CASE_EVIDENCE_NOT_FOUND` error for an existing legacy result that has no selected evidence; malformed endpoint syntax cannot reach the filesystem. Service/program defects remain `500`, never a fabricated health object. | Resolve only `ROOT/_body_solver/<case>/case_evidence.v1.json` for M1. It must be an independently re-resolved, root-contained file. The future Task-6 identity lookup is not silently inferred here. |
| `POST /api/case-review` | `201` JSON: `{ "ok": true, "review": <immutable case_review.v1 record>, "review_summary": <derived current summary>, "case_health": <fresh projection> }`. Recomputing after publish avoids returning an approval with stale citation status. | `400` malformed JSON, non-object body, unknown keys/types, missing/blank reviewer/reason, invalid `decision`, malformed hash, unsafe case, or forbidden review output/supersession input; `404` safe case/evidence absent; `409` `{ "ok": false, "code": "REVIEW_TARGET_CHANGED", ... }` if supplied `target_sha256` does not match freshly read current evidence bytes, including a change detected after lock acquisition; `403` local/CSRF guard failure; `500` unexpected infrastructure failure only. | Input is exactly `{case, reviewer_id, decision, reason, target_sha256}` plus an optional controller-approved `supersedes_review_id` only if Task 3 exposes it. `case` is not a filesystem path. Use the Task-3 service to validate/read/hash/publish; handler does path validation and HTTP conversion only. |

`target_sha256` is the Task-1-G expected hash precondition. Compare it with the selected current evidence bytes before review creation, then have `create_review()` re-read/hash immediately before immutable publication. A 409 must not write a review. The endpoint must return the Task-3 record's computed authoritative hash, not echo the request as proof.

### Case identifier decision required before Task 6

The Task-4 prose calls the path parameter and POST field `case`, while the current body UI only has an `_body_solver` *folder name*. Task 2 creates an M1 `legacy-<20 hex>` `case_id` from a geometry/run hash tuple, which need not equal that folder name; Task 6 later introduces `case_identity.v1`. Do **not** make a speculative ID-to-folder scan.

Recommended M1 rule: for the two new Task-4 routes, `case` means exactly the validated physical body-solver folder name, and the server selects only its default `case_evidence.v1.json`. The response includes both physical `case` and evidence identity/legacy ID for display. Task 6 can add a separately specified ID route/index without changing these legacy routes. Controller confirmation is required because this semantics is not written in the Task-4 interface.

### Missing-evidence payload decision required

The body-result route must continue to display an existing result even when Task-2 evidence is absent, while a health object cannot honestly be constructed from no evidence. Recommended additive fields in that one case are `"case_health": null` and `"review_summary": {"status": "NOT_AVAILABLE", "reason_codes": ["CASE_EVIDENCE_NOT_FOUND"]}`; the UI renders a non-green blocker with catalog copy. The dedicated health GET returns the 404 above. Do not substitute legacy `result_gate` data. This exact nullable shape needs a controller/schema ruling.

## Local-only and request safety

Retain `_local_post_allowed()` unmodified as the first POST check. It requires `Host` to be `127.0.0.1` or `localhost` (optional port), rejects `Sec-Fetch-Site: cross-site` and `same-site`, requires an `Origin` to exactly match `http://<Host>`, and permits origin-less local CLI/smoke requests (`cfd_studio.py:3352-3367`). It already protects all mutations before JSON parsing (`3487-3500`) and must protect `/api/case-review` identically.

The server binding to `127.0.0.1` is necessary but does not replace this local-service CSRF defense. Keep `Cache-Control: no-store` for health/review responses (`3374-3385`). Do not add remote listener, cookies, bearer tokens, or a new CORS allowance in Task 4. The existing handler turns malformed JSON into a generic exception/500; route-level JSON validation (or a carefully compatible shared 400 conversion) is needed for the new POST so attacker-controlled malformed bodies receive the specified 400 and publish nothing.

## UI and Korean-copy contract

The body-results page currently renders only a legacy `result_gate` strip, including a green `DESIGN_CITABLE` branch (`cfd_studio.py:3891`). Replace/augment it with a Case Health card driven by added `case_health`/`review_summary` data:

1. Show the catalog-derived user status/citation status, usable scope, blocker(s), and next action at the card's top. Put residual, y+, phi, raw hash, artifact paths, evidence IDs, and review record details under an expandable **근거 보기** section.
2. Obtain Korean `impact` and `next_actions` exclusively from Task 1's `status_descriptor`/health snapshot. Do not add a second UI hard-coded mapping for a reason code. The only presentation-only state may be the explicitly ruled missing-evidence state above.
3. `DESIGN_CITABLE` may use green only when the authoritative health table says it and the summary says the current review is approved. `SCREENING_ONLY` and `NOT_EVALUATED` must be amber/neutral; `CITATION_BLOCKED`, invalidated, malformed, and missing evidence must be red/neutral. Never show either screening or citation-blocked as a successful green result.
4. Preserve the existing slice viewer, `result_gate` rendering (as legacy diagnostic information), 3.0 FTT control, result URLs, and single worker queue. Do not change their semantics to report Case Health as solver completion.

The existing field-run page currently calls `complete` “설계 검토 인용 가능” based on its older field/legacy state (`cfd_studio.py:3744-3747`). Task 3's compatibility rule says `complete` is raw-analysis completion and only additive health data can support a citation claim. Task 4 must not copy that legacy copy into the new health card; if this page is touched, it must consume the same health snapshot and catalog copy.

## Report and watermark contract

`generate_body_fitted_report()` needs a new authoritative health/review input path. It presently accepts only `case_dir` and cannot safely derive a `projects_root` for temporary callers/tests. Recommended backward-compatible signature is `generate_body_fitted_report(case_dir, out_html=None, *, projects_root=None)`, with Studio passing `ROOT`; callers without a safe root get an explicit non-citable/missing-evidence presentation rather than a guessed root. The controller must decide whether this function recomputes health from evidence or consumes a freshly validated Task-3 projection, but it must never read old report HTML, summary cache, or legacy result-gate citation status as source authority.

Required rendered rules:

- For `SCREENING_ONLY`, write the exact fixed Korean watermark **“초기안 비교용 · 설계 인용 불가”** at the document top and in print-first-page styling. It is not optional explanatory prose hidden below metrics.
- For `CITATION_BLOCKED`, `NOT_EVALUATED`, missing evidence, or invalid review, render a visible non-green citation hold/block at the same top position; no design-review wording is allowed.
- Only for `DESIGN_CITABLE`, permit the design-review statement and render the validation/applicability scope, the current evidence IDs/links, and reviewer ID plus current review ID/hash binding. A reviewer alone is never enough.
- Add a compact evidence table containing the fixed health checks in catalog order, status, Korean impact/next action, reason codes, and evidence references. Escape all artifact/reviewer/reason text exactly as current report uses `html.escape`.
- Keep the current thermal/heat contract, numerical-quality table, metrics, and generated report route. Legacy `result_gate` may be shown only as diagnostic provenance, clearly labelled not the Case Evidence citation decision.

The current report has only three citation labels (`DESIGN_CITABLE`, `SCREENING_ONLY`, `NOT_EVALUATED`) and colors all others as failure (`cfd_report.py:310-364`); Task 4 must explicitly add the Task-1 `CITATION_BLOCKED` state and prevent it from falling through to ambiguous text. The master-plan wording “first page” is ambiguous for an HTML document; use a top-of-document banner plus `@media print` first-page/top treatment unless the controller specifies PDF pagination behavior.

## Advice grouping without breaking current callers

`cfd_advice.recommendations()` currently returns sorted rows `{priority, category, finding, action, basis}` and uses one legacy `trust.citable` boolean to prepend a convergence block (`cfd_advice.py`, `recommendations`). Existing tests select by `category` and assert ordering/wording. Preserve all five existing keys and their priority ordering; make grouping additive, for example `group`/`group_label`:

| Group | Source of actions | Rule |
|---|---|---|
| `input` | Missing/uncertain design inputs, airflow and heat-source input findings | May retain deterministic input calculations; do not claim a CFD result is citable. |
| `model` | Geometry, diffuser snap/resolution, heat-model limitations | Existing engineering/model limitation advice belongs here. |
| `evidence` | Case Health blockers, invalid/stale evidence, review needed, catalog next actions | Build from health reason codes and catalog copy; this group is first whenever it blocks use. No hand-authored competing Korean remedy. |
| `field` | Field-calibration/measurement/TAB prerequisites | Only show when the profile requires field evidence; absence remains `NOT_EVALUATED`, not PASS. |

Use the authoritative citation state, not `result_gate.citable`, to decide whether to seal result-derived design recommendations. The report/UI can render group headings from the additive field. Keep legacy calls with `trust=None` working; a transition adapter may accept the new health object but must fail closed if it lacks a valid citation status. Do not change existing Korean categories, `priority`, or digest payload shape other than additive fields.

## Required tests and regression boundary

Add focused tests before implementation, then run the Task-4 command plus producer/service regressions:

```powershell
& $Python -B -m pytest -q tests/test_studio_workflow.py tests/test_body_fitted_report.py tests/test_cfd_advice.py tests/test_cfd_result_gate.py tests/test_cfd_evidence.py tests/test_cfd_case_health.py tests/test_cfd_review.py
```

The Task-4 minimum four-suite command remains required; the last three suites ensure its source-of-truth producers were not bypassed.

| Area | Positive proof | Negative/regression proof |
|---|---|---|
| Body results | Existing `/api/body-results/<case>` keeps all legacy keys/200 and adds health/review; old clients/slices still work. | Case traversal/encoded escape still 404; absent evidence does not turn a valid legacy body result into 404 and does not synthesize health from `result_gate`. |
| Health GET | Mutate authoritative run/mesh/result after evidence publication; `GET /api/case-health/<case>` reflects recomputed `BLOCKED` health and no summary cache is read. | Invalid/missing case/evidence, symlink/reparse escape, malformed evidence, and citation-table/catalog mismatch fail closed; never use cache/report or `run.design_ready`. |
| Review POST | Same-origin loopback request creates one immutable hash-bound record and refreshes health/review summary. | Wrong/stale `target_sha256` returns exactly 409/`REVIEW_TARGET_CHANGED` and leaves no file; malformed/missing fields 400; invalid decision 400; cross-site, `same-site`, mismatched origin, and non-loopback host 403 before service invocation. |
| Review concurrency | Two creation attempts cannot overwrite a review; unique immutable records or a deliberate service conflict are surfaced. | Target changes between initial compare and locked publish returns 409; competing unsuperseded current reviews never select first/newest by filesystem order. |
| UI copy | All four citation states display catalog Korean scope/blocker/next action; only design-citable is green. | `SCREENING_ONLY` and `CITATION_BLOCKED` are never green and raw residual/hash remain outside the primary card. |
| Report | Screening output has the exact watermark; design-citable output has scope, evidence IDs, reviewer/review binding, and check table. | Legacy `cfd_result_gate` claiming `DESIGN_CITABLE` cannot create a design-review banner; stale/blocked/missing health cannot include design-citable wording; HTML escaping protects reviewer/reason refs. |
| Advice | Existing categories/priority tests continue; new health blockers group as `evidence`, and model/input/field rows group deterministically. | A legacy `citable=True` alone cannot unseal design action when health is blocked/not evaluated; field advice does not convert missing field evidence into PASS. |

## Controller decisions still needed before implementation

1. Confirm the M1 `case` field/path semantics above (physical body-solver folder only) and defer case-ID/Design identity resolution to Task 6; otherwise specify a safe one-to-one index now.
2. Confirm the `GET /api/case-health` missing-evidence status/body and the additive nullable `case_health`/`review_summary` body-results shape. This is necessary to preserve legacy result visibility without inventing a health artifact.
3. Add/confirm `expected_target_sha256` on Task-3 `create_review()` and choose the canonical review directory/discovery mechanism. `build_case_health(evidence_path, projects_root)` otherwise lacks a safe way to locate the current review.
4. Choose the exact outcome for no approval and a valid `REJECTED` review (`NOT_EVALUATED` is the Task-3 precheck recommendation) and the status for multiple valid unsuperseded reviews (recommended fail-closed `CITATION_BLOCKED`/`REVIEW_HISTORY_AMBIGUOUS`). The UI/report must not make its own choice.
5. Confirm whether the POST must expose `supersedes_review_id`; the required append-only lifecycle cannot be fully driven by the Task-4 four-field payload without an additional reviewed action/route.
6. Decide how `generate_body_fitted_report()` receives a safe `projects_root`/fresh health object, and what “first page” means for its HTML output/print layout. Do not infer a root from arbitrary `case_dir` tests or CLI paths.
7. Confirm Task-3's raw-evidence-only/all-eight `design_ready` aggregation and priority precedence. Task 4 displays it but must not derive it from purpose, reviewer, FTT, or legacy run fields.

# SDD ledger — plan: docs/superpowers/plans/2026-08-24-mep-cfd-master-development.md

## Setup

- Original checkout preserved at `C:\Users\User\Documents\MEP CFD Studio` on `codex/cfd-studio-improvements`.
- Isolated worktree: `C:\Users\User\Documents\MEP CFD Studio\.worktrees\case-evidence-review-gate`.
- Execution branch: `codex/case-evidence-review-gate`, start commit `bd7d1aaba7a0c083b4931b3f72cdf297e3342780`.
- Selected baseline snapshot: 117 code, schema, benchmark, test, and planning files copied from the approved current checkout; SHA-256 mismatches: 0.
- Excluded from baseline: `.codex-temp`, `.claude`, `.superpowers`, PPTX/render artifacts, `cfd_projects`, generated reports, and the bundled Python installer.

## Rulings

- Ruling: Create the isolated worktree before Task 0 tests — the worktree skill and the plan's dirty-tree constraint require isolation before any test artifacts or commits — if wrong, Task 0 ordering differs from the prose but the source checkout remains safer and unchanged.
- Ruling: Interpret the user's `계획서대로 개발시작` as approval to freeze the current code-only state in the isolated branch, not as permission to stage artifacts or mutate the original checkout — if wrong, the branch may include pre-existing code changes the user wanted separated, but they remain reviewable and the original is untouched.
- Ruling F1: Run companion P0.0 first and bind every Task 0 validation command to `.venv-vv\Scripts\python.exe`; the prior `.venv` command is superseded — otherwise M0 could be a false green from an unpinned interpreter.
- Ruling F2: The canonical baseline is `cfd_projects/_release_evidence/vv/{candidate_id}/vv_baseline.json` with its sibling JUnit file; all later consumers must carry `baseline_evidence_path` plus the artifact hash, and the conflicting flat `vv_baseline.v1.json` path is superseded — otherwise Task 13 could freeze the wrong baseline.
- Ruling F3: Task 1 must define exactly one of `case_identity` or `legacy_case_ref`; the M1-only legacy branch requires immutable geometry and run hashes and can never produce `DESIGN_CITABLE` — otherwise the first actual-DXF slice cannot be represented before Task 6 or could become falsely citable.
- Ruling F4: Task 1 adds `CITATION_BLOCKED` to the citation catalog and a versioned `citation_decision_table` in `case_health.v1`; Task 3 must implement exhaustive table-driven precedence tests: invalid/stale evidence or review => `CITATION_BLOCKED`; any required `FAIL`/`BLOCKED` => `CITATION_BLOCKED`; incomplete required evaluation => `NOT_EVALUATED`; screening purpose or legacy identity => `SCREENING_ONLY`; only a current approved review and every purpose-required check `PASS` => `DESIGN_CITABLE`. Optional modules cannot raise or lower the core status unless the declared purpose requires them — otherwise UI, API, and report could disagree about trust.
- Ruling F5: Task 9 must create `scripts/studio_browser_smoke.py` and emit `cfd_projects/_release_evidence/ui/{candidate_id}/design_scenario_compare_smoke.json`; the repeatable command is `& $Python -B scripts/studio_browser_smoke.py --base-url http://127.0.0.1:8765 --out <artifact>` and M2 cannot close without a verified artifact — otherwise a broken GUI route could escape API tests.
- Ruling F6: P6.4/P7.3 numerical bands remain informational-only until an approval manifest records source, measurement uncertainty, reviewer, version, and hash; Task 13 may inventory them but must forbid PASS/L3 derivation from them — otherwise an unapproved threshold could create a false validation claim.
- Ruling F7: Task 14 implementation may proceed fail-closed, but M4 exposure requires an approval intake artifact containing the ISO/ASHRAE source edition, reference-vector fixture hash, reviewer ID, version, and approval hash — otherwise comfort PASS would be untraceable.
- Ruling F8: Task 15 must add an IAQ benchmark family with source/uncertainty manifest, age-of-air or step-response fixture, runner command, and evidence test; IAQ remains `NOT_EVALUATED` until that benchmark passes — otherwise conservation tests alone could be mislabeled as validation.
- Ruling F9: Task 17 source tests are pre-RC evidence only; final acceptance must execute the installed release-audit entry point against app-owned project paths with both `--contract v1` and `--contract v2`, as required by companion P10.3 — otherwise checkout-source PASS could be mistaken for installer acceptance.
- Ruling F10: `mpi_runtime_smoke.v1` must include `physics_profiles`, compared QoIs, approved tolerance-manifest hash, and unsupported-profile serial fallback; IAQ and CHT/radiation selection must be tested and remain serial unless explicitly covered — otherwise optional parallel execution could bypass physics-specific equivalence evidence.
- Ruling F11: Tasks 18 and 19 are post-M7 optional/research work; any build that incorporates either task must mint a new RC and repeat P8.4 through P10 before L4 release — otherwise post-audit code could ship under stale evidence.
- Ruling Task1-A: New trust schemas use Draft 2020-12, closed objects, POSIX projects-root-relative paths, and strict lowercase SHA-256; runtime containment remains mandatory in Task 2 — otherwise schema-valid paths could escape or resolve under different roots.
- Ruling Task1-B: Purpose profiles are closed to `screening`, `design_review_candidate`, `benchmark`, and `field_validation`; screening requires the five core checks and is capped at `SCREENING_ONLY`, design/field require all eight checks plus current approval, and benchmark requires seven non-field checks and yields design-citation `NOT_EVALUATED` — otherwise "purpose-required" cannot be evaluated deterministically.
- Ruling Task1-C: Evidence uses an exact ordered eight-check array, while health uses a closed keyed nine-check object including derived `design_ready` — otherwise duplicate/order validation would conflict with Task 3's keyed health API.
- Ruling Task1-D: Artifact refs require geometry/surface/mesh/run/result and allow only thermal input/progress, numerical sensitivity, GCI, benchmark, and field evidence as profile-gated keys; Task 2, not schema presence, establishes completeness — otherwise closed v1 contracts would be either unusably narrow or falsely permissive.
- Ruling Task1-E: Current identity is a `{contract: case_identity.v1, path, sha256}` JSON-artifact link; legacy identity is screening-only — otherwise the Task 6 bridge would be structurally ambiguous.
- Ruling Task1-F: `citation_decision_table.v1` is an exact schema `const` mirrored and tested against the catalog export, and Task 3 must reject mismatches; benchmark/not-applicable precedes screening in the ordered table — otherwise consumers could silently invent precedence.
- Ruling Task1-G: Reviews target only `case_evidence.v1`; APPROVED/REJECTED are persisted, INVALIDATED/SUPERSEDED are derived, client expected hash is compared before Task 3 recomputes/stores the authoritative hash — otherwise append-only review history and the 409 stale-target behavior could diverge.
- Ruling Task1-H: Root errors are closed code/detail/evidence-ref objects; stable reason codes are authoritative and Korean impact/actions come from the catalog, with health snapshots carrying deterministic display copy — otherwise API/report copy and machine-readable reasons would drift.
- Ruling Task2-A: Numerical-sensitivity preparation/frozen-pair artifacts have no final authority in Task 2; numerical provenance may establish `numerics_verified`, but sensitivity remains absent until a versioned owner supplies an executable validator — otherwise preparation metadata could masquerade as V&V evidence.
- Ruling Task2-B: Standalone `thermal_progress.json` is canonical and must deep-equal the run manifest's embedded progress — otherwise two divergent histories could both look current.
- Ruling Task2-C: Geometry may be any safe non-generated projects-root-contained `geometry.v2` JSON independently bound to the surface path/hash — otherwise real imports would be overconstrained or downstream path strings could self-authorize.
- Ruling Task2-D: Legacy `case_id` is `legacy-` plus 20 hex characters from the canonical geometry/run path+hash tuple — otherwise relocation or similarly named solver folders could create unstable/colliding bridge identities.
- Ruling Task2-E: GCI authority requires exactly one current provenance match; none is `NOT_EVALUATED`, multiple is `BLOCKED/AMBIGUOUS_GCI_EVIDENCE` — otherwise filesystem enumeration order could choose the trust result.
- Ruling Task2-F: Absent profile-gated GCI/benchmark/field evidence is `NOT_EVALUATED`; supplied unsafe/ambiguous evidence and missing/unsafe core artifacts are `BLOCKED` — otherwise absence and tampering would become indistinguishable.
- Ruling Task2-G: Default output is atomically replaced at `case_dir/case_evidence.v1.json`, invalidating hash-bound prior reviews; explicit safe output paths support history, and every evidence/report/cache output is excluded from source discovery — otherwise the evidence could validate itself or stale approvals could survive replacement.
- Ruling Task3-A: `create_review` requires `projects_root` and `expected_target_sha256`, rechecks after acquiring the review-directory lock, and fails `REVIEW_TARGET_CHANGED` on mismatch — otherwise Task 4's optimistic concurrency promise has a TOCTOU gap.
- Ruling Task3-B: Reviews are immutable direct children of `evidence.parent/_reviews`; health validates all matching files non-recursively and never selects by timestamp/filesystem order — otherwise review discovery could escape or become nondeterministic.
- Ruling Task3-C: A current rejection for a review-required purpose is `CITATION_BLOCKED/REVIEW_REJECTED`, while a missing approval is `NOT_EVALUATED/REVIEW_REQUIRED` — otherwise explicit human rejection and unfinished review would be conflated.
- Ruling Task3-D: `design_ready` aggregates all eight raw evidence checks independently of purpose/review using `FAIL > BLOCKED > NOT_EVALUATED > PASS` — otherwise approval or a less demanding purpose could rewrite scientific readiness.
- Ruling Task3-E: Review records use `supersedes_review_ids`; multiple valid leaves are `REVIEW_HISTORY_AMBIGUOUS` until one new immutable record supersedes all leaves for the identical target hash — otherwise concurrent reviewers could be resolved by arbitrary file order.
- Ruling Task3-F: Preserve current fail-closed field status semantics: `complete` only for recomputed `DESIGN_CITABLE`; all other citation states use terminal `analysis_complete_not_citable`, and terminal refresh never reruns the solver — otherwise older consumers could read raw completion as a design claim.
- Ruling Task4-A: M1 API `case` is the validated physical `_body_solver` folder name; legacy/content IDs are response metadata until Task 6 owns an index — otherwise speculative scans could bind the wrong run.
- Ruling Task4-B: Existing body-results stay visible with nullable health and `CASE_EVIDENCE_NOT_FOUND`; dedicated health GET returns a stable 404 for absent evidence and fail-closed 200 health for projectable tampering — otherwise compatibility would be lost or legacy result-gate data would be forged into health.
- Ruling Task4-C: Review POST exposes optional `supersedes_review_ids`, preserves loopback/Origin protection, and maps malformed/missing/stale/infrastructure cases to 400/404/409/500 exactly — otherwise concurrency and clients would make incompatible assumptions.
- Ruling Task4-D: Report recomputes health using explicit `projects_root`; callers without safe authority retain metrics but receive a top/print missing-evidence banner, never a guessed citation status — otherwise arbitrary CLI/test paths could be treated as trusted roots.
- Ruling Task4-E: HTML first-page watermark means a top-of-document banner plus print styling; only authoritative `DESIGN_CITABLE` is green and design-review wording is forbidden for every other state — otherwise presentation could overclaim despite correct JSON.
- Ruling Task4-F: Advice keeps legacy fields/ordering and adds catalog-driven evidence/input/model/field groups; legacy `trust.citable` cannot unseal design actions — otherwise old callers break or stale trust bypasses health.
- Ruling Task4-G: Task 1 owns actionable copy for the five missing/review lifecycle codes, and Task 4 must consume it — otherwise UI/report would create a second status authority.

## Mandatory pre-flight consistency scan

Full read-only report: `preflight-scan.md`. The following ledger tables preserve one row for every task and every identified shared file/interface/schema boundary.

### Per-task self-consistency

| Task | Ownership/interface/test/gate result | Finding |
|---|---|---|
| 0 | Runtime outputs and bootstrap ordering conflict with companion P0.0/P0.1; M0 remains fail-closed. | F1, F2 |
| 1 | Six new contract/catalog files are isolated, but mandatory identity arrives later. | F3 |
| 2 | Evidence producer and recomputation/forgery tests align with P0. | Clean |
| 3 | Health/review interfaces are coherent, but trust-state derivation is incomplete. | F4 |
| 4 | Studio/report/advice consumers and compatibility regressions are coherent. | Clean |
| 5 | Working-PC evaluation is serial-only and evidence-safe. | Clean |
| 6 | Immutable Design/Scenario/Run contracts and tests align. | Clean |
| 7 | Versioned sidecar migration preserves v1 and fail-closed resume. | Clean |
| 8 | Templates use stable geometry IDs and forbid inferred physical values. | Clean |
| 9 | Compare/UI/report flow is coherent; browser smoke lacks command/artifact. | F5 |
| 10 | Validation-anchor authority cleanly separates candidate and final citation gate. | Clean |
| 11 | Generic benchmark registry and provenance validation are coherent. | Clean |
| 12 | Measurement/calibration split and hash-preserved imports are coherent. | Clean |
| 13 | Frozen inventory is coherent, but companion thresholds conflict with approval authority. | F6 |
| 14 | Comfort is fail-closed; approved reference vector is an unscheduled input. | F7 |
| 15 | IAQ conservation path exists, but M5 lacks an executable benchmark evidence path. | F8 |
| 16 | Radiation/CHT keeps a separate surface contract and withholds production flag. | Clean |
| 17 | Support/readiness work is coherent; source command cannot prove installed RC. | F9 |
| 18 | MPI remains opt-in, but physics scope/tolerance manifest is underspecified. | F10 |
| 19 | Surrogate is fail-closed, but any inclusion invalidates the prior frozen RC. | F11 |

### Shared-boundary scan

| Earlier -> later | Shared file/interface/schema boundary | Result |
|---|---|---|
| T0 -> T5 | `working_validation.py`, validation test | Clean |
| T0 -> T13 | `vv_baseline.py`, baseline test and evidence path | F2 |
| T1 -> T2 | status catalog and `case_evidence.v1` | F3 |
| T1 -> T3 | `case_health.v1`, `case_review.v1`, catalog | F4 |
| T1 -> T4 | status/health/review presentation semantics | Clean |
| T1 -> T17 | release-wide status vocabulary | Clean |
| T2 -> T3 | `cfd_evidence.py`, result gate, evidence hashes | Clean |
| T2 -> T4 | result-gate regression and evidence presentation | Clean |
| T2 -> T7 | field-pipeline regression baseline | Clean |
| T2 -> T10 | `cfd_result_gate.py`, result/field tests | Clean |
| T2 -> T11 | result-gate benchmark applicability | Clean |
| T2 -> T16 | result-gate radiation/CHT integration | Clean |
| T3 -> T4 | health/review records and endpoints | F4 |
| T3 -> T7 | field manifest terminal/identity fields | Clean |
| T3 -> T10 | field health and validation anchor | Clean |
| T3 -> T11 | result-gate regression expectations | Clean |
| T3 -> T12 | `cfd_case_health.py`, field calibration | F4 |
| T3 -> T14 | case-health comfort visibility | Clean |
| T3 -> T15 | case-health IAQ visibility | Clean |
| T3 -> T16 | result-gate physics regression | Clean |
| T3 -> T17 | health/review release semantics | Clean |
| T4 -> T7 | Studio routes and legacy scan | Clean |
| T4 -> T9 | Studio/report/advice and compare UI | Clean |
| T4 -> T10 | result-gate UI regression | Clean |
| T4 -> T11 | report/citation benchmark presentation | Clean |
| T4 -> T12 | Studio/report measurement mapping | Clean |
| T4 -> T14 | comfort UI/report/advice | Clean |
| T4 -> T15 | IAQ UI/report | Clean |
| T4 -> T16 | CHT/radiation reporting | Clean |
| T4 -> T17 | readiness/support UI | Clean |
| T4 -> T18 | MPI presentation and serial default | Clean |
| T4 -> T19 | surrogate UI/report watermark | Clean |
| T5 -> T13 | working-PC verification and frozen inventory | Clean |
| T5 -> T17 | runtime capabilities/readiness | Clean |
| T5 -> T18 | serial baseline and MPI gate | Clean |
| T6 -> T7 | project model and legacy link | Clean |
| T6 -> T8 | Design/Scenario template contract | Clean |
| T6 -> T9 | immutable IDs and CRUD/compare UI | Clean |
| T6 -> T19 | run identity and training inventory | Clean |
| T7 -> T9 | Studio legacy/run identity | Clean |
| T7 -> T10 | field identity and anchor | Clean |
| T7 -> T13 | field identity/provenance evidence | Clean |
| T7 -> T19 | linked legacy identity inventory | Clean |
| T8 -> T9 | scenario semantic diff/template | Clean |
| T8 -> T19 | scenario features and OOD logic | Clean |
| T9 -> T19 | compare/report and recommendation UI | Clean |
| T10 -> T11 | anchor-aware citable gate | Clean |
| T10 -> T12 | field-authority anchor | Clean |
| T10 -> T13 | `validation_anchor.v1` and inventory | Clean |
| T10 -> T16 | final citation gate and CHT | Clean |
| T11 -> T12 | benchmark/report and field comparison vocabulary | Clean |
| T11 -> T13 | benchmark manifests and frozen evidence | F6 |
| T11 -> T15 | benchmark registry and IAQ family | F8 |
| T11 -> T16 | benchmark gate and radiation/CHT | Clean |
| T12 -> T13 | measurement/calibration and field validation | F6 |
| T12 -> T14 | postprocess/health and comfort | Clean |
| T12 -> T15 | postprocess/health and IAQ | F8 |
| T12 -> T16 | postprocess and thermal balance | Clean |
| T12 -> T17 | measurement UI and installed flow | F9 |
| T13 -> T17 | frozen evidence and release audit | F9 |
| T14 -> T15 | shared post/report/Studio health pattern | Clean |
| T14 -> T16 | MRT labels and radiation source | Clean |
| T14 -> T17 | comfort gate and core release | Clean |
| T14 -> T19 | comfort and surrogate presentation | Clean |
| T15 -> T16 | physics/post/report manifests | Clean |
| T15 -> T17 | IAQ status and core release | Clean |
| T15 -> T18 | IAQ physics and MPI scope | F10 |
| T15 -> T19 | IAQ and surrogate presentation | Clean |
| T16 -> T18 | CHT/radiation physics and MPI scope | F10 |
| T16 -> T19 | CHT/radiation and surrogate report | Clean |
| T17 -> T18 | frozen RC and MPI changes | F11 |
| T17 -> T19 | frozen RC and surrogate changes | F11 |
| T18 -> T19 | optional feature-state presentation | Clean |

## Task status

- Task 0: original code/baseline slice complete — commits `eef978e`, `8bb9c5e`; review round 2 clean; controller focused verification `38 passed`; working-copy plan revision adds Steps 6–8, which remain pending.
- Task 1: complete — commits `7669d35`, `c0c3c59`; review fix round 1 clean; controller focused verification `45 passed`; controller full verification `675 passed, 14 skipped`.
- Task 2: complete — commits `77c0cc8`, `b533118`, `f24358c`, `2e9282e`, `458b66f`; final reviewer verdict `CLEAN`; controller focused verification `96 passed`; controller full verification `733 passed, 14 skipped`.
- Task 3: complete — commits `367ae7b`, `bcd9f11`, `18dfc2f`, `64ead34`; final reviewer verdict `CLEAN`; controller focused verification `148 passed`; controller full verification `785 passed, 14 skipped`.
- Task 4: complete — commits `fd0bcee`, `24d18aa`; final reviewer verdict `CLEAN`; controller focused verification `260 passed`; controller full verification `808 passed, 14 skipped`.
- Task 0 Step 6: authorized, push pending — `origin` is public, the feature branch is absent remotely, and the mandatory safety scan found likely real-derived architectural geometry already present in remote `feature/cfd`; on 2026-08-25 the user explicitly accepted that known pre-existing risk and authorized a public branch push. This is not recorded as a clean scan.
- Task 0 Steps 7–8: pending — RACI owner availability and first-green Windows CI have not been recorded.
- Task 4.5: pending — confirmed real-DXF geometry and an accountable MEP reviewer are not available.
- Task 5a–5c: pending — revised plan forbids code work before Task 0 Steps 6–8 and keeps runtime/GUI evidence behind geometry and environment gates.
- Task 1 pre-dispatch contract audit: `TASK1_BRIEF_READY`.
- Task 2 pre-dispatch authority audit: `TASK2_BRIEF_READY`.
- Task 3 pre-dispatch health/review audit: `TASK3_BRIEF_READY`.
- Task 4 pre-dispatch Studio/report audit: `TASK4_BRIEF_READY`.

## Review log

- Task 0 review round 1 (`130df37..eef978e`): NEEDS FIXES.
  - Critical: clean up an environment created by the bootstrap after every post-creation failure.
  - Critical: authenticate the supplied Python executable; version/architecture stdout alone is spoofable.
  - Important: enforce the canonical candidate-specific baseline path.
  - Important: require exact name/version/hash equality between JSON toolchain lock and requirements lock.
  - Controller-added Critical: the committed real lock fails its declared schema on `2.9.0.post0`; schema and validator grammar must agree and the real artifact must validate.
  - Minor: do not hide cleanup errors in the clean-bootstrap test.
- Task 0 fix round 1 (`eef978e..8bb9c5e`): all six findings ADDRESSED; no new Critical/Important breakage.
- Controller verification at `8bb9c5e`: focused `38 passed in 114.78s`; lock/requirements `PASS`; declared schema `PASS`; Python `3.12.10|64bit`; pip `25.1.1`; pytest `8.3.5`; direct pins exact; one `pip-25.1.1.dist-info`; clean git status.
- Canonical baseline `baseline-20260824T024845Z-8bb9c5e5b8a1`: HEAD match, dirty paths 0, total 644 = 630 passed + 14 runtime-gated skips, failures/errors 0; baseline/reference SHA match; six I/O roots PASS and access denied 0. Overall I/O artifact remains correctly `BLOCKED` only for `AUTHORITATIVE_CASE_INVENTORY_MISSING`.
- Task 1 review round 1 (`8bb9c5e..7669d35`): FIX REQUIRED — `supersedes_review_ids` was incorrectly required for an initial review artifact although the contract defines it as optional.
- Task 1 fix round 1 (`7669d35..c0c3c59`): finding ADDRESSED; no new Critical/Important breakage; reviewer verdict `CLEAN`.
- Controller verification at `c0c3c59`: focused Task 1 modules `45 passed in 1.30s`; full authenticated suite `675 passed, 14 skipped, 7 warnings in 198.19s`; JUnit `cfd_projects/_controller_verify/task1-postfix-full.xml`; clean git status before runtime evidence.
- Task 2 initial review (`c0c3c59..77c0cc8`): FIX REQUIRED — raw-source output overwrite, unsafe reparse siblings retaining PASS, explicit stale GCI downgraded to NOT_EVALUATED, irreproducible narrowed GCI root, and non-JSON geometry authority.
- Task 2 fix rounds (`77c0cc8..458b66f`): every finding regression-tested and addressed; follow-up reviews closed field-DXF/native/backslash/non-string legacy path overwrite variants and corrected default-versus-explicit GCI semantics; final scoped verdict `CLEAN`.
- Controller verification at `458b66f`: focused Task 2 modules `96 passed, 7 dependency deprecation warnings in 37.63s`; full authenticated suite `733 passed, 14 skipped, 7 warnings in 364.00s`; JUnit `cfd_projects/_controller_verify/task2-final-full.xml`; clean git status before runtime evidence.
- Task 3 initial review (`458b66f..367ae7b`): FIX REQUIRED — health/evidence TOCTOU, review pre-publish TOCTOU, overwrite-capable review publication, and missing current target schema validation.
- Task 3 fix rounds (`367ae7b..64ead34`): all findings regression-tested and addressed; shared reentrant review-state lock serializes review creation, health publish, and field manifest publish; traversal/reparse terminal paths and the persistent lock file are physically contained and fail closed; final scoped verdict `CLEAN`.
- Controller verification at `64ead34`: focused Task 3 boundary `148 passed, 7 dependency deprecation warnings in 80.59s`; full authenticated suite `785 passed, 14 skipped, 7 warnings in 512.55s`; JUnit `cfd_projects/_controller_verify/task3-final-full.xml`; clean git status before runtime evidence.
- Task 4 initial review (`64ead34..fd0bcee`): FIX REQUIRED — unbounded review request input, shallow health dictionaries able to unseal advice, and report/digest call sites omitting current health authority.
- Task 4 fix round (`fd0bcee..24d18aa`): every finding regression-tested and addressed; the loopback guard precedes bounded request parsing, advice accepts only schema-valid authoritative health, and HTML/Markdown/JSON report paths share freshly recomputed health; final scoped verdict `CLEAN`.
- Controller verification at `24d18aa`: focused Task 4 boundary `260 passed, 7 dependency deprecation warnings in 130.62s`; full authenticated suite `808 passed, 14 skipped, 7 warnings in 582.17s`; JUnit `cfd_projects/_controller_verify/task4-final-full.xml`; exit code 0.
- 2026-08-25 GitHub/next-gate audit at local HEAD `1342a94`: remote `origin` is public with default branch `master`; remote heads do not include `codex/case-evidence-review-gate`; 22 local commits are not in cached remote refs; GitHub CLI authentication is expired.
- 2026-08-25 pre-push safety audit: no high-confidence secret/private-key/token signatures; `cfd_projects/` has zero tracked paths; four root sample DXFs are generator-backed synthetic fixtures. `debug_tools/temp_geometry.json`, associated `temp_export*.dxf`, and reviewed screenshots contain detailed likely real-derived B4 architectural geometry/source context. These blobs are byte-identical to public remote `feature/cfd` commit `f951120`, so the risk predates this branch but fails the revised plan's clean public-push gate.
- 2026-08-25 publication ruling: proceed with public branch backup because the user explicitly selected public continuation after the likely real-derived, already-public geometry risk was disclosed. The branch adds no new sensitive artifact according to the scoped comparison, but acceptance does not convert the safety scan into `clean`; if wrong, the pre-existing public exposure continues and must be remediated separately.

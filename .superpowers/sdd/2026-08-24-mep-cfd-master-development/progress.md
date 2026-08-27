# SDD ledger — plan: docs/superpowers/plans/2026-08-24-mep-cfd-master-development.md

## Setup

- Original checkout preserved at `C:\Users\User\Documents\MEP CFD Studio` on `codex/cfd-studio-improvements`.
- Isolated worktree: `C:\Users\User\Documents\MEP CFD Studio\.worktrees\case-evidence-review-gate`.
- Execution branch: `codex/case-evidence-review-gate`, start commit `bd7d1aaba7a0c083b4931b3f72cdf297e3342780`.
- Selected baseline snapshot: 117 code, schema, benchmark, test, and planning files copied from the approved current checkout; SHA-256 mismatches: 0.
- Excluded from baseline: `.codex-temp`, `.claude`, `.superpowers`, PPTX/render artifacts, `cfd_projects`, generated reports, and the bundled Python installer.
- Task 0 implementation candidate verified and published at `113a2e7b6b20f25f2700408d6a3c11709c48869b`; public first-green run `32800246154` is bound to that exact SHA.

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
- Ruling Task5a-A: Preserve `working_validation.v1.schema.json` exactly: ordered eight checks, existing state labels and derivations, fixed scope/limitations, `design_citable=false`, and `release_ready=false`; producer-specific science belongs in separate schemas — otherwise code-only preparation could silently change the public evaluator contract.
- Ruling Task5a-B: The fixed manifest registry is `_working_validation/local_usability_acceptance.json`, `_working_validation/working-room-v1/working_room_acceptance.json`, `_working_validation/sgi-screening-v1/sgi_screening_acceptance.json`, `_working_validation/heat-box-v1/verification_manifest.json`, and `_working_validation/numerical-spotcheck-v1/numerical_spotcheck.json`; each manifest uses relative paths/hashes and every raw dependency enters `evidence_sha256` — otherwise `latest`, cache, or caller-selected evidence could become authoritative.
- Ruling Task5a-C: 5a implements schemas, pure revalidators, validation-scope guards, and synthetic immutable-tree/tamper tests only. It runs no solver, FreeCAD, Studio, browser, real DXF, or acceptance evidence; genuine runtime producers belong to 5b and confirmed-DXF GUI/restart/usability to 5c — otherwise a synthetic PASS could be mistaken for single-PC evidence.
- Ruling Task5a-D: Modify `working_validation.py`, `cfd_capabilities.py`, and only explicit fail-closed validation scopes in `cfd_physics.py`; create five producer schemas and four validator modules. Reuse `cfd_run.py` and `cfd_post.py` read-only and do not modify `cfd_studio.py` for test convenience — otherwise Task 5a would absorb runtime/UI behavior beyond its gate.
- Ruling Task5a-E: The six-decimal numerical sample fingerprint is a bounded near-copy heuristic that removes insignificant float nonces; it cannot attest an independent solver execution. Task 5b must hash-bind and recompute case-local raw `T/U/phi/yPlus` plus execution evidence before any unbounded no-copy claim.
- Ruling Task5a-F: The aggregate writer protects fixed producer namespaces before manifest existence, accepts only safe canonical output paths, and final-rehashes exact-case/reparse-free/single-link evidence after all validators; saved-run comparison requires two distinct file identities bound by `output_path` — otherwise missing manifests, path aliases, later validators, or one copied run could mutate or counterfeit the eight-gate result.
- Ruling Task5a-G: Local Python 3.14 full-suite failures at the pinned-executable SHA assertion are recorded as non-green environment evidence, not waived. The public workflow's authenticated Python 3.12.10 run is the authoritative Task 5a full-suite gate — otherwise 1,177 unrelated passes could hide a failed toolchain precondition.
- Ruling Task5b-A: The 2026-08-27 user authorization permits synthetic-evidence-only Task 5b execution without confirmed site geometry. This does not appoint an MEP owner, close Task 4.5/5c/M1, or permit synthetic evidence to become design-citable or release evidence — otherwise a current-PC runtime check could be mistaken for site validation.
- Ruling Task5b-B: Keep runtime production in `scripts/produce_local_usability_acceptance.py` and pure revalidation in `scripts/local_usability_acceptance.py`; stage a complete candidate and publish the canonical manifest last only after isolated PASS — otherwise a failed or partial run could replace the prior authority.

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

- Task 0: COMPLETE through Steps 1–8 — baseline commits `eef978e`, `8bb9c5e`; public decision `4170958`; Windows CI `83e7cda`, parse repair `af4de6b`; Windows path-identity repairs `65c4da3`, `97323ec`, `113a2e7`; final independent verdict `CLEAN`; public first-green run `32800246154` at exact HEAD `113a2e7`.
- Task 1: complete — commits `7669d35`, `c0c3c59`; review fix round 1 clean; controller focused verification `45 passed`; controller full verification `675 passed, 14 skipped`.
- Task 2: complete — commits `77c0cc8`, `b533118`, `f24358c`, `2e9282e`, `458b66f`; final reviewer verdict `CLEAN`; controller focused verification `96 passed`; controller full verification `733 passed, 14 skipped`.
- Task 3: complete — commits `367ae7b`, `bcd9f11`, `18dfc2f`, `64ead34`; final reviewer verdict `CLEAN`; controller focused verification `148 passed`; controller full verification `785 passed, 14 skipped`.
- Task 4: complete — commits `fd0bcee`, `24d18aa`; final reviewer verdict `CLEAN`; controller focused verification `260 passed`; controller full verification `808 passed, 14 skipped`.
- Task 0 Step 6: complete — public `origin/codex/case-evidence-review-gate` exists and implementation HEAD `113a2e7` was pushed with zero unpushed commits immediately afterward. The user accepted the disclosed, pre-existing likely real-derived geometry risk; this remains a known-risk exception, not a clean scan.
- Task 0 Step 7: complete — `docs/governance/mep-cfd-raci-availability-2026-08-25.md` records all eight decision rows. Only `codex-agent` development capability is `AVAILABLE_NOW`; every human/external role is `UNASSIGNED/BLOCKED_NO_OWNER` and its dependent Task is excluded from the active critical path.
- Task 0 Step 8: complete — locked Windows CI first green at run `32800246154`, job `97659530263`; 851 tests = 837 passed + 14 skipped, failures/errors 0, 103.521 seconds; JUnit artifact `9546275605`, digest `sha256:9e0b2d7d6d4a8129b075b488b173d666afc5269f79e8a2fac561dc7a96932874`.
- Task 4.5: READY_FOR_MEP_REVIEW (technical preparation only) — a local-only ignored review package now records the exact source audit, 30 terminal candidates, three equipment candidates, fingerprints, and accountable decision fields. Current geometry recomputes to 128 blockers and no confirmed geometry exists. The Task remains OPEN because an accountable MEP reviewer and evidence-backed unit/zone/height/terminal/heat decisions are unavailable; Task 5c/M1 Exit remain blocked.
- Task 5a: COMPLETE (code-only) — commits `1f953ef`, `bec844a`, `d5940b6`, `363fa01`, `9921674`, `1a3228c`, `596358d`, and aggregate closure `f3b7109`; six validators, five producer schemas, fixed dispatch, final evidence rehash, output-authority protection, and tamper tests are implemented. Independent final verdict `CLEAN`; focused split total `421 passed, 7 skipped, 9 subtests passed`; exact-commit public CI run `32822053903` is green with 1,193 tests and 0 failures/errors. No runtime PASS is claimed.
- Task 5b–5c: pending/gated — Step 1 is complete; Step 2 code uses terminal-only level 1 refinement on a 0.125 m background and fixed `deltaT=0.01 s`, but the actual 240 s anchor/repeat has not been rerun. 5c owns confirmed-DXF GUI/restart/usability. Task 4.5 and named human roles remain unavailable.
- Task 6: COMPLETE (code-contract scope) — immutable Design/Scenario/Run schemas and repository, canonical-JSON-derived IDs, atomic publication, path containment, variation whitelist, and current-reference tamper validation are implemented. Controller focused verification: `142 passed`; this does not close Task 5b, M1, or release evidence.
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
- 2026-08-25 Task 0 CI round 1: public run `32792611981` rejected workflow YAML before job creation because a plain `run:` scalar began with PowerShell `&`; commit `af4de6b` changed only that command to a YAML block scalar after independent review.
- 2026-08-25 Task 0 CI round 2: public run `32793134455`, job `97638744731`, created the locked environment and ran the suite but ended `804 passed, 4 failed, 14 skipped`. Three report failures and one FreeCAD capability failure shared the hosted `RUNNER~1` versus `runneradmin` path-identity defect; JUnit artifact `9543877200`, digest `sha256:a4a4ad55c537b8d648e724d1cdf64bf71457f7277a783ad796f69f2cd94670d3`.
- 2026-08-25 Windows path-identity repair: commits `65c4da3`, `97323ec`, and `113a2e7` added identity-aware alias handling while preserving canonical containment, full lexical reparse inspection, raw-dot rejection, existing-parent-only publication, source path/hardlink protection, review locks, and FreeCAD precedence. Three review loops closed P1/P2 findings; final verdict `CLEAN`.
- Controller verification at `113a2e7`: locked Python 3.12.10 focused groups `88 passed, 7 warnings` and `154 passed, 7 warnings`; full authenticated suite `837 passed, 14 skipped, 7 warnings in 565.61s`; JUnit `.superpowers/sdd/2026-08-24-mep-cfd-master-development/ci-windows-path-alias-controller-release-junit.xml`; exit code 0.
- 2026-08-25 Task 0 first green: public run `32800246154`, job `97659530263`, exact HEAD `113a2e7`; checkout, locked Python, lock verification, authenticated environment, environment identity, full pytest, and JUnit upload all succeeded. JUnit artifact `9546275605` reports 851 tests = 837 passed + 14 skipped, failures/errors 0, 103.521 seconds; digest `sha256:9e0b2d7d6d4a8129b075b488b173d666afc5269f79e8a2fac561dc7a96932874`. M0 is complete; general release remains NO-GO and M1 remains blocked by Task 4.5/5b/5c evidence.
- 2026-08-25 Task 5a module integration: serial commits `1f953ef`, `bec844a`, `d5940b6`, `363fa01`; working/SGI commits `9921674`, `1a3228c`; heat/numerical commit `596358d`. Module-level independent verification reported `117 passed, 6 subtests passed` for serial, `135 passed` for working/SGI/restart, and `166 passed, 7 skipped, 41 subtests passed` for the six validator modules before aggregate closure.
- 2026-08-25 Task 5a aggregate review: the initial review reproduced malformed status crashes, output-tree pollution, stale cross-validator evidence, and noncanonical/alias evidence. Follow-up adversarial rounds additionally closed missing-manifest producer writes, exact-case and undeclared hardlink aliases, payload contradictions/duplicate keys, Windows ADS/reserved/cache/temp/traversal output paths, atomic-temp residue, and same-run/two-hardlink comparison bypass. Final independent verdict on current bytes: `CLEAN`; `tests/test_working_validation.py` = `60 passed`, heat/numerical output guards = `2 passed`, compile and diff checks pass.
- 2026-08-25 Task 5a controller focused split: integration `60 passed`; heat/numerical `49 passed`; working/SGI/serial `227 passed`; capabilities/physics `85 passed, 7 skipped, 9 subtests passed`; combined `421 passed, 7 skipped, 9 subtests passed`. Skips remain runtime-gated and are not solver/CAD validation.
- 2026-08-25 Task 5a local full-suite diagnostic under Python 3.14.3: `1177 passed, 2 failed, 14 skipped, 7 warnings, 115 subtests passed in 1299.33s`. Both failures are `tests/test_dependency_lock.py` authenticated-base-Python assertions because the current executable SHA-256 `cce21c...` differs from pinned Python 3.12.10 SHA-256 `4d6f5f...`; this run is not green.
- 2026-08-25 Task 5a authoritative public full suite: [run `32822053903`](https://github.com/donghyun1park-web/mep-parser/actions/runs/32822053903), job `97722069826`, exact code commit `f3b7109386195ae665bd216cb689c686f23dea99`; checkout, locked Python 3.12.10, lock verification, authenticated environment, identity verification, full pytest, and JUnit upload all succeeded. JUnit artifact `9553586783` reports 1,193 tests = 1,179 passed + 14 skipped, failures/errors 0, 217.691 seconds; digest `sha256:1be289869b9ff4a78944997c7f1755361d6be334551045b5607ff5ae678d64d9`. This closes Task 5a's code-only full-suite gate, not Task 5b/5c runtime evidence.
- 2026-08-26 Task 4.5 technical preparation: manual decimal-task brief and sanitized report were produced because the SDD extractor accepts integer headings only. Exact project data remains local and ignored under `cfd_projects/_task45_review/`; no source or confirmed geometry is tracked. Controller and independent review reran the same nine confirmation/safety nodes with 9/9 passing. Source hashes remained unchanged, all 30 terminal and three equipment decisions remain pending, and confirmed geometry count remains 0. Final review after documentation fixes: no Critical/Important technical or privacy finding; Task state remains `READY_FOR_MEP_REVIEW`, not complete.
- Ruling: Once the launch subtask has reached `READY_FOR_BROWSER_REVIEW`, the controller may perform the separately authorized localhost import needed to load an ignored unconfirmed review artifact; this does not relax the launch subtask's source-copy boundary and does not authorize confirmation, save, mesh, solver, or case creation. Runtime status: import succeeded, the review remains fail-closed with all 128 blockers, and the terminal review UI is open with confirmation/save actions unperformed.
- Ruling: Register the 2026-08-27 user statements as `PARTIAL_USER_INPUT_RECORDED`, not accountable MEP approval, because reviewer identity, authority, timestamped evidence, controlled approval, terminal role/host/direction, and heat-source basis remain absent; if wrong, a non-accountable message could incorrectly authorize confirmed geometry or downstream execution.
- Status: Task 4.5 remains `OPEN`; the supplied unit, closed-boundary intent, height, terminal count/airflow, and approximate occupant/lobby-flow heat estimate are locally registered only. All reviewer/evidence fields and row decisions remain pending, the heat input is `SCREENING_ONLY`, and confirmed geometry, case, mesh, and solver counts remain zero.
- Ruling: The second 2026-08-27 user confirmation accepts the candidate terminal configuration (15 supply + 15 exhaust, 444 CMH each, ceiling supply down/exhaust up) but is not accountable MEP approval. Separate-copy authorization is conditional on the existing technical confirmation contract; `NONE_PROVIDED` heat basis leaves 15.5 kW `SCREENING_ONLY`. If wrong, a non-accountable message could publish a scientifically unsupported heat model as confirmed geometry.
- Task 4.5 confirmation attempt: an implementer-added blank CSV row caused a pre-call preprocessing error and was removed without changing the 30 candidate rows or their user-input fields. The one actual real-data confirmation call then returned `ok=false` at the heat-source-required gate with zero heat-source obstacles. Source/runtime inputs were unchanged; confirmed geometry, case, mesh, solver, GCI, and field-job actions remained zero. Status: `CONFIRMATION_AUTHORIZED_BUT_BLOCKED_HEAT_INPUT`; Task 4.5, Task 5c, and M1 Exit remain blocked.
- 2026-08-27 Task 5b Step 1 implementation: added a separate fail-closed producer with candidate staging, same-run evidence binding, manifest-last publication, rollback, sanitized external-runtime failure, and direct CLI support. TDD producer suite reached `14 passed`; a real run exposed an OpenFOAM startup-banner false positive, and the validator was narrowed with regression coverage while preserving actual fatal detection (`4 passed`).
- 2026-08-27 Task 5b Step 1 actual acceptance: authenticated Python 3.12.10 executed FreeCAD 1.1.1/OCC 7.8.1 staged diagnostics, OpenFOAM v2606 serial 64-cell acceptance through physical time 1.0 s with clean `End`, report/runtime baseline generation, Studio HTTP/DOM startup and clean shutdown 3/3, and five actionable diagnostic observations. Candidate and independent post-publication evaluations both returned `PASS` with blockers 0. Runtime evidence remains ignored under `cfd_projects/`; MPI execution smoke remains `NOT_RUN`.
- 2026-08-27 Task 5b Step 1 focused regression: locked Python 3.12.10 ran producer, pure validator, capability, Studio workflow, and physics tests with `289 passed, 7 skipped, 7 warnings in 73.82s`. The skips remain separately runtime-gated and do not weaken the serial acceptance claim.
- 2026-08-27 Task 5b Step 1 pre-commit checks: producer and validator compile PASS; `git diff --check` PASS; `cfd_projects/` tracked paths remain 0; targeted public-safety scan found no new credential/private-key material or local runtime path in the new producer, tests, or sanitized report.
- 2026-08-27 Task 5b Step 1 public verification: code/docs commit `9e625e2dfd6c05b03e0d6efdffbcbf6b8fc5cb35` was pushed to public `origin/codex/case-evidence-review-gate`. Exact-commit Windows CI [run `33028118325`](https://github.com/donghyun1park-web/mep-parser/actions/runs/33028118325), job `98374129769`, completed successfully in 4m7s with `1194 passed, 14 skipped, 7 warnings`; JUnit artifact `9629255682`, digest `sha256:be01310ff1ca7d9dee6eb78234c8822ec84a819694630ded2c03f8d24e5c78fb`.
- Task 5b status: Step 1 COMPLETE; Steps 2–3 OPEN. Task 5b Gate, Task 4.5, Task 5c, M1, design citation, and release remain open/blocked. Detailed sanitized evidence is in `task-5b-step1-report.md`.
- 2026-08-27 approved Task 5b Step 2 criterion update: failed candidates established peak Co 2.40 at terminal level 2 / `deltaT=0.02 s`, peak Co 1.3895 at level 1 / `0.02 s`, and approximately 9.997% terminal-area error at level 0. The contract now fixes terminal-only level 1 refinement and `deltaT=0.01 s` without relaxing Co, area, closure, or repeatability limits. Validator/producer focused regression is included in the `142 passed` Task 6 boundary run; no new canonical 240 s acceptance was published.
- 2026-08-27 Task 6 TDD and controller verification: the initial six tests failed with `ModuleNotFoundError: project_model`; after implementation, nine contract tests passed, including immutable revision, display-name and JSON-format-insensitive identity, geometry-mutation rejection, schema closure, atomic-failure cleanup, path-escape rejection, and reference tamper detection. The combined Task 6/geometry/GCI/working-room boundary completed with `142 passed, 7 warnings` under available Python 3.14. The repository's `.venv-vv` is currently broken because it points to a missing Python 3.12 installation; the required locked Python 3.12 full-suite authority was therefore supplied by the public Windows CI recorded below.
- 2026-08-27 Task 6 public verification: implementation/plans/progress commit `923e4f438c35e390ca0c35600400626cace02393` was pushed to public `origin/codex/case-evidence-review-gate`. Exact-commit locked Windows CI [run `33031962459`](https://github.com/donghyun1park-web/mep-parser/actions/runs/33031962459), job `98386275226`, completed successfully in 4m13s; full pytest reported `1208 passed, 14 skipped, 7 warnings in 212.25 s`, failures/errors 0. Task 6 code-contract scope is complete; Task 5b Step 2 actual 240 s anchor/repeat, Task 5c, M1, and release remain open.

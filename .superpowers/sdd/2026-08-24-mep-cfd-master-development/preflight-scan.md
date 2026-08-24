# SDD pre-flight consistency scan — 2026-08-24 MEP CFD master plan

Scope: read-only consistency scan of `docs/superpowers/plans/2026-08-24-mep-cfd-master-development.md` (the **Plan**) against `exa-results/mep-cfd-benchmark-2026-08-24.md` (the **Spec**) and the named companion plans. References use `Plan Lx-Ly`, `Spec Lx-Ly`, and companion task IDs. “Clean” means internally traceable, not that runtime evidence already exists.

## 1. Per-task self-consistency

| Task | Files / ownership | Interface and predecessor/consumer fit | Tests and command | Commit and gate | Result |
|---|---|---|---|---|---|
| 0 | Existing producers/evaluators/tests are named (Plan L227-L235). Runtime output conflicts with companion P0.1 path. | Baseline ID is produced for later work, but no consumer contract or field is named. | Commands use arbitrary `.venv` before the required P0.0 reproducible `.venv-vv` bootstrap (L246-L264; companion P0.0/P0.1). | No source commit; worktree branch is created only after baseline. M0 is correctly fail-closed. | **Finding F1/F2** |
| 1 | Six new files are complete and isolated (L276-L283). | Catalog + three schemas are appropriately declared for Tasks 2–4; required `case_identity` arrives only in Task 6. | RED, negative schema, and focused commands are specified (L298-L327). | Exact add/commit and M1-inherited gate are present. | **Finding F3** |
| 2 | Producer/test plus a narrow `cfd_result_gate.py` change are named (L348-L353). | Public build/validate interface and raw-artifact recomputation align with P0 evidence goal (Spec L56-L61). | Forgery/staleness matrix and focused command are present (L367-L391). | Exact commit; no independent task gate, consumed by T3/T4. | Clean |
| 3 | Health/review services and `field_pipeline_job.py` delta are named (L428-L435). | Review hash binding is sound; `design_ready`/citation derivation is not a complete normative truth table. | Approval, prerequisite, stale-review and focused suites are named (L451-L492). | Exact commit; M1 relies on it. | **Finding F4** |
| 4 | All listed Studio/report/advice files and regression suites are coherent (L504-L512). | New endpoints consume T2/T3 services and preserve body-results compatibility. | API/stale tests plus focused regression are executable (L526-L554). | Exact commit; M1 UI part is covered. | Clean |
| 5 | Existing validation/capability files plus four named producers and six suites are named (L563-L572). | Public evaluator remains stable; correctly serial-only and non-citable. | Companion Tasks 2–5 and concrete serial/GUI commands are given (L579-L613). | “commit code only” lacks exact add list but is intentionally evidence-safe; M1 gate is explicit. | Clean |
| 6 | Schemas/model/tests are complete (L618-L624). | Immutable Design/Scenario/Run contracts match Spec P1 (L62-L66). | RED/green and geometry contract suites are named (L646-L690). | Exact commit; feeds T7–T9/T19. | Clean |
| 7 | Sidecar migration, v2 schema, readers and tests are complete (L711-L720). | Preserves v1 and prevents resume on changed identity; matches global no-in-place-rewrite constraint. | Regression includes v1 fixture compatibility (L741-L758). | Exact commit; feeds field and UI work. | Clean |
| 8 | Templates/schema/tests and `project_model.py` delta are named (L769-L775). | Prohibits inferred physical values, uses stable geometry IDs, and keeps physics dictionaries owner-specific. | Concrete focused command is supplied (L800-L809). | Exact commit; clean T6 dependency. | Clean |
| 9 | Compare/schema/UI/report/advice/tests are all named (L817-L826). | APIs and comparison eligibility correctly consume immutable identity and preserve wrappers. | Focused suite is executable; browser smoke has no reproducible command/artifact acceptance. | Exact commit and M2 gate are explicit. | **Finding F5** |
| 10 | Anchor schema/service and all listed numerical/field deltas are named (L883-L893). | Separates `GCI_CANDIDATE` from citable final gate and feeds T13. | Cycle/mismatch tests and focused command are given (L909-L943). | Exact commit; companion P1.1–P1.5 supplies the detailed runtime work. | Clean |
| 11 | Generic benchmark registry/validator, source schemas, fixtures and consumers are named (L952-L962). | Directly implements Spec P1 V&V expansion (Spec L62-L66). | Provenance, model-form and focused tests are named (L974-L996). | Exact commit; thresholds correctly remain approval-owned. | Clean |
| 12 | Measurement/calibration schemas, services and downstream files are complete (L1005-L1017). | Hash-preserved imports, disjoint calibration/holdout and fail-closed pre-approval behavior are coherent. | Focused command and exact commit are present (L1044-L1076). | Feeds T13 and field-facing health/report/UI consumers. | Clean |
| 13 | Read-only inventory producer/test and evidence root are named (L1081-L1090). | Consumes T10–T12 outputs and companion P2–P7; no mutation rule is sound. | Candidate commit, tests, inventory command and placeholder substitution are explicit (L1104-L1149). | Commit establishes frozen M3 candidate; evidence storage rule is sound. | **Finding F6** |
| 14 | Comfort module/schema/tests and downstream integrations are complete (L1154-L1164). | Conditional `NOT_EVALUATED`, source hierarchy and no quiet defaults implement Spec P2 (L67-L72). | Reference-vector/test command is explicit. Approved source/vector is an external prerequisite, not supplied in the task. | Exact commit and Design Partner M4 gate are stated. | **Finding F7** |
| 15 | IAQ modules/schemas/tests and physics/post/UI integrations are named (L1234-L1244). | Input completeness and fail-closed claim scope match Spec P2. | Conservation suite is concrete, but no IAQ benchmark source/family/runner command can yield the required M5 age-of-air benchmark PASS. | Exact commit; M5 overstates what its listed work can prove. | **Finding F8** |
| 16 | Radiation/CHT schemas/service/tests and producer changes are named (L1311-L1325). | Separate surface contract correctly preserves `geometry.v2`; benchmark-first production gate matches Spec P2. | Two-plate execution and test command are present. Approved radiation/CHT tolerances remain external by design. | Commit deliberately withholds production flag pending raw-evidence review; M6 is correct. | Clean |
| 17 | Support/docs and install/release deltas are listed (L1384-L1395). | Support privacy/status vocabulary/rollback intent is coherent and matches Spec P0. | Final command runs checkout source with relative `cfd_projects` and no v1/v2 contract; it cannot satisfy companion P8.2/P10 installed-package authority. | M7 claims frozen-RC release despite the non-authoritative command. | **Finding F9** |
| 18 | Parallel/capability/physics/run/Studio changes plus schema/test are named (L1455-L1463). | Opt-in serial-equivalence contract preserves global serial-first constraint. | Smoke and equivalence command is concrete; approval-owned tolerance/evidence schema is underspecified. | Exact commit; M8-A correctly keeps default serial. | **Finding F10** |
| 19 | Surrogate/schema/tests plus model/compare/Studio/report consumers are complete (L1510-L1517). | Design/site holdout and `CFD_REQUIRED` fail-closed response satisfy Spec P3. | OOD-focused command is concrete; externally approved model/error/OOD thresholds are intentionally prerequisites. | Exact commit; must not silently join a prior frozen RC. | **Finding F11** |

## 2. Shared-boundary table

Only explicit shared files, named interfaces, schemas, or declared data contracts are counted. Producer is earlier task in plan order.

| Earlier → later | Shared boundary | Earlier produces | Later consumes/modifies | Risk | Result |
|---|---|---|---|---|---|
| T0 → T5 | `working_validation.py`, `tests/test_working_validation.py` | Baseline evaluator/test | Code-owned completed checks | Baseline test environment drift | Clean |
| T0 → T13 | `vv_baseline.py`, `tests/test_vv_baseline.py` | Baseline evidence | Frozen inventory validation | Path/identity mismatch F2 | Finding |
| T1 → T2 | status + `case_evidence.v1` | Status vocabulary/evidence schema | Evidence bundle producer | Case identity timing F3 | Finding |
| T1 → T3 | `case_health.v1`, `case_review.v1`, catalog | Health/review contracts | Health/review services | Citation truth-table F4 | Finding |
| T1 → T4 | status catalog / health-review API semantics | User-facing labels | Studio/report presentation | Copy drift unless contract test | Clean |
| T1 → T17 | status vocabulary | Shared status semantics | Docs/release-wide copy contract | Duplicated catalog authority | Clean |
| T2 → T3 | `cfd_evidence.py`; `cfd_result_gate.py`; result/field tests | Recomputed evidence | Health and review binding | Evidence path/hash must remain authoritative | Clean |
| T2 → T4 | `tests/test_cfd_result_gate.py`; case evidence | Gate regression/evidence | Health endpoint/report view | Cached summary bypass | Clean |
| T2 → T7 | `tests/test_field_pipeline_job.py` | Field regression baseline | Identity-linked field pipeline | Test ownership collision | Clean |
| T2 → T10 | `cfd_result_gate.py`, result/field tests | Result-gate behavior | Anchor-aware result gate | Gate semantics regression | Clean |
| T2 → T11 | `cfd_result_gate.py`, result test | Citation gate | Benchmark applicability integration | Benchmark may self-declare PASS | Clean |
| T2 → T16 | `cfd_result_gate.py`, result test | Citation gate | Radiation/CHT gating | New physics must not weaken gate | Clean |
| T3 → T4 | `case_health.v1`, `case_review.v1`, health/review services | Health/review records | GET/POST and report rendering | Incomplete design-ready derivation F4 | Finding |
| T3 → T7 | `field_pipeline_job.py`, field test | Terminal health fields | v2 identity/resume fields | Manifest migration collision | Clean |
| T3 → T10 | `field_pipeline_job.py`, field test | Field health/current evidence | Validation-anchor authority | Hash/reference drift | Clean |
| T3 → T11 | `tests/test_cfd_result_gate.py` | Result-gate regression expectation | Benchmark gate change | Shared test update must retain health cases | Clean |
| T3 → T12 | `cfd_case_health.py` | Case-health check model | Field-calibrated status | F4 leaves citable mapping ambiguous | Finding |
| T3 → T14 | `cfd_case_health.py` | Evidence/citation status | Comfort visibility gate | No upgrade via comfort | Clean |
| T3 → T15 | `cfd_case_health.py` | Evidence/citation status | IAQ visibility gate | No upgrade via IAQ | Clean |
| T3 → T16 | `tests/test_cfd_result_gate.py` | Result-gate regression | CHT gate change | Physics must not mutate health truth | Clean |
| T3 → T17 | case-health/review status semantics | Blockers/review validity | Release/UI vocabulary | Multiple status sources | Clean |
| T4 → T7 | `cfd_studio.py`, `tests/test_studio_workflow.py` | Existing UI compatibility | Case scanning/legacy metadata | Route regression | Clean |
| T4 → T9 | `cfd_studio.py`, `cfd_report.py`, `cfd_advice.py`, shared tests | Evidence UI/report | Design/Scenario UI and compare reports | UI/report merge ordering | Clean |
| T4 → T10 | `tests/test_cfd_result_gate.py` | Gate UI regression | Anchor-aware gate | Test suite integration | Clean |
| T4 → T11 | `cfd_report.py`, result test | Report/citation presentation | Benchmark applicability report | Watermark/citation inconsistency | Clean |
| T4 → T12 | `cfd_studio.py`, `cfd_report.py`, workflow/report tests | Evidence card/report | Measurement mapping/field report | UI route ownership | Clean |
| T4 → T14 | `cfd_studio.py`, `cfd_report.py`, `cfd_advice.py` | Evidence label pattern | Comfort UI/report/advice | Comfort claim must stay conditional | Clean |
| T4 → T15 | `cfd_studio.py`, `cfd_report.py` | Evidence presentation | IAQ UI/report | Claim label drift | Clean |
| T4 → T16 | `cfd_report.py`, result/report tests | Report gate styling | CHT/radiation reporting | Production flag leakage | Clean |
| T4 → T17 | `cfd_studio.py`, workflow test | Local-only evidence UI | Readiness/support center | User-path regression | Clean |
| T4 → T18 | `cfd_studio.py` | Serial UI behavior | MPI opt-in presentation | Default serial must persist | Clean |
| T4 → T19 | `cfd_studio.py`, `cfd_report.py` | Evidence/report layout | Surrogate watermark/results | Misleading recommendation presentation | Clean |
| T5 → T13 | `cfd_verification.py` and working-PC evidence | Serial/exact verification producers | Frozen scientific inventory | Candidate/current identity drift | Clean |
| T5 → T17 | `cfd_capabilities.py` | Runtime capability semantics | Setup/readiness center | Duplicate capability claims | Clean |
| T5 → T18 | `cfd_capabilities.py`, capability tests | Serial capability baseline | Evidence-gated MPI capability | MPI default/identity regression | Clean |
| T6 → T7 | `project_model.py`, project tests, identity schema | Immutable repository model | Legacy run linking | v1 data mutation | Clean |
| T6 → T8 | `project_model.py`, Design/Scenario contract | Reviewed design/scenario | Template application/diff | Geometry vs scenario change misclassification | Clean |
| T6 → T9 | Design/Scenario/Run APIs/model | Immutable IDs/revisions | CRUD/run/compare UI | Identity availability before UI | Clean |
| T6 → T19 | `project_model.py`, project test | Run identity | Training inventory | Leakage via revisions | Clean |
| T7 → T9 | `cfd_studio.py`; legacy/run identity | Safe legacy scan/link | New UI and compare lookup | Legacy routes break | Clean |
| T7 → T10 | `field_pipeline_job.py` | v2 identity field pipeline | Field-authority anchor | Resume/anchor mismatch | Clean |
| T7 → T13 | field identity and resume provenance | Linked field run | Frozen field evidence | Identity hash drift | Clean |
| T7 → T19 | `project_model.py` | Legacy-linked identities | Dataset inventory | Legacy unlinked rows leakage | Clean |
| T8 → T9 | Scenario semantic diff/template contract | Validated scenario values | Scenario UI/compare input diff | Template role mapping | Clean |
| T8 → T19 | `project_model.py`, scenario attributes | Stable scenario features | Surrogate feature/OOD logic | Template version feature drift | Clean |
| T9 → T19 | `cfd_compare.py`, `cfd_studio.py`, `cfd_report.py` | Run comparison/report UI | Recommendation ranking/watermark | Recommendation confused with comparison evidence | Clean |
| T10 → T11 | `cfd_result_gate.py` | Anchor-aware citable gate | Benchmark applicability integration | Cross-gate circularity | Clean |
| T10 → T12 | field-authority anchor interface | Anchor/hash rules | Measurement/field comparison | Calibration/holdout authority mismatch | Clean |
| T10 → T13 | `validation_anchor.v1`, anchor service | Immutable authority anchor | Frozen V&V inventory | Candidate/anchor drift | Clean |
| T10 → T16 | `cfd_result_gate.py` | Final citation-gate separation | Radiation/CHT citable gate | Physics-specific bypass | Clean |
| T11 → T12 | `cfd_report.py` and benchmark applicability | Comparator/report vocabulary | Field comparison report | Benchmark/field evidence conflation | Clean |
| T11 → T13 | benchmark source/validation manifests | Approved benchmark registry | Candidate scientific evidence | Threshold authority conflict F6 | Finding |
| T11 → T15 | benchmark registry interface | Generic source/validation mechanism | IAQ benchmark claim | Missing IAQ family/command F8 | Finding |
| T11 → T16 | `cfd_report.py`, `cfd_result_gate.py`, benchmark contract | Benchmark gate/report | Radiation/CHT validation | Approved-tolerance requirement | Clean |
| T12 → T13 | field measurement/calibration/validation schemas | Holdout/UQ evidence | Frozen field validation | Threshold authority conflict F6 | Finding |
| T12 → T14 | `cfd_post.py`, `cfd_report.py`, `cfd_studio.py`, `cfd_case_health.py` | Sampled evidence/health view | Comfort weighting/report | Field result must not enable comfort claim | Clean |
| T12 → T15 | same post/report/Studio/health boundary | Sampled evidence/health view | IAQ field comparison/card | IAQ field protocol absent F8 | Finding |
| T12 → T16 | `cfd_post.py`, `cfd_report.py` | Time-window/postprocess rules | CHT output/report | Thermal balance convention drift | Clean |
| T12 → T17 | `cfd_studio.py`, workflow test | Measurement UI | UAT/release flow | Installed UI differs from source | Finding F9 |
| T13 → T17 | frozen scientific evidence/inventory | M3 validation bundle | P9 release acceptance/audit | Checkout command violates installed RC authority F9 | Finding |
| T14 → T15 | `cfd_post.py`, `cfd_report.py`, `cfd_studio.py`, `cfd_case_health.py` | Conditional module display/status | IAQ module display/status | Module-level statuses must not raise case status | Clean |
| T14 → T16 | `cfd_post.py`, `cfd_report.py` | MRT source labels | Radiation/CHT MRT source | Validated vs approximate MRT leakage | Clean |
| T14 → T17 | `release_audit.py`, `cfd_studio.py` | Comfort claim gate | Release/UI status | Core release must not require optional comfort | Clean |
| T14 → T19 | `cfd_report.py`, `cfd_studio.py` | Conditional comfort reporting | Surrogate UI/report | Recommendation must not imply comfort approval | Clean |
| T15 → T16 | `cfd_physics.py`, `cfd_post.py`, `cfd_report.py`, `cfd_case_health.py` | Feature-profile/postprocess patterns | CHT/radiation implementation | Result manifests must remain distinct | Clean |
| T15 → T17 | `cfd_studio.py` | IAQ status card | UAT/release UI | Optional IAQ does not block core release | Clean |
| T15 → T18 | `cfd_physics.py`, `cfd_studio.py` | Serial scalar feature profile | MPI-aware run/UI changes | Parallel IAQ equivalence omitted | Finding F10 |
| T15 → T19 | `cfd_studio.py`, `cfd_report.py` | IAQ report/status | Surrogate presentation | Prediction not IAQ claim | Clean |
| T16 → T18 | `cfd_physics.py` | Radiation/CHT profile | MPI run changes | MPI equivalence scope omitted | Finding F10 |
| T16 → T19 | `cfd_report.py` | CHT/radiation report | Surrogate report | No physics claim through surrogate | Clean |
| T17 → T18 | `cfd_capabilities.py`, `cfd_studio.py` | Installed readiness/serial UX | MPI opt-in UX | Later code invalidates frozen RC F11 | Finding |
| T17 → T19 | `cfd_studio.py` | Frozen release UI | Surrogate UI addition | Later code invalidates frozen RC F11 | Finding |
| T18 → T19 | `cfd_studio.py` | Optional backend presentation | Surrogate UI | Two optional feature states compete for UX | Clean |

## 3. Conflicts, defects, and missing prerequisites

### Load-bearing

1. **F1 — M0 uses the wrong validation environment/order.** Plan T0 runs `.venv\\Scripts\\python.exe` before invoking companion P0.0–P0.2 (Plan L246-L267), while companion P0.0 requires a hash-locked `.venv-vv` bootstrap and companion P0.1 uses that interpreter. This weakens the stated reproducible baseline and can let Task 0 pass using an unpinned environment.

2. **F2 — Baseline artifact path/shape is contradictory.** T0 declares `cfd_projects/_release_evidence/vv_baseline.v1.json` (Plan L234), but the exact companion P0.1 producer emits `cfd_projects/_release_evidence/vv/{candidate_id}/vv_baseline.json` with `candidate_id` and JUnit sibling. No adapter/path mapping is specified. Later frozen inventory needs a single authoritative reference.

3. **F3 — M1 evidence requires `case_identity` before Task 6 creates its contract.** T1 makes `case_identity` mandatory in Case Evidence (Plan L315-L317); T2 produces that evidence (L356-L363); T5/M1 requires actual-DXF case evidence before the M2/T6 identity model exists (L602-L614; L616-L690). This violates the planned M1→M2 sequencing unless a versioned legacy identity reference is explicitly legal.

4. **F4 — `design_ready` / citation status has no exhaustive decision contract.** T1 fixes check IDs (L292-L296) and T3 lists check fields plus two examples (L450-L466), but neither states the exact boolean/precedence matrix for `SCREENING_ONLY`, `NOT_EVALUATED`, `CITATION_BLOCKED`, and `DESIGN_CITABLE` across purpose, optional modules, failures, blocks, and field evidence. This is the load-bearing trust-state calculation required by Spec P0 (Spec L56-L61).

5. **F6 — Task 13's instruction to execute companion P2.1–P7.3 imports unapproved thresholds contrary to the master plan.** Master §9 declares TAB temperature/velocity/flow U95 bands and radiation tolerances informational until source/reviewer/version/hash approval (Plan L1594-L1605), and T12 likewise requires approved uncertainty budget before `field_validated` (L1040-L1042). Companion P7.3 hard-codes terminal ±5%, T MAE ≤1 K, U error and U95 thresholds; companion P6.4 hard-codes radiation tolerances. T13 says to execute those tasks exactly (L1099, L1127-L1139). The plan’s authority section does not resolve which rule wins for pass/fail.

6. **F8 — M5 is not executable as written.** T15 requires an age-of-air benchmark and field comparison path for claim activation (Plan L1275-L1288, L1304-L1306), but neither T11’s registered families (L980-L987) nor T15 creates an IAQ/age-of-air benchmark source, fixture, runner invocation, or a test that verifies `benchmark PASS`. Conservation alone cannot meet the M5 gate or Spec P2 requirement for separately validated IAQ (Spec L67-L72).

7. **F9 — M7’s final acceptance command cannot establish a frozen installed-RC release.** T17 invokes checkout-source `release_audit.py --projects-root cfd_projects` (Plan L1435-L1440). Companion P8.2 requires installed app-owned paths and `release_audit.py --contract v2`; companion P10.3 requires the installed audit executable and both v1/v2 runs. Therefore the stated command cannot prove two-clean-PC/package/recovery release evidence, even if it returns PASS.

### Non-blocking but must be ruled before the affected gate

8. **F5 — T9 browser smoke is unbound.** T9 says browser smoke must reach Design/Scenario/compare (Plan L859-L867) but supplies neither a browser command nor a saved acceptance artifact. Unit/API coverage is still executable.

9. **F7 — Comfort reference authority is external but not scheduled as an input acquisition.** T14 requires an approved ISO/ASHRAE reference-vector source/hash (Plan L1182-L1186), yet no task registers or approves it. Safe failure is described, so this blocks M4 only rather than core work.

10. **F10 — MPI equivalence scope/threshold is underspecified for new physics.** T18 requires empirical reviewer-approved tolerance (Plan L1474-L1479), but its schema/interface does not say whether the comparison includes IAQ scalar (T15) and radiation/CHT energy fields (T16), even though both modify `cfd_physics.py`. It must fail closed until an approved scope/tolerance manifest exists.

11. **F11 — T18/T19 can invalidate M7’s frozen RC with no re-RC rule.** T17 freezes and audits a release candidate (Plan L1417-L1447); T18/T19 then commit changes to runtime/UI/report (L1494-L1505; L1559-L1570). Companion P8.4 says any post-RC code change is NO-GO until a new RC/P9–P10 cycle. Core M7 can ship without M8, but an M8-containing release needs an explicit re-RC dependency.

## 4. Proposed controller rulings

| Finding | Smallest exact ruling | Why | Cost if wrong |
|---|---|---|---|
| F1 | Move companion P0.0 bootstrap ahead of both T0 pytest commands; bind `$Python` to `.venv-vv\\Scripts\\python.exe`. | Makes M0’s baseline reproducible. | False-green baseline and irreproducible later failures. |
| F2 | Adopt companion P0.1 path as canonical and add one explicit `baseline_evidence_path` reference wherever T0/T13 consume it; delete the conflicting T0 runtime path. | One artifact identity is necessary for hash-chain validation. | Inventory can validate the wrong/stale baseline. |
| F3 | In T1 define mutually exclusive `case_identity` (T6+) and `legacy_case_ref` (M1) branches; require immutable geometry/run hashes for the latter and prohibit it from `DESIGN_CITABLE`. | Lets the first screening slice work without inventing T6 early. | M1 cannot produce valid evidence, or legacy cases get falsely citable identities. |
| F4 | Add a versioned `citation_decision_table` to `case_health.v1` plus exhaustive table-driven tests before T3 implementation. | Health is the central user-facing authority. | Different endpoints/reports issue different trust states. |
| F5 | Add one repeatable local browser smoke command and a small JSON/HTML acceptance record to T9. | Turns a manual assertion into reviewable evidence. | M2 GUI route failure escapes unit tests. |
| F6 | Controller must choose one authority: either approve/version the P6.4/P7.3 thresholds into the master’s approved manifests before T13, or run them informational-only and forbid PASS/L3 from them. | Resolves direct threshold contradiction without relaxing any criterion. | Invalid L3 PASS or permanently blocked V&V from conflicting rules. |
| F7 | Create an approval intake item supplying vector source edition, fixture hash, and reviewer ID before opening M4’s exposure gate. | Allows T14 code/tests now while keeping claims closed. | Comfort PASS rests on untraceable vectors. |
| F8 | Add an IAQ family to T11 or T15: source/uncertainty manifest, age-of-air/step-response case, benchmark runner command, and M5 evidence test; retain `NOT_EVALUATED` until it passes. | Supplies the missing evidence path mandated by M5/Spec P2. | IAQ feature is labelled validated based only on conservation tests. |
| F9 | Replace T17 Step 9 with companion P10.3 installed-executable commands using `--contract v1` and `--contract v2`; retain source pytest only as pre-RC developer evidence. | Satisfies frozen-package/two-PC release authority. | Source checkout PASS is mistaken for releasable installer PASS. |
| F10 | Extend `mpi_runtime_smoke.v1` with `physics_profiles`, QoIs, approved tolerance-manifest hash, and an explicit unsupported-profile serial fallback; test IAQ/CHT selection. | Keeps MPI opt-in fail-closed across future physics. | Parallel runs may silently lack equivalence proof for enabled physics. |
| F11 | State: “T18/T19 are post-M7 research/optional branches; any merge intended for an L4 release restarts P8.4→P10 on a new RC.” | Preserves frozen-RC integrity without blocking core L4. | A post-audit code change is shipped under stale release evidence. |

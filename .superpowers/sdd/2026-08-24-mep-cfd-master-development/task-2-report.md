# Task 2 report — authoritative immutable Case Evidence

## Scope and file ownership

- Worktree: `C:\Users\User\Documents\MEP CFD Studio\.worktrees\case-evidence-review-gate`
- Branch: `codex/case-evidence-review-gate`
- Required baseline: `c0c3c59691976d23b16a6de0c256083319f79b99`
- Created `cfd_evidence.py`.
- Created `tests/test_cfd_evidence.py`.
- `cfd_result_gate.py` and `tests/test_cfd_result_gate.py` were not modified.  The existing public `body_fitted_numerical_provenance_issues()` helper was sufficiently narrow and retained the current system-file and semantic numerics checks without weakening the result gate.
- No Task 3 health evaluator, review persistence, API, UI, solver, benchmark producer, numerical-sensitivity promotion, or citation promotion was implemented.

## RED evidence

The complete initial tamper-matrix test file was written before the production module.  The authenticated Python 3.12 command was:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_evidence.py
```

The expected missing-feature RED was captured:

```text
E   ModuleNotFoundError: No module named 'cfd_evidence'
1 error in 0.89s
```

The first implementation run exposed three real defects:

```text
3 failed, 32 passed in 12.85s
```

The defects were Windows absolute selected-case path handling for GCI and an unsafe symlink fallback while constructing a blocked geometry reference.  After the symlink/path correction, the remaining common GCI directory-vs-file resolution defect produced:

```text
2 failed, 33 passed in 15.99s
```

The directory-valued GCI selected-case provenance was then handled separately from file-valued artifact references.

Two self-review findings were also regression-tested RED before correction:

```text
# Explicit unreadable optional GCI root was incorrectly treated as absent
1 failed, 35 deselected in 1.06s

# Surface output ../ traversal was blocked, but without the required PATH_ESCAPE-class code
1 failed, 2 passed, 36 deselected in 1.38s
```

These were corrected so an explicitly supplied unreadable GCI root is `BLOCKED`, while a genuinely absent default GCI source is `NOT_EVALUATED`, and lexically unsafe raw child paths produce `PATH_ESCAPE` before any read/hash.

## GREEN evidence

The first complete Task 2 suite GREEN was:

```text
35 passed in 11.21s
```

The first prescribed focused regression was:

```text
73 passed in 14.18s
```

After the unreadable-GCI self-review fix:

```text
74 passed in 12.68s
```

The final fresh prescribed focused command was:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_evidence.py tests/test_cfd_result_gate.py tests/test_field_pipeline_job.py
```

Exact final result:

```text
........................................................................ [ 93%]
.....                                                                    [100%]
77 passed in 14.19s
```

The authenticated Python 3.12 compile check also exited 0:

```powershell
& .venv-vv\Scripts\python.exe -B -m py_compile cfd_evidence.py tests/test_cfd_evidence.py
```

No full-suite result is claimed.  The controller will independently run the full suite.

## Authority decisions implemented

1. `projects_root` is resolved as the authority root. `case_dir` must be a real directory strictly beneath `_body_solver`. Every new stored artifact link is normalized projects-root-relative POSIX text.
2. Reads are fail-closed: lexical `.`/`..`, drive-relative, backslash-relative, symlink/reparse traversal, root escape, generated namespaces, report/cache/temp/release evidence, and unsafe declared children are rejected before use. Current producer absolute raw paths are accepted only when they resolve to a real contained non-reparse artifact; they are normalized on output.
3. Discovery is an exact direct-child allow-list: `_occ_geometry/*/surface_manifest.json`, `_body_mesh/*/mesh_manifest.json`, and permitted-GCI-root `*/grid_convergence.json`. No recursive project glob can discover a report, cache, previous evidence file, numerical preparation, or presentation output as authority.
4. The chain is recomputed in order: current `geometry.v2`; the byte-identical canonical OCC surface and its STL/BREP; the byte-identical canonical mesh plus copied surface and `mesh_input.json`; solver mesh copy; `thermal_input.v1`; current buoyant run and numerical system provenance; standalone canonical progress with exact embedded JSON equality; result VTU/summary/x-y-z slices and run/mesh/thermal hashes; then unique current GCI.
5. `numerics_verified` uses the current run, current thermal input, current `controlDict`/`fvSchemes`/`fvSolution`, canonical effective settings/numerics, restart input when selected, and `body_fitted_numerical_provenance_issues()`. No numerical-sensitivity/preparation artifact is discovered or promoted.
6. Exactly one schema-valid `grid_convergence.v3` manifest matching the selected solver directory and all four current hashes passes `grid_verified`. Zero matches is `NOT_EVALUATED`; multiple matches is `BLOCKED` with `AMBIGUOUS_GCI_EVIDENCE`; an explicitly supplied unreadable/invalid GCI root is `BLOCKED`.
7. Absent field evidence is `NOT_EVALUATED`. Supplied field evidence must be under `_release_evidence`, pass its schema and `field_acceptance.validate_evidence()`, and bind the exact selected geometry/surface/mesh/run/result paths and current hashes; unsafe or invalid supplied evidence is `BLOCKED`.
8. Benchmark authority remains absent/`NOT_EVALUATED`. Legacy evidence is fixed to `purpose=screening`; its relocation-stable ID is `legacy-` plus the first 20 lowercase SHA-256 hex characters of the canonical four-field geometry/run reference tuple.
9. The default output is `case_dir/case_evidence.v1.json`. Publishing uses a same-parent dot-prefixed staging file, JSON write, flush, `os.fsync`, `os.replace`, and exception cleanup. Output paths cannot overwrite any selected source artifact. Previous evidence and staging files are never input candidates.
10. `validate_case_evidence()` validates the stored schema and root-relative refs, rehashes every stored ref, repeats the complete raw recomputation, ignores only the non-authoritative creation timestamp when comparing, and reports stale source/manifests rather than trusting the stored status/checks.

## Tamper coverage

The test matrix covers caller-authored PASS, missing each core artifact, current-producer absolute contained paths, geometry mutation, surface output mutation, raw path traversal, mesh surface copy and mesh-input mutation, thermal input and system-file mutation, self-consistent upwind claims, run/result/VTU/summary/slice mutation, missing slice axis, canonical progress disagreement and impossible history, zero/one/multiple/stale/wrong-contract/unreadable GCI, numerical-preparation exclusion, absent and supplied-invalid field evidence, generated/release namespace exclusion, symlink escape, relocation-stable identity, self-output exclusion, clean repeat validation, later manifest/raw-source mutation, edited/escaping stored evidence, atomic staging cleanup, and unsafe/source-overwriting output paths.

## Limitations and concerns

- This task recomputes existing artifacts; it did not run FreeCAD, OpenFOAM, a real GCI study, or a live field-DXF pipeline. Test fixtures exercise the real filesystem, schemas, hashing, semantic validators, and existing field/result validators, but are not solver execution proof.
- Positive field evidence remains delegated to the existing independent `field_acceptance.validate_evidence()` plus exact selected-chain comparison. Task 2 adds no new DXF parser or field acceptance producer.
- Only the controller-approved final `grid_convergence.v3` contract is eligible for GCI authority. Earlier GCI contracts and numerical-sensitivity preparation artifacts remain non-authoritative.
- Evidence is intentionally legacy screening evidence. It does not evaluate Task 3 health, approvals, `design_ready`, or design citation, and cannot become `DESIGN_CITABLE` in this task.
- No full-suite PASS claim is made; only the exact focused regression above was captured.

## Self-review

- Scope diff contains only the new evidence module and its test file; no existing result/field gate was weakened.
- New path writes are limited to the requested evidence output and same-parent temporary sibling. Tests verify staging cleanup on a forced flush failure.
- Hash expectations in the tests are independently derived from fixture bytes, and behavior assertions target recomputed status/error/ref outcomes rather than mocked PASS values. The only mock forces an OS flush failure to observe real cleanup behavior.
- Mutation review confirms tests fail for missing checks, wrong branch selection, stale hashes, unsafe paths, absent atomic cleanup, permissive GCI first-match selection, caller status trust, and failure to recompute after later source mutation.

## Review fix round 1 (2026-08-24)

### Finding-by-finding disposition

1. **Complete output/source separation.** Recalculation now accumulates every real raw file it consults, including canonical and solver-copy manifests, geometry, surface STL/BREP, mesh input and mesh STL, run/thermal/progress, numerical system files, selected restart input, result VTU/summary/slices, and GCI candidates. Output validation rejects equality with any accumulated source before creating the atomic staging file. Regression coverage directly protects `system/fvSchemes`, canonical `mesh_input.json`, OCC STL, result VTU, a result slice, and a geometry candidate rejected for a non-JSON suffix. The fixture uses initial thermal input, so no restart file is selected in this matrix; the implementation tracks `thermal_restart_input.json` when run provenance selects it.
2. **Unsafe authority siblings.** Any direct-child symlink/reparse candidate observed under `_occ_geometry`, `_body_mesh`, or `_body_gci` now blocks that authority before a valid sibling can pass it. OCC and mesh therefore block their associated core checks; GCI returns a specifically `BLOCKED` grid check with `PATH_ESCAPE`. The Windows symlink regressions ran in the authenticated environment rather than skipping.
3. **Explicit stale/wrong-case GCI.** A supplied canonical GCI root with a schema-valid but stale or wrong-case final manifest now returns `BLOCKED` with `GCI_EVIDENCE_STALE`. The default authority still implements the binding zero-match rule: when it contains only another case's otherwise valid evidence, `grid_verified` is `NOT_EVALUATED`.
4. **Closed reproducible GCI scope.** A supplied `gci_root` must resolve exactly to `projects_root/_body_gci`; a study subdirectory is rejected. Stored PASS evidence can be revalidated after the selected manifest is moved between sibling study directories. Explicit missing/stale failures carry deterministic error markers that let `validate_case_evidence()` repeat the same canonical-root computation without expanding the v1 schema or validator interface.
5. **Geometry authority suffix.** A resolved geometry candidate must end in `.json` before any JSON read or content hash. Valid geometry.v2 bytes renamed to `.txt` block `geometry_valid` with `GEOMETRY_PATH_INVALID`; the rejected candidate is still included in output protection so publishing cannot overwrite it.

Schema-invalid GCI candidates are also consulted before a matching PASS can be returned. Core authority-namespace errors cannot leave the affected core status at PASS: unsafe OCC selection clears `surface_ok`, and unsafe mesh selection clears `mesh_ok`.

### RED evidence

The complete reviewer regression selection was added before production changes and run with:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_evidence.py -k "geometry_authority_requires_json_suffix or stale_other_case_gci or narrowed_gci or sibling_study_revalidates or unsafe_sibling_candidate or overwrite_authoritative_raw_child"
```

Exact RED summary:

```text
11 failed, 1 passed, 38 deselected in 3.38s
```

The one pass was the clean sibling-study revalidation control. The eleven failures covered non-JSON geometry acceptance, stale/wrong-case explicit GCI being treated as absent, narrowed-root acceptance, unsafe OCC/mesh/GCI siblings being ignored, and all five requested raw-source overwrite targets.

Self-review then added two stricter boundary regressions before their corrections:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_evidence.py -k "stale_other_case or default_gci_authority_with_only_other_case or rejected_geometry_candidate"
```

Exact additional RED summary:

```text
2 failed, 1 passed, 49 deselected in 2.65s
```

Those failures showed default other-case GCI was over-blocked and a rejected `.txt` geometry candidate was not yet output-protected.

### GREEN and final verification evidence

The reviewer regression selection became:

```text
12 passed, 38 deselected in 3.40s
```

The complete evidence test file then reported:

```text
50 passed in 15.40s
```

After the two self-review boundary corrections, their targeted selection reported:

```text
3 passed, 49 deselected in 2.05s
```

The final fresh prescribed focused command was:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_evidence.py tests/test_cfd_result_gate.py tests/test_field_pipeline_job.py
```

Exact final result:

```text
........................................................................ [ 80%]
..................                                                       [100%]
90 passed in 24.80s
```

The final authenticated compile command and diff check both exited 0:

```powershell
& .venv-vv\Scripts\python.exe -B -m py_compile cfd_evidence.py tests/test_cfd_evidence.py
git diff --check
```

Only `cfd_evidence.py` and `tests/test_cfd_evidence.py` are intended for this review-fix commit. No full-suite result is claimed; the controller will run it independently.

### Review-fix limitations and self-review

- No Task 3 health evaluation, review persistence, API/UI, solver, benchmark, citation promotion, or schema expansion was added.
- No existing result or field gate was weakened. The change only narrows authority selection, makes unsafe candidates fail closed, protects raw inputs from output replacement, and preserves the default zero-matching-GCI `NOT_EVALUATED` rule.
- Atomic publication remains same-parent write, flush, `fsync`, `os.replace`, and exception cleanup. Source equality is rejected before staging creation, and each overwrite regression verifies original bytes remain unchanged.
- The diff was checked for whitespace errors; only Git's existing LF-to-CRLF working-copy warnings were emitted.
- This remains filesystem/schema/hash/semantic validation of fixtures, not evidence that FreeCAD, OpenFOAM, a real GCI study, or a live field-DXF pipeline ran.

## Review fix round 2 (2026-08-24)

### Disposition A: protect every contained field source before validation

The field-evidence regression creates a real non-sample DXF with ezdxf, reconnects the complete current geometry→surface→mesh→thermal→run→result→GCI chain, builds `field_dxf_acceptance.v1` through the production field acceptance builder, and confirms the independent field validator returns `ok=True`. It then passes the DXF itself as `output_path` and requires a source-artifact rejection with byte-for-byte preservation.

`_field()` now reads the supplied manifest, safely resolves every top-level path-bearing artifact record with the same contained, non-reparse root-relative resolver used by evidence references, and records each safe real file before calling `field_acceptance.validate_evidence()`. This includes `source_dxf` and the geometry/surface/mesh/run/result records. Invalid evidence still receives this pre-validation protection when it contains safe records. Absolute, traversal, backslash, missing, or reparse/escaping records resolve to `None` and are never added as safe sources.

Exact RED command and result:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_evidence.py -k "valid_field_evidence_source_dxf"
```

```text
1 failed, 52 deselected, 7 warnings in 2.95s
```

The failure was `DID NOT RAISE ValueError`, proving that publishing could replace the independently validated source DXF before the correction.

Exact targeted GREEN result:

```text
1 passed, 52 deselected, 7 warnings in 2.53s
```

The seven warnings are existing ezdxf/pyparsing deprecation warnings emitted while generating the real test DXF; they are not validation failures.

### Disposition B: preserve default zero-match GCI semantics

Schema-invalid candidates no longer block merely because they exist under the default `_body_gci` discovery root. With no schema-valid current match the grid check is `NOT_EVALUATED`; with exactly one schema-valid current match plus an unrelated malformed candidate, that match passes. An explicitly supplied canonical GCI root still treats any malformed candidate as `BLOCKED`/`GCI_SCHEMA_INVALID`, and the unsafe/reparse early return remains unconditional for both default and explicit authority.

Exact RED command and result:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_evidence.py -k "only_malformed_candidate"
```

```text
1 failed, 53 deselected in 1.08s
```

The failing status was `BLOCKED` instead of the binding `NOT_EVALUATED` zero-match result.

Exact targeted GREEN result:

```text
1 passed, 53 deselected in 0.87s
```

A combined round-2 boundary selection also exercised explicit-invalid and default unsafe-sibling controls:

```text
7 passed, 48 deselected, 7 warnings in 4.10s
```

### Round-2 complete verification

Complete Task 2 evidence suite:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_evidence.py
```

```text
55 passed, 7 warnings in 19.54s
```

Fresh prescribed focused suite:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_evidence.py tests/test_cfd_result_gate.py tests/test_field_pipeline_job.py
```

```text
........................................................................ [ 77%]
.....................                                                    [100%]
93 passed, 7 warnings in 23.52s
```

The authenticated compile and whitespace checks exited 0:

```powershell
& .venv-vv\Scripts\python.exe -B -m py_compile cfd_evidence.py tests/test_cfd_evidence.py
git diff --check
```

### Round-2 self-review and limitations

- The production change is twelve diff lines: safe pre-validation field artifact tracking plus an `explicit` guard on schema-invalid GCI blocking. No schema, public interface, result gate, field validator, or Task 3 behavior changed.
- The valid-field regression uses production builders and validators rather than mocking a PASS. It verifies the source bytes remain unchanged after the rejected publish.
- Default malformed GCI candidates are ignored only for schema validity. Unsafe/reparse traversal is checked and blocked before candidate parsing, and explicitly supplied invalid/stale/wrong-case evidence remains fail-closed.
- Only `cfd_evidence.py` and `tests/test_cfd_evidence.py` are intended for the round-2 commit. The appended report is under the existing ignored SDD report tree.
- No full-suite, CAD, solver, live field pipeline, or real GCI execution result is claimed.

## Review fix round 3 (2026-08-24)

### Critical legacy field-path overwrite disposition

The remaining bypass came from a deliberate contract difference: Case Evidence references accept only normalized projects-root-relative POSIX paths, while the legacy field validator resolves native Windows absolute paths and backslash-relative paths. The round-2 source inventory used the strict Case Evidence resolver, so a legacy spelling could be opened by `field_acceptance.validate_evidence()` and still remain absent from output protection.

This round adds `_resolve_field_record_for_tracking()`, a tracking-only adapter used exclusively while inventorying supplied field artifact records. It mirrors the legacy spelling rules sufficiently to form an absolute candidate, then delegates to `_safe_existing()`. Therefore a file is tracked only when it exists, resolves beneath `projects_root`, and has no symlink/reparse component. Absolute outside paths, traversal escapes, missing files, and reparse paths remain untracked and cannot become authority. `_resolve_ref()` and all stored Case Evidence reference rules are unchanged.

The parameterized regression first builds and independently validates a real field-evidence chain, then rewrites only `artifacts.source_dxf.path` as either a native contained absolute Windows path or a contained backslash-relative path. The legacy validator demonstrably opens the path and rejects the manifest later because its recomputed canonical record differs. Publishing to that DXF must nevertheless reject the source path before staging and preserve its exact bytes.

### RED and GREEN evidence

Targeted command:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_evidence.py -k "legacy_field_source_path_spelling"
```

Exact RED result before the tracking-only resolver:

```text
2 failed, 55 deselected, 7 warnings in 3.67s
```

Both native-absolute and backslash-relative cases failed with `DID NOT RAISE ValueError`, proving each source could be replaced.

Exact targeted GREEN result:

```text
2 passed, 55 deselected, 7 warnings in 3.31s
```

The warnings are the same ezdxf/pyparsing deprecation warnings from constructing the real DXF fixture.

### Final round-3 verification

Complete Task 2 evidence suite:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_evidence.py
```

```text
57 passed, 7 warnings in 20.31s
```

Prescribed focused suite:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_evidence.py tests/test_cfd_result_gate.py tests/test_field_pipeline_job.py
```

```text
........................................................................ [ 75%]
.......................                                                  [100%]
95 passed, 7 warnings in 21.07s
```

Authenticated compile and whitespace checks exited 0:

```powershell
& .venv-vv\Scripts\python.exe -B -m py_compile cfd_evidence.py tests/test_cfd_evidence.py
git diff --check
```

### Round-3 self-review and limitations

- The production diff is confined to one private tracking-only resolver and replacing one inventory call. General artifact resolution, stored references, validators, and schemas are untouched.
- Both regressions use the public build interface, production field builder/validator, real contained filesystem artifacts, and byte preservation assertions; no PASS result is mocked.
- The resolver cannot promote an outside or reparse path because `_safe_existing()` performs lexical root containment, strict resolution, file-type validation, resolved containment, and reparse-chain rejection before returning a path.
- Exactly `cfd_evidence.py` and `tests/test_cfd_evidence.py` are intended for the commit; this report remains in the existing ignored SDD tree.
- No repository-wide suite, CAD/solver execution, live field run, or real GCI study is claimed.

## Critical closeout correction (2026-08-24)

The tracking-only legacy field resolver now mirrors the legacy validator's value coercion exactly at its path-construction boundary: `Path(str(value or ""))`. A truthy non-string JSON value such as `123` can therefore protect the real contained file `projects_root/123` before `field_acceptance.validate_evidence()` reads it. The candidate still must pass `_safe_existing()`, so root containment, real-file type, strict resolution, and non-reparse requirements remain mandatory. General `_resolve_ref()`, stored Case Evidence references, and every non-field authority path remain strict and unchanged.

The regression copies a real DXF to `projects_root/123`, changes only the supplied field artifact record to numeric JSON `123`, confirms the legacy field evidence is rejected later, and requires `output_path=projects_root/123` to raise before publication while preserving the DXF bytes.

Targeted RED command and result:

```powershell
& .venv-vv\Scripts\python.exe -B -m pytest -q tests/test_cfd_evidence.py -k "truthy_non_string"
```

```text
1 failed, 57 deselected, 7 warnings in 3.36s
```

The failure was `DID NOT RAISE ValueError`. After changing only the tracking helper's coercion, targeted GREEN was:

```text
1 passed, 57 deselected, 7 warnings in 2.83s
```

Complete evidence suite:

```text
58 passed, 7 warnings in 25.67s
```

Fresh prescribed focused suite:

```text
96 passed, 7 warnings in 28.63s
```

The authenticated `py_compile` command and `git diff --check` exited 0. The seven warnings remain ezdxf/pyparsing deprecation warnings from generating the real DXF fixture. Exactly `cfd_evidence.py` and `tests/test_cfd_evidence.py` are intended for the commit; no full repository suite or solver/CAD run is claimed.

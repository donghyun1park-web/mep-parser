### Task 2: authoritative Case Evidence 재계산기를 만든다

**Files:**

- Create: `cfd_evidence.py`
- Create: `tests/test_cfd_evidence.py`
- Modify: `cfd_result_gate.py` only to expose stable public artifact-validation helpers; do not weaken existing gates.
- Test: `tests/test_cfd_result_gate.py`

**Interfaces:**

```python
def build_case_evidence(
    case_dir: Path,
    *,
    projects_root: Path,
    gci_root: Path | None = None,
    field_evidence_path: Path | None = None,
    output_path: Path | None = None,
) -> dict: ...

def validate_case_evidence(
    evidence_path: Path,
    *,
    projects_root: Path,
) -> list[dict[str, str]]: ...
```

- [ ] **Step 1: forged/stale evidence의 실패 tests를 작성한다**

  ```python
  def test_self_declared_pass_is_not_evidence(tmp_path):
      case = make_complete_case(tmp_path)
      forged = case / "case_evidence.json"
      forged.write_text('{"status":"PASS"}', encoding="utf-8")
      result = build_case_evidence(case, projects_root=tmp_path)
      assert result["status"] != "PASS"

  def test_changed_result_manifest_invalidates_evidence(tmp_path):
      evidence = build_complete_evidence(tmp_path)
      mutate_file(Path(evidence["artifact_refs"]["result"]["path"]))
      assert any(x["code"] == "ARTIFACT_HASH_MISMATCH"
                 for x in validate_case_evidence(Path(evidence["path"]), projects_root=tmp_path))
  ```

- [ ] **Step 2: tests가 실패하는지 확인한다**

  Run: `& $Python -B -m pytest -q tests/test_cfd_evidence.py`

  Expected: FAIL because functions do not exist.

- [ ] **Step 3: authoritative artifact inventory를 구현한다**

  현재 `geometry.v2`, surface, mesh, run, thermal input/progress, result, numerical sensitivity, GCI, field evidence를 현재 disk에서 다시 열고 path containment, schema, SHA-256, cross-reference를 검증한다. `case_summary()` cache와 report HTML은 source evidence로 사용하지 않는다.

- [ ] **Step 4: 원자 publish를 구현한다**

  같은 parent의 staging file에 JSON을 쓰고 `os.replace()`한다. output 자체와 generated report는 다음 run의 source inventory에서 제외한다.

- [ ] **Step 5: tamper matrix를 통과시킨다**

  Test missing artifact, symlink/root escape, wrong contract, mismatched current hash, stale GCI case, copied benchmark manifest, modified geometry, caller-authored PASS.

- [ ] **Step 6: focused regression을 실행한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests/test_cfd_evidence.py tests/test_cfd_result_gate.py tests/test_field_pipeline_job.py
  ```

  Expected: PASS.

- [ ] **Step 7: commit한다**

  ```powershell
  git add cfd_evidence.py cfd_result_gate.py tests/test_cfd_evidence.py tests/test_cfd_result_gate.py
  git commit -m "feat: recompute immutable case evidence"
  ```

## Controller rulings and exact authority boundaries

These rulings are binding and incorporate the Task 1 contract decisions. Do not broaden Task 2 into a new solver or V&V study.

1. `projects_root` is the authority root. Resolve `case_dir` strictly beneath `projects_root/_body_solver`. Every new stored link is normalized POSIX/projects-root-relative and is re-resolved with symlink/reparse/root-containment checks before any read or hash.
2. Recompute in this dependency order: `geometry -> surface -> mesh -> thermal_input -> run -> thermal_progress -> result -> unique current GCI`; independently validate supplied field evidence against that exact selected chain. Caller status strings, `design_ready`, reports, cache, prior case evidence, and generated presentation assets are never source evidence.
3. Canonical artifact keys follow Task 1. Core geometry/surface/mesh/run/result must be current and valid for a screening build. A buoyant run additionally requires thermal input and standalone thermal progress. Result refs must bind current run/mesh/thermal input and all declared VTU/summary/slices. Preserve existing numerical-provenance semantic checks.
4. No current numerical-sensitivity preparation/frozen-pair artifact is final authority. Do not populate `numerical_sensitivity` as PASS in Task 2. `numerics_verified` is derived only from current run/system numerical provenance. Future final sensitivity authority is versioned by its owning task.
5. `thermal_progress.json` is the canonical progress artifact. The embedded `run_manifest.thermal_progress` must be deep/canonical-JSON equal to it; mismatch is `BLOCKED`, even if either copy claims PASS.
6. Geometry may live anywhere under `projects_root` as a `.json` only if it is not under generated/evidence/cache/temp/report namespaces, validates as `geometry.v2`, is independently selected, and is bound by the current surface source path/hash. Do not infer authority solely from the surface's path string.
7. Legacy identity is recomputed, never copied. Set `case_id` to `legacy-` plus the first 20 lowercase hex characters of SHA-256 over canonical JSON containing `geometry_path`, `geometry_sha256`, `run_manifest_path`, and `run_manifest_sha256`; this is stable under projects-root relocation and distinguishes solver paths. Legacy evidence remains `purpose=screening` and cannot be design-citable.
8. GCI selection is deterministic and fail-closed: exactly one schema-valid manifest under the permitted GCI root whose selected-case path and current run/result/mesh/thermal hashes match may be used. Zero matches => `grid_verified=NOT_EVALUATED`; more than one => `grid_verified=BLOCKED` with `AMBIGUOUS_GCI_EVIDENCE`. Never choose first glob/PASS.
9. For profile-gated GCI, benchmark, or field evidence: absence => that source check is `NOT_EVALUATED`; a supplied ambiguous, unreadable, stale, tampered, wrong-root, or wrong-case artifact => `BLOCKED`. Missing/unsafe core artifacts => `BLOCKED`. Purpose profiles then control citation; Task 2 does not promote citation status.
10. Default output is `case_dir/case_evidence.v1.json`. Atomic same-parent replacement is allowed and must invalidate any review bound to the prior bytes; callers needing history may provide a distinct safe root-contained output path. Exclude the selected output, staging sibling, all prior case evidence, reports/caches, and `_release_evidence/**` from discovery except an explicitly supplied independently validated field evidence file.
11. Use an exact allow-list, not recursive project globbing. Same-parent atomic publish must write/flush/fsync/replace and clean staging on every exception.
12. If factoring `cfd_result_gate.py`, expose only narrow read-only helpers that retain existing result semantic and four-hash GCI checks. Do not accept caller-provided PASS/hashes or move Task 2 path/schema authority into an implicit permissive boolean.
13. Mandatory tests include every mutation in `task-2-evidence-precheck.md`, canonical progress mismatch, zero/multiple GCI selection, legacy-ID determinism across relocated roots, self-output exclusion, and repeat validation after source mutation.

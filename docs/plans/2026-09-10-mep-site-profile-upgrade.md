# Site-profile MEP upgrade design and execution record

Goal: transfer the proven DXF path/outline, alignment, elevation and native artifact techniques into the existing persistent GUI application. Preserve the geometry.json contract and architecture behavior; add deterministic Blender output and Codex declarative proposals. Do not generalize the reference site's dimensions or infer design approval.

Architecture: DXF → unit/layer/region inventory → source-hash-bound MEP profile → evaluated source curves and endpoint graph or closed outline polygonization → geometry.json → persistent preview and immutable native build snapshots → artifact verification receipt. ProjectSession validates candidates before CAS commit. Codex reads/inventories/diagnoses/proposes; the same GUI applies the concrete reviewed settings.

Interfaces: `inspect_mep_source(path)`, `validate_profile(profile, source_sha256)`, `parse(..., mep_profile=None)`, `ProjectSession.configure_source`, `propose_mep_profile`, `build_blender(geometry_path, out_dir, blender_path=None, timeout=900)`. Geometry dimensions and axis placement are centralized in `geom_contract` with injected JavaScript counterparts. Footprint geometry has height and actual polygon, no inferred centerline width/length.

Execution sequence:

1. Preserve original d9f80ef baseline and private/untracked files; develop in isolated codex/mep-profile-upgrade clone.
2. Reproduce path-join, raw spline controls, dimension override and preflight gaps with failing tests; implement curve graph/profile and common dimension contract.
3. Implement GUI site setup, project-persistent profiles, stale-proposal/CAS protection and sampled continuous preview.
4. Add deterministic Blender/GLB builder, source-only floor solids, preflight plus native reopen and GLB actual data verification; update FreeCAD and quantity consumers.
5. Document techniques and add project skill/MCP configuration; run full regression, real DXF comparison and separate native fixtures.
6. Review changed-file inventory and baseline hashes, back up target files, install only owned changes into the requested target; verify target hashes and entry points. Leave unrelated files intact.

Acceptance evidence belongs in `upgrade_verification.md` and exported receipts. Baseline suite: 281 passed /22 skipped /1 pre-existing frontend manifest hash failure. Final status is recorded only after current code and actual target verification; skipped native suites remain skipped.

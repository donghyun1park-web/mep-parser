# MEP CFD RACI availability record — 2026-08-25

## Record scope and vocabulary

- **Record date:** 2026-08-25
- **Plan scope:** `docs/superpowers/plans/2026-08-24-mep-cfd-master-development.md`, §11 RACI and the Task 0 / M0 gates.
- **Branch scope:** `codex/case-evidence-review-gate`.
- **Status vocabulary:** `AVAILABLE_NOW`, `AVAILABLE_FROM`, and `BLOCKED_NO_OWNER`.

An unavailable required role is `BLOCKED_NO_OWNER`; it is never converted into an assumed person, a product owner, or an approval. `UNASSIGNED` means no stable owner ID and no confirmed availability date/window have been evidenced. A slash-delimited plan role is recorded below as its separate capabilities: one available capability does not silently satisfy another.

The only currently evidenced capability is the development-agent capability:

- **Role:** Development agent capability
- **Stable owner ID:** `codex-agent`
- **Availability:** `2026-08-25 / current development session`
- **Status:** `AVAILABLE_NOW`
- **Limit:** permits scoped implementation work only. It does not provide human approval, product ownership, MEP confirmation, scientific-threshold approval, field validation, or release sign-off.

No human, GitHub user, or repository owner has been assigned from inference. In particular, `donghyun1park-web` is not treated as the owner of any role in this record.

## §11 decision availability matrix

For every assignment, the cell states `Role`, `Owner ID`, `Availability`, and `Status`. `No confirmed date/window` is an explicit unavailable window, not a proposed date.

| Decision | Responsible | Accountable | Consulted | Informed |
|---|---|---|---|---|
| Domain/schema/code | Role: Developer capability (from `Developer/agent`)<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER`<br><br>Role: Development agent capability (from `Developer/agent`)<br>Owner ID: `codex-agent`<br>Availability: `2026-08-25 / current development session`<br>Status: `AVAILABLE_NOW` | Role: Tech lead<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: CFD reviewer<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: Product owner<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` |
| Numerical threshold/GCI | Role: CFD V&V engineer<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: CFD lead<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: Developer<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: Product owner<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` |
| Terminal/heat/BC input | Role: MEP engineer<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: Project MEP lead<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: TAB capability (from `TAB/CFD`)<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER`<br><br>Role: CFD capability (from `TAB/CFD`)<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: Operator<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` |
| Field measurement/uncertainty | Role: TAB capability (from `TAB/field engineer`)<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER`<br><br>Role: Field engineer capability (from `TAB/field engineer`)<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: Project MEP lead<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: CFD reviewer<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: Developer<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` |
| Comfort/IAQ scope | Role: Building-physics capability (from `Building physics/IAQ reviewer`)<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER`<br><br>Role: IAQ reviewer capability (from `Building physics/IAQ reviewer`)<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: Project MEP lead<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: CFD capability (from `CFD/Product`)<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER`<br><br>Role: Product capability (from `CFD/Product`)<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: Users<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` |
| Release RC/package | Role: Developer capability (from `Developer/IT`)<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER`<br><br>Role: IT capability (from `Developer/IT`)<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: Product owner<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: CFD reviewer<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: Users<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` |
| UAT result | Role: Observer capability (from `Observer/Product`)<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER`<br><br>Role: Product capability (from `Observer/Product`)<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: Product owner<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: MEP users<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: Developer<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` |
| MPI/surrogate enablement | Role: Developer capability (from `Developer/CFD`)<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER`<br><br>Role: CFD capability (from `Developer/CFD`)<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: Tech lead<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: IT capability (from `IT/Product`)<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER`<br><br>Role: Product capability (from `IT/Product`)<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` | Role: Users<br>Owner ID: `UNASSIGNED`<br>Availability: No confirmed date/window<br>Status: `BLOCKED_NO_OWNER` |

## Gate impact

| Area | Availability outcome and critical-path effect |
|---|---|
| Task 5a — validators code | May proceed after the CI mechanism is reviewed because it changes no scientific threshold and can remain fail-closed. The available `codex-agent` development-agent capability permits scoped implementation only; it supplies neither a human approval nor a decision owner. |
| Task 4.5 — confirmed geometry; Task 5c — GUI E2E and reduced usability | `BLOCKED_NO_OWNER` and `BLOCKED_INPUT_CONFIRMATION` until an MEP engineer/project MEP lead and one confirmed geometry are available. |
| Task 5b — solver evidence | Conservatively blocked behind Task 4.5. The plan introduces Task 4.5 as a prerequisite for 5b/5c even though the later short gate sentence names only 5c. |
| M3; M4/M5/M6 external approval gates; M7; M8 | Excluded from the active critical path until their named owners exist. This prevents unstaffed scientific, field, release, UAT, MPI, and surrogate work from becoming an assumed schedule. |
| Active code critical path after this record | `first-green Windows CI → Task 5a` only. This does not close M1: confirmed geometry, current single-PC evidence, and the other M1 exit conditions remain independently required. |

## 2026-08-27 scope revision — user-authorized synthetic Task 5b execution

The user explicitly authorized moving to Task 5 and accepted the proposed
synthetic-evidence-only boundary. This revision permits Task 5b environment,
working-room, heat-box, and limited numerical evidence that does not consume or
claim confirmed site geometry. It does not appoint an MEP engineer, approve a
scientific threshold, or convert user statements into accountable MEP approval.

- Task 5b Step 1 may execute on the current PC and publish ignored local runtime
  evidence only after the pure validator returns PASS.
- Task 4.5 remains `BLOCKED_NO_OWNER` / `BLOCKED_INPUT_CONFIRMATION`.
- Task 5c and M1 Exit remain blocked behind one accountable confirmed geometry.
- Task 5b evidence remains synthetic, serial, non-citable, and non-release
  evidence; it cannot replace field inputs, external review, or M1 exit checks.
- This dated revision supersedes only the earlier conservative dependency of
  all Task 5b work on Task 4.5. All human-owner availability rows remain intact.

## Future owner activation

A future owner becomes active only through a new dated record or append-only revision that states the stable owner/reviewer ID, the role, availability date/window, scope, and supporting evidence/review record. A personal display name is never hard-coded into product code. Until that update is reviewed, this record remains authoritative for availability and the affected work remains blocked.

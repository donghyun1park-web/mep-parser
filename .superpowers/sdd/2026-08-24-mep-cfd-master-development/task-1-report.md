# Task 1 report — Case Evidence status contracts

## Scope and commit

- Commit: `7669d355d9139a57327b51e35724d72ea274408a` — `feat: define case evidence status contracts`
- Staged and committed exactly: `cfd_status_catalog.py`, the three v1 schemas, and the two Task 1 test files.
- No Task 0 or later-task producer/consumer file was changed.  This task adds structural vocabulary and schema contracts only; it does not recompute evidence, evaluate health, persist reviews, or expose HTTP/UI behavior.

## RED evidence

The prescribed non-elevated authenticated-venv command could not start in the workspace sandbox:

```text
No Python at '"C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe'
```

Using the available Python 3.14 only to witness the intended missing-feature RED then produced:

```text
41 failed in 1.42s
```

The failures were the expected `ModuleNotFoundError: No module named 'cfd_status_catalog'` and missing `case_evidence.v1.schema.json`, `case_health.v1.schema.json`, and `case_review.v1.schema.json` files.

## GREEN evidence

The authenticated elevated `.venv-vv\Scripts\python.exe` focused command was run after implementation:

```text
............................................                             [100%]
44 passed in 1.00s
```

Command:

```powershell
& .venv-vv/Scripts/python.exe -B -m pytest -q tests/test_cfd_status_catalog.py tests/test_case_evidence_schema.py
```

`git diff --cached --check` was clean.  The three JSON schemas also parsed with the authenticated interpreter.

## Full-suite evidence and limitation

The authenticated full suite was started twice (the second with `--disable-warnings --tb=short`) before the harness's 30-second outer streaming boundary.  Both child pytest processes continued and subsequently exited; no JUnit or test-output file exists in the checkout, and the harness did not retain their final exit code/summary after the parent stream returned.

Captured exact partial outputs were:

```text
........................................................................ [ 10%]
........................................................................ [ 20%]
...................................................ssss................. [ 31%]
................................................ss...................... [ 41%]
........
```

and:

```text
........................................................................ [ 10%]
........................................................................ [ 20%]
...................................................ssss................. [ 31%]
..........................
```

Therefore no full-suite PASS claim is made from these runs.  The parent controller will independently rerun and capture the complete authenticated full-suite result.

## Contract self-review

- Exact four evidence and citation states, nine fixed health IDs, four closed purpose profiles, and the eight ordered decision rows are catalog authority and schema-tested.
- Evidence checks are fixed ordered `prefixItems`; health checks are a closed nine-key object.  Links use root-relative normalized POSIX paths and strict lowercase SHA-256; artifact refs use the required core keys and an allow-list of profile-gated keys.
- Evidence identity is exclusive current `case_identity` vs closed legacy bridge.  Legacy artifacts require `screening`; a legacy health artifact cannot claim `DESIGN_CITABLE`.
- Health embeds the exact catalog decision-table version/literal.  Reviews target only closed `case_evidence.v1` links and support unique `supersedes_review_ids`.
- Negative coverage rejects paths, hashes, extras, duplicate check/reason/evidence-ref IDs, identity misuse, review-target omissions, and decision-table drift.  The mandatory review/evidence catalog codes have Korean impact and action text.

## Review fix round 1 — optional initial-review supersession list

Reviewer finding: `supersedes_review_ids` is optional under the Task 1 lifecycle ruling, but the initial schema made it a root-required field.  The correction removes only that name from the root `required` list; its declared unique string-array schema remains unchanged when the field is present.

### RED

Test added first: `test_case_review_schema_accepts_initial_review_without_supersedes` removes `supersedes_review_ids` from an otherwise valid initial review.

```text
1 failed, 33 deselected in 0.72s
```

The expected validator error was:

```text
'supersedes_review_ids' is a required property
```

### GREEN

After the one-field schema correction, the authenticated focused command was:

```powershell
& .venv-vv/Scripts/python.exe -B -m pytest -q tests/test_cfd_status_catalog.py tests/test_case_evidence_schema.py
```

with exact output:

```text
.............................................                            [100%]
45 passed in 2.33s
```

Self-review showed only `case_review.v1.schema.json` and `tests/test_case_evidence_schema.py` changed; `git diff --check` was clean.  A pre-existing ignored `.test-studio-34mrmnw6/` runtime artifact was not modified or staged.

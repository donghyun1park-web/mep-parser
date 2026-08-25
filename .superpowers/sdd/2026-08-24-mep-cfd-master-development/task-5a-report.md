# Task 5a — Code-owned WORKING_SINGLE_PC validators report

## Outcome

Task 5a is code-complete. The fixed `working_validation.v1` eight-check order,
state labels, scope, `design_citable=false`, and `release_ready=false` were
preserved. Six concrete validators now replace the future-placeholder path:

- serial environment and fixed 64-cell/runtime evidence;
- working-room anchor/repeat evidence;
- SGI screening and restart integrity;
- exact heat-box verification;
- bounded scheme/time/mesh numerical spot checks.

This milestone added schemas, pure revalidators, immutable evidence checks,
synthetic/tamper fixtures, and aggregate dispatch only. It did **not** run a
solver, FreeCAD, Studio, browser, GUI DXF flow, or real-DXF acceptance. Missing
real producer evidence therefore remains `BLOCKED`; Tasks 5b and 5c own those
runtime artifacts.

## Integrated commits

The reviewed module commits integrated before the final aggregate closure are:

```text
1f953ef feat: validate serial environment evidence
bec844a fix: harden serial environment evidence
d5940b6 fix: bind serial runtime and manifest evidence
363fa01 fix: bind serial solver and case inputs
9921674 feat: validate working-room and SGI evidence
1a3228c fix: harden working-room acceptance evidence
596358d feat: validate heat and numerical evidence
```

The final aggregate commit `f3b7109386195ae665bd216cb689c686f23dea99`
adds output/evidence boundary hardening, the public eight-check dispatch,
plan/progress updates, and this report.

## Aggregate fail-closed closure

Independent review initially reproduced four release-blocking gaps. The final
implementation closes them as follows:

- malformed/unhashable validator status values become stable `BLOCKED` results;
- aggregate output is passed to every side-effect-free validator, while the
  serial validator retains its separate writer contract and its raw authority
  is protected by aggregate preflight;
- fixed producer namespaces are protected even when their manifests are
  missing, so an aggregate cannot be written into raw case trees;
- every evidence key must be canonical project-relative POSIX text, exact-case
  on disk, reparse-free, current, and non-cache/non-temp;
- regular-file evidence must have one hardlink; different keys cannot share a
  filesystem identity;
- all evidence is identity/digest rechecked after all validators, including the
  numerical directory-tree algorithm, before state emission;
- the writer rejects traversal, ADS, Windows-illegal/reserved names,
  cache/temp locations, reparse/non-regular leaves, and unsafe validator output
  blockers, and removes its temporary file if atomic replacement fails;
- saved payload validation rejects noncanonical evidence, PASS/blocker
  contradictions, missing PASS evidence, and duplicate JSON keys;
- two-run comparison requires different filesystem identities and binds each
  payload's `output_path` to the file actually compared.

The final independent verdict on the current bytes is `CLEAN`; no actionable
P1/P2 finding remains.

## Verification evidence

TDD captured the main aggregate boundary as RED before implementation:

```text
17 failed, 10 passed
```

Additional RED cycles covered missing-manifest producer-tree writes, exact-case
aliases, undeclared hardlinks, payload contradictions, unsafe Windows output
names, atomic cleanup, and same-run comparison bypasses. Final focused results:

```text
working_validation integration: 60 passed
heat + numerical validators:     49 passed
working + SGI + serial:          227 passed
capabilities + physics:          85 passed, 7 skipped, 9 subtests passed
focused aggregate total:         421 passed, 7 skipped, 9 subtests passed
```

The seven focused skips are explicit runtime gates and are not solver/CAD PASS
evidence. Current-file `py_compile` and `git diff --check` also pass.

The full local suite under the only currently callable interpreter, Python
3.14.3, produced:

```text
1177 passed, 2 failed, 14 skipped, 7 warnings, 115 subtests passed
```

Both failures are the same toolchain-authentication precondition in
`tests/test_dependency_lock.py`: the running Python 3.14 executable SHA-256 is
not the pinned Python 3.12.10 executable SHA-256. This is intentionally **not**
reported as a green full suite. The public Windows CI uses the pinned 3.12.10
bootstrap and is the authoritative full-suite gate for the published commit.

That exact published code commit passed public Windows CI [run
32822053903](https://github.com/donghyun1park-web/mep-parser/actions/runs/32822053903),
job `97722069826`. Direct inspection of uploaded JUnit artifact `9553586783`
reported:

```text
1193 tests = 1179 passed + 14 skipped
0 failures, 0 errors
217.691 seconds
```

The artifact digest is
`sha256:1be289869b9ff4a78944997c7f1755361d6be334551045b5607ff5ae678d64d9`.
This closes the code-only full-suite gate; it is not solver or GUI runtime
evidence.

## Remaining boundary

- Task 5b must generate genuine serial, working-room, heat-box, and numerical
  raw solver evidence, including case-local `T/U/phi/yPlus` and execution
  provenance.
- The six-decimal copied-result fingerprint remains a bounded near-copy
  heuristic, not independent execution attestation.
- Task 5c still requires confirmed real-DXF GUI, shutdown/resume, restart, and
  usability evidence plus the named human review roles.
- General release and M1 remain `NO-GO`; no Task 5a code test changes that
  decision.

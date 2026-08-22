# Task 12 Report: atomic evidence-file reads

## Outcome

All acceptance evidence reads now bind the audit and the read to one file descriptor. Both ordinary retained `Path` packs and fd-rooted candidate packs:

- inspect the entry without following links;
- require the audited entry to be a regular file;
- open with `O_NOFOLLOW | O_NONBLOCK`;
- `fstat` the opened descriptor and require a regular file with the same device/inode as the audited metadata;
- hash or read only from that descriptor; and
- close the descriptor on success, identity/type failure, open failure, and read failure.

A regular file replaced after `lstat` with a FIFO therefore cannot block, and a replacement symlink cannot be followed. A same-name replacement regular file also fails the device/inode comparison. The behavior is shared by evidence enumeration, JSON loading, PNG validation/metrics, screenshot provenance, receiver reservation fingerprints, capture/final attestation hashes, retained anchor verification, and candidate-anchor promotion.

Candidate finalization preserves its existing `finally`-based key destruction. The new race regression proves an fd-rooted FIFO replacement raises before signing, leaves neither final attestation nor candidate anchor, destroys the ephemeral private key, and lets workspace cleanup remove both the held staging directory and empty requested candidate entry without touching the outside marker.

No evidence schema, signed anchor contract, Make target, or operational workflow changed.

## Root cause

Task 11 correctly rejected static symlinks and special objects during tree enumeration, but `_safe_evidence_files` separated the regular-file `lstat` from `path.read_bytes()`. A replacement in that interval invalidated the audit:

- ordinary `Path.read_bytes()` followed a replacement symlink;
- fd-rooted reads rejected a final symlink but did not normalize the failure;
- both ordinary and fd-rooted blocking `O_RDONLY` opens waited indefinitely when the replacement was a FIFO; and
- a candidate finalization blocked while its ephemeral signing key and private staging workspace remained live.

The defect was a check/use split, not a missing static type check. The fix therefore makes the audited metadata an explicit input to the descriptor open and verifies identity after open, rather than adding another pathname check.

## TDD evidence

Observed RED before production changes (`5 failed, 100 deselected`):

- retained Path + FIFO reached the one-second SIGALRM deadline while blocked in `Path.read_bytes()`;
- retained Path + symlink completed without raising, proving it read the outside target;
- fd-rooted + FIFO reached the same deadline while blocked in `os.open`;
- fd-rooted + symlink raised raw `OSError: Too many levels of symbolic links` instead of the shared fail-closed contract; and
- candidate finalization + FIFO reached the deadline inside `_attestation_files`.

Observed GREEN after the minimal audited-fd implementation:

- four retained/fd-rooted FIFO/symlink races plus candidate key/staging cleanup: 5 passed;
- those races plus the six existing static unsafe-entry regressions: 11 passed, 94 deselected;
- complete visual-twin suite: 105 passed.

The tests replace the victim immediately after returning its original regular-file metadata. SIGALRM is only a test guard: a passing implementation completes well before it and raises `ValueError`; the guard prevents a vulnerable implementation from hanging pytest.

## Verification

- `PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q tests/e2e/test_robocasa_visual_twin.py`: 105 passed in 43.59 s.
- `make robocasa-acceptance`: pinned round3 revalidated successfully without starting a stack.
- `go test ./...`: all Go packages passed.
- `cd web && npm ci && npm test`: 97 passed. The initial dependency-free run failed only because `three` was absent; the locked install resolved all five module-load failures. Generated `node_modules` was moved to `/tmp/tangying-task12-node-modules.LZByZ8/node_modules` afterward.
- Python compile check for runner and visual-twin tests: passed.
- `git diff --check`: passed.

## Scope

Changes are limited to `scripts/run_robocasa_harness.py`, `tests/e2e/test_robocasa_visual_twin.py`, and this report. Pinned round3 artifacts, trusted anchor bytes, signed schemas, Go production code, and Web production code are unchanged.

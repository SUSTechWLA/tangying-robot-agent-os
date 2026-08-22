# Task 12 Report: atomic evidence-file reads

## Outcome

All acceptance evidence reads now bind the audit and the read to one file descriptor. Both ordinary retained `Path` packs and fd-rooted candidate packs:

- hold the canonical evidence root (or direct file parent) and traverse every component with `dir_fd`, `O_DIRECTORY`, and `O_NOFOLLOW`;
- inspect the entry without following links;
- require the audited entry to be a regular file;
- open with `O_NOFOLLOW | O_NONBLOCK`;
- `fstat` the opened descriptor and require a regular file with the same device/inode as the audited metadata;
- hash or read only from that descriptor; and
- close the descriptor on success, identity/type failure, open failure, and read failure.

A regular file replaced after `lstat` with a FIFO therefore cannot block, and a replacement symlink cannot be followed. A same-name replacement regular file also fails the device/inode comparison. The behavior is shared by evidence enumeration, JSON loading, PNG validation/metrics, screenshot provenance, receiver reservation fingerprints, capture/final attestation hashes, retained anchor verification, and candidate-anchor promotion.

The ordinary-Path implementation fixes the canonical path before auditing, which preserves legitimate trusted system links such as macOS `/var -> /private/var`. It then holds and reopens the canonical root/parent through no-follow component traversal. Replacing any audited descendant's ancestor with a symlink to the moved original inode therefore fails closed even though the file device/inode itself still matches.

Candidate finalization preserves its existing `finally`-based key destruction. The new race regression proves an fd-rooted FIFO replacement raises before signing, leaves neither final attestation nor candidate anchor, destroys the ephemeral private key, and lets workspace cleanup remove both the held staging directory and empty requested candidate entry without touching the outside marker.

No evidence schema, signed anchor contract, Make target, or operational workflow changed.

## Root cause

Task 11 correctly rejected static symlinks and special objects during tree enumeration, but `_safe_evidence_files` separated the regular-file `lstat` from `path.read_bytes()`. A replacement in that interval invalidated the audit:

- ordinary `Path.read_bytes()` followed a replacement symlink;
- fd-rooted reads rejected a final symlink but did not normalize the failure;
- both ordinary and fd-rooted blocking `O_RDONLY` opens waited indefinitely when the replacement was a FIFO; and
- a candidate finalization blocked while its ephemeral signing key and private staging workspace remained live.

The defect was a check/use split, not a missing static type check. The fix therefore makes the audited metadata an explicit input to the descriptor open and verifies identity after open, rather than adding another pathname check.

Review round 1 found two remaining forms of the same ownership defect. First, ordinary reads opened a full pathname, so `O_NOFOLLOW` protected only the final component; moving an audited ancestor outside the root and replacing it with a symlink to that moved directory preserved the victim file's device/inode and let the read escape. Second, `os.fdopen` was invoked directly in three owned-fd paths; if stream conversion itself raised, no stream existed to close the descriptor. Ordinary retained roots/direct parents are now held and revalidated component-by-component, and `_fdopen_owned` explicitly closes on conversion failure.

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

Review round 1 RED and GREEN:

- RED: retained enumeration, direct read, direct hash, and promotion all accepted/followed an audited ancestor moved outside the root; read/hash/fd-rooted-read each leaked one descriptor when `os.fdopen` was fault-injected (`7 failed, 105 deselected`);
- GREEN: all seven focused ancestor/descriptor regressions passed, and the combined FIFO/symlink/special-object/ancestor/fdopen selection passed 18 tests;
- a first full-suite run exposed that temporary audit paths use macOS `/var`; canonicalizing the trusted path once before no-follow traversal restored the Task 11 trusted-system-link contract while preserving post-audit ancestor detection;
- complete visual-twin suite after that compatibility fix: 112 passed.

The tests replace the victim immediately after returning its original regular-file metadata. SIGALRM is only a test guard: a passing implementation completes well before it and raises `ValueError`; the guard prevents a vulnerable implementation from hanging pytest.

## Verification

- `PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q tests/e2e/test_robocasa_visual_twin.py`: 112 passed in 44.18 s.
- `make robocasa-acceptance`: pinned round3 revalidated successfully without starting a stack.
- `go test ./...`: all Go packages passed.
- `cd web && npm test`: 97 passed using the parked locked dependency tree; the temporary worktree symlink was removed afterward. The initial Task 12 dependency-free run had failed only because `three` was absent, then passed after `npm ci`.
- Python compile check for runner and visual-twin tests: passed.
- `git diff --check`: passed.

## Scope

Changes are limited to `scripts/run_robocasa_harness.py`, `tests/e2e/test_robocasa_visual_twin.py`, and this report. Pinned round3 artifacts, trusted anchor bytes, signed schemas, Go production code, and Web production code are unchanged.

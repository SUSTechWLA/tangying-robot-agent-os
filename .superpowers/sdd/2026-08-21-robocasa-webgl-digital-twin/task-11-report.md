# Task 11 Report: candidate directory-handle integrity

## Outcome

Candidate generation no longer writes through the user-visible candidate path. The runner now:

- canonicalizes and fixes the trusted artifacts root first, so a legitimate trusted parent symlink such as macOS `/var -> /private/var` is accepted;
- applies `O_NOFOLLOW` traversal only to user-controlled components below that canonical root;
- creates an empty 0700 candidate entry and a random 0700 private staging directory directly below the trusted root;
- holds directory descriptors and device/inode identities for the candidate parent, candidate entry, staging parent, and staging entry for the full run;
- reopens and revalidates the requested parent plus candidate/staging identities at receiver and runner boundaries;
- routes every staging read, atomic write, chmod, unlink, mkdir, and recursive enumeration through the held staging descriptor using `openat`-style `dir_fd` operations and `O_NOFOLLOW`; no staging pathname is used for evidence I/O;
- encodes screenshots in memory before writing their PNG bytes through the fd-rooted workspace;
- publishes staging only after final signature validation and identity revalidation, then atomically renames it over the still-empty candidate entry;
- clears the held staging directory relative to its descriptor, then removes a renamed staging entry only when its device/inode matches;
- returns an fd-rooted published handle, so a candidate pathname replacement after the final check cannot redirect the returned content.

If the prepared candidate entry is removed and replaced with a symlink to either an outside directory or pinned `round3`, receiver and runner writes remain in private staging and publication fails closed. Neither target receives a capture session or evidence file.

The runner prints the random private staging `capture-session.json` path after task binding. README and the RoboCasa handoff operations guide now instruct the uploader to use that printed path.

## Root cause

Task 10 held and revalidated only the candidate parent. After `mkdir(candidate)`, the candidate entry itself was not opened or identified. Replacing that empty entry with a symlink left the parent device/inode unchanged, and later ordinary `Path` writes followed the replacement. The no-follow helper also began at `/`, incorrectly treating a trusted system symlink above the artifacts root as user-controlled input.

Review round 1 found that the first Task 11 implementation still used the held staging fd only as an identity probe. A deterministic swap immediately after `assert_integrity()` redirected `_write_session()` through the mutable staging pathname; `stop()` then deleted the outside target file. The same check/use/check window covered screenshots, runner evidence, summary, envelope, and attestation. Publication also returned the mutable candidate `Path`, so a replacement after its last identity check changed what the caller read.

## TDD evidence

Observed RED before production changes:

- the new workspace interface did not exist, so candidate replacement regressions failed before safe staging was available;
- the trusted-root regression failed with `candidate path contains a symlink component: var`;
- four focused cases failed (`4 failed, 86 deselected`).

Observed GREEN after the minimal staging/identity implementation:

- candidate replacement to outside and pinned round3, trusted-root parent symlink, below-root symlink rejection, stale cleanup, and parent-swap focus: 7 passed;
- Task 10 receiver/candidate/promotion/retained trust-boundary focus: 25 passed;
- complete visual-twin suite: 90 passed.

Review round 1 RED and GREEN:

- RED: staging-entry replacement after the precheck overwrote and then deleted `outside/capture-session.json` and `round3/capture-session.json`; post-check candidate replacement made the returned publication path resolve outside (`3 failed, 90 deselected`);
- GREEN: outside/round3 session markers remain byte-identical, the full receiver screenshot -> summary -> envelope -> attestation chain stays on the held fd, renamed staging is scrubbed, the private key is destroyed, and publication returns the held content handle (`4 passed`);
- complete visual-twin suite after fd-rooting: 94 passed.

## Verification

- `PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q tests/e2e/test_robocasa_visual_twin.py`: 94 passed in 42.19 s after review round 1.
- `make robocasa-acceptance`: pinned round3 revalidated successfully without starting a stack.
- `go test ./...`: all Go packages passed.
- `cd web && npm ci && npm test`: 97 passed; generated `node_modules` was moved out of the worktree afterward.
- Python compile check for runner, uploader, and visual-twin test: passed.
- `git diff --check`: passed.

## Scope

Changes are limited to the RoboCasa acceptance runner, visual-twin regressions, the candidate-session operations documentation, and this report. Pinned round3 artifacts, its trusted anchor, signing schemas, uploader authentication, Go production code, and Web production code are unchanged.

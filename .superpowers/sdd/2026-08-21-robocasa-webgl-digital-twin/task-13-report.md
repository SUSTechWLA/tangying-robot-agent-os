# Task 13 Report: deterministic evidence-fd ownership

## Outcome

Evidence reads and hashes no longer transfer a verified descriptor into
`os.fdopen`. A single `_read_owned_descriptor` owner now consumes the audited
descriptor with an `os.read` loop and closes it in `finally` on success or read
failure. Byte reads join the chunks only after the descriptor is closed; text
reads decode those bytes afterward; SHA-256 updates directly from chunks read
from that same verified descriptor.

The former duplicate guard, `_fdopen_owned`, `_same_open_description`, and all
mutable seek-offset identity probing are deleted. Consequently evidence reads
cannot reach the old `dup()`/EMFILE conversion path and cannot close an
unrelated descriptor whose reused number happens to sit at either old probe
offset. `FDRootedPath.open()` preserves its read-only interface with an
in-memory binary/text stream created after the audited descriptor is closed.

Task 12 held-root semantics are unchanged: retained validation and candidate
promotion still enumerate and perform every semantic/signature read relative
to one held root descriptor, then revalidate root identity before exit.

## Root cause

`os.fdopen` is an ownership-transfer API whose failure contract cannot reveal
whether it consumed the numeric descriptor before raising. Task 12 tried to
recover that missing ownership fact by duplicating the descriptor and probing
shared seek offsets. That introduced two defects:

- `os.dup(file_fd)` could raise `EMFILE` before cleanup protected the owned
  descriptor, leaking it; and
- seek offsets are mutable file state, not open-file-description identity. A
  reused unrelated descriptor at the selected probe offset could be mistaken
  for the original and closed.

The deterministic fix removes the ownership transfer instead of attempting to
infer what happened after it failed.

## TDD evidence

RED was observed before production changes (`7 failed, 117 deselected`):

- direct read and hash raised injected `EMFILE` at `dup()` instead of completing
  and left the newly opened evidence descriptor outside a deterministic owner;
- direct read, hash, and fd-rooted read did not call the required `os.read`
  path, so the injected original read exception was not observed; and
- both former `(guard offset, probe offset)` pairs `(0, 1)` and `(1, 0)` entered
  the injected close/reuse `fdopen` path.

GREEN after the minimal ownership rewrite:

- the seven new regressions passed;
- the combined descriptor, ancestor-replacement, FIFO/symlink, decode-failure,
  and held-root selection passed 27 tests; and
- the complete visual-twin suite passed 120 tests.

The regressions prove that `dup()` resource exhaustion is no longer on an
evidence-read path, the exact injected `os.read` exception object remains
visible, every owned read descriptor is closed without leakage, unrelated
descriptors remain readable at both old probe offsets, and invalid text
encoding occurs only after the fd-rooted descriptor has closed.

Review round 1 found that an `os.close` failure in the descriptor owner's
`finally` block could still replace an already-active read or digest-update
exception. RED reproduced both double-failure cases while confirming that a
standalone close failure already propagated (`2 failed, 1 passed, 120
deselected`). The owner now records the active exception: a secondary close
failure is attached to that exception as a diagnostic note, while a close
failure with no active error is still raised unchanged. All three review
regressions passed, the expanded safety selection passed 30 tests, and the
complete visual-twin suite passed 123 tests.

## Verification

- `PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q tests/e2e/test_robocasa_visual_twin.py`: 123 passed in 45.93 s.
- `make robocasa-acceptance`: pinned round3 retained pack revalidated.
- `go test ./...`: all Go packages passed.
- `cd web && npm ci && npm test`: 97 passed. The initial dependency-free run
  failed only because `three` was absent; the generated `node_modules` tree was
  parked outside the worktree afterward.
- Python compile check for the runner and visual-twin tests: passed.
- `git diff --check`: passed.

## Scope

Changes are limited to `scripts/run_robocasa_harness.py`,
`tests/e2e/test_robocasa_visual_twin.py`, and this report. Signed schemas,
pinned round3 evidence, trusted anchor bytes, held-root validation/promotion,
Go production code, and Web production code are unchanged.

# Pinned XLeRobot compatibility fixtures

These unmodified source files are from Vector-Wangel/XLeRobot commit
`3d14695e40c9c68229c0aacffca6053c75cd3eb6`, under
`software/src/robots/xlerobot_2wheels/`. Original Apache 2.0 copyright and license
notices are retained. The `.txt` suffix keeps the original sources out of test
discovery, package installation, and our Python linter.

`test_driver.py` checks the source hash, extracts the real robot class AST, and
executes it against fake serial buses and camera construction. No LeRobot install,
serial device, or external checkout is required. Tests reproduce the original
key and configuration defects, then exercise our compatibility mixin with the
same class. These are software bus-contract tests, not hardware validation.

The expected source hashes live in `xlerobot_adapter/upstream_compat.py`. A future
upstream or LeRobot update requires an explicit review of the hardware contract,
new source fixtures, and regression verification; do not silently update hashes.

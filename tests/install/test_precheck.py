"""Keep the cold-start precheck honest.

The precheck decides whether a fresh machine is declared ready to install. A
check that misjudges is worse than no check: it sends someone to fix a machine
that was already fine, or waves through one that is not. The version comparator
below was written wrong once (it classified go1.26.2 as older than 1.26, and
Python 3.11.9 as "11.9"), so it now has its own regression test rather than
being taken on trust.

These tests read the script; they do not run an install.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRECHECK = REPO / "scripts" / "precheck.sh"


def _comparator_body() -> str:
    text = PRECHECK.read_text()
    match = re.search(r"^version_at_least\(\) \{.*?^\}", text, re.MULTILINE | re.DOTALL)
    assert match, "version_at_least is missing from scripts/precheck.sh"
    return match.group(0)


def _compare(found: str, want: str) -> bool:
    """Run the real comparator from the script in an isolated shell."""
    script = _comparator_body() + '\nversion_at_least "$1" "$2"\n'
    result = subprocess.run(
        ["bash", "-c", script, "vt", found, want],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def test_precheck_script_is_executable_and_syntax_clean():
    assert PRECHECK.exists(), "scripts/precheck.sh is missing"
    assert PRECHECK.stat().st_mode & 0o111, "scripts/precheck.sh is not executable"
    subprocess.run(["bash", "-n", str(PRECHECK)], check=True)


def test_version_comparator_accepts_sufficient_versions():
    for found, want in [
        ("go1.26.2", "1.26"),
        ("1.26.2", "1.26"),
        ("Python 3.11.9", "3.11"),
        ("v24.14.1", "18.0"),
        ("2.0.0", "1.99.99"),
        # A missing patch component counts as zero, so a bare "1.26" is not
        # older than "1.26.0". Getting this backwards would reject an exactly
        # supported toolchain.
        ("1.26", "1.26"),
        ("1.26.2-rc1", "1.26"),
    ]:
        assert _compare(found, want), f"{found!r} should satisfy >= {want!r}"


def test_version_comparator_rejects_insufficient_versions():
    for found, want in [
        ("go1.25.9", "1.26"),
        ("3.10.14", "3.11"),
        ("v16.0.0", "18.0"),
        ("Python 3", "3.11"),
        # Nothing parseable must never pass: an unreadable version is not a
        # reason to declare a machine ready.
        ("", "1.0"),
        ("unknown", "1.0"),
    ]:
        assert not _compare(found, want), f"{found!r} must not satisfy >= {want!r}"


def test_precheck_only_reads_the_machine():
    """A precheck that changes things cannot be run to find out.

    The check is for a mutating command in *command position*, not for the words
    appearing anywhere. The script legitimately prints install instructions to
    the operator ("macOS: brew install go") — that is advice, not an action, and
    a naive substring check flags it. What must not exist is the precheck
    running one itself: at the start of a line, or after a command separator or
    a command substitution.
    """
    mutating = (
        r"pip3? install",
        r"apt-get install",
        r"apt(-get)? (update|upgrade)",
        r"brew install",
        r"go install",
        r"npm (ci|install)",
        r"\bmkdir\b",
        r"\brm\b",
        r"\bchmod\b",
        r"systemctl",
        r"docker(-| )compose up",
        r"git (clone|pull|checkout|reset)",
    )
    # A mutating command counts as executed when it sits in command position.
    executed = re.compile(
        r"(?:^|[;&|]|\$\()\s*(?:" + "|".join(mutating) + r")\b",
        re.MULTILINE,
    )
    for line_number, line in enumerate(PRECHECK.read_text().splitlines(), start=1):
        stripped = line.strip()
        # Comments and printed advice are not execution.
        if stripped.startswith("#") or stripped.startswith(("note ", "pass ", "warn ", "fail ")):
            continue
        assert not executed.search(line), (
            f"scripts/precheck.sh line {line_number} executes a mutating command: {stripped!r}"
        )


def test_precheck_covers_every_install_role_plus_cloud():
    text = PRECHECK.read_text()
    for role in ("sim", "local", "robot-pi", "cloud"):
        assert f"check_{role.replace('-', '_')}()" in text, f"role {role} is not checked"


def test_precheck_platform_rows_match_install_support():
    """The precheck must not be more permissive than install.sh.

    install.sh is the authority (validate_role_platform in scripts/install/common.sh).
    If the precheck claimed a platform was fine and install.sh then refused it,
    the precheck would be actively misleading.
    """
    common = (REPO / "scripts" / "install" / "common.sh").read_text()
    for row in (
        "sim:darwin:macos:*:amd64",
        "local:linux:ubuntu:24.04:arm64",
        "robot-pi:linux:ubuntu:24.04:arm64",
    ):
        assert row in common, f"install.sh no longer supports {row}; update precheck.sh"
    precheck = PRECHECK.read_text()
    # robot-pi is the narrowest claim, so it is the one worth pinning exactly.
    assert "linux:ubuntu:24.04:arm64) return 0 ;;" in precheck


def test_fault_injector_only_injects_what_the_runtime_can_prove():
    """The injector must not fabricate faults.

    The runtime publishes only faults it can prove, and a fault list that cries
    wolf is worse than a short one. An injector that invented a fourth fault would
    make its own report meaningless, so the scenarios are checked to be the ones
    the simulator actually implements.
    """
    injector = (REPO / "scripts" / "inject_faults.py").read_text()
    runtime = (REPO / "sim" / "mujoco" / "tangying_sim" / "rgbd_runtime.py").read_text()

    # The simulator's vocabulary (what the robot proves) and the injector's
    # vocabulary (what the agent reports) are different layers on purpose: an
    # e-stop becomes ANOMALY_SAFETY_STOP, a module fault becomes
    # ANOMALY_COMPONENT_FAULT. So each fault is checked for in the layer that owns
    # its name, not in both.
    for fault_code in ("EMERGENCY_STOP_LATCHED", "WORKCELL_CALIBRATION_MISMATCH", "NAV_MAP_NOT_READY"):
        assert fault_code in runtime, f"the simulator no longer publishes {fault_code}"
    for agent_code in ("ANOMALY_SAFETY_STOP", "ANOMALY_COMPONENT_FAULT"):
        assert agent_code in injector, f"the injector does not expect {agent_code}"

    # Scenarios that cannot be forced must say so rather than pretending to
    # inject, otherwise a missing fault reads as a supervisor miss.
    assert "notApplicable" in injector
    assert "无法从外部注入" in injector


def test_fault_injector_distinguishes_faults_that_share_a_code():
    """Two module faults share ANOMALY_COMPONENT_FAULT.

    Matching on the code alone would accept the wrong fault as evidence that the
    right one was found.
    """
    injector = (REPO / "scripts" / "inject_faults.py").read_text()
    assert "expect_message_contains" in injector
    assert "NAV_MAP_NOT_READY" in injector
    assert "WORKCELL_CALIBRATION_MISMATCH" in injector

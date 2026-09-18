"""Every script that writes to the local console must carry the session.

This test exists because of a regression it would have caught. The console gained
a session requirement on every mutating route, and the change was verified with
curl and with Go tests. It broke eleven scripts — including the household
acceptance suite the README tells a reader to run, and `scripts/demo.sh`, which CI
runs on every push. A guard that is only verified through one client is a guard
whose other clients are untested, and the ones that break are the ones nobody ran.

The check is deliberately structural rather than behavioural: it reads the scripts
and asks whether the session reaches the request, because starting a stack per
script in a unit test is not something this repository can afford and would not
have caught the regression either — the scripts were fine, the console changed.

What it does not prove: that a script which assembles its headers somewhere else
still works. It fails loudly on any write path in the listed files that does not
mention the session, so a new one has to be looked at rather than assumed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
SCRIPTS = REPOSITORY / "scripts"

# Scripts that write to the *local console*. The Fleet control plane is a
# different server with its own bearer-token auth, and a script that talks to it
# needs nothing from this list: `fleet-sim.sh`, `robocasa-fleet.sh`,
# `robocasa-demo.sh` and `evaluate_natural_language.py` all log in first.
LOCAL_CONSOLE_CLIENTS = (
    "demo.sh",
    "run_home_task_suite.py",
    "run_rgbd_acceptance.py",
    "run_home_mobile_manipulation_acceptance.py",
    "run_navigation_acceptance.py",
    "run_navigation_source_fault.py",
    "build_sim_map.py",
)

# How a script is noticed as a candidate.
#
# The rule is deliberately coarse: anything that talks about tasks and does not
# log in. It is a *candidate* detector, not a write detector — `build_sim_map.py`
# writes through an `api(path, body)` helper whose requests become POSTs because
# they carry data, and no simple pattern sees that. So the list below is
# maintained by hand and this only makes sure a new script cannot be missed in
# silence: adding one that mentions tasks without logging in fails until somebody
# decides which side it is on.
CANDIDATE = "/v1/tasks"

# Scripts that mention tasks and need nothing from this test, each for a stated
# reason rather than by omission.
NOT_LOCAL_CONSOLE_CLIENTS = {
    # Read-only: they analyse a ledger that already exists.
    "diagnose_task.py": "reads and reports; issues no write",
    "compare_destination_policy.py": "reads and reports; issues no write",
    # Authenticated against the Fleet control plane, which is a different server
    # with bearer-token auth of its own.
    "evaluate_natural_language.py": "logs in to the Fleet API",
    "run_fleet_harness.py": "logs in to the Fleet API",
    "run_robocasa_harness.py": "logs in to the Fleet API",
    "fleet-sim.sh": "logs in to the Fleet API",
    "robocasa-demo.sh": "logs in to the Fleet API",
    "robocasa-fleet.sh": "logs in to the Fleet API",
}


def candidates() -> set[str]:
    found = set()
    for path in SCRIPTS.iterdir():
        if path.suffix not in {".py", ".sh"} or path.name == "console_session.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if CANDIDATE in text:
            found.add(path.name)
    return found


def test_no_script_talks_to_the_console_without_being_classified():
    unclassified = sorted(
        candidates() - set(LOCAL_CONSOLE_CLIENTS) - set(NOT_LOCAL_CONSOLE_CLIENTS)
    )
    assert not unclassified, (
        "these scripts talk about tasks and are classified neither as local-console "
        f"clients nor as exempt: {unclassified}. If one writes to the local console "
        "it needs the session header (scripts/console_session.py); if it does not, "
        "add it to NOT_LOCAL_CONSOLE_CLIENTS with the reason."
    )


def test_the_exemptions_are_still_needed():
    """An exemption for a file that no longer exists is an exemption nobody checked."""
    for name, reason in NOT_LOCAL_CONSOLE_CLIENTS.items():
        assert (SCRIPTS / name).exists(), f"{name} is exempted ({reason}) but is gone"
        assert reason.strip(), f"{name} is exempted without a reason"


@pytest.mark.parametrize("name", LOCAL_CONSOLE_CLIENTS)
def test_a_local_console_client_carries_the_session(name):
    """The mechanism, not the vocabulary.

    The first version of this check asked whether the file mentioned "session",
    and it passed a script with the header deliberately removed: a `--session-token`
    flag and an environment-variable name both contain the word. What has to be
    present is the call that puts the header on the request.
    """
    text = (SCRIPTS / name).read_text(encoding="utf-8")
    if name.endswith(".py"):
        # `session_headers(` with the paren: the import line contains the name
        # too, so checking for the name alone passed a file with the call removed.
        assert "console_session import" in text and "session_headers(" in text, (
            f"{name} writes to the local console without putting the session on the "
            "request; the console answers 401 and the script fails with a bare HTTP error"
        )
    else:
        assert "X-Tangying-Session" in text, (
            f"{name} writes to the local console without the session header"
        )


def test_the_shared_helper_is_the_one_place_that_finds_the_token():
    """Scripts discover the token through one module, not each in their own way."""
    for name in LOCAL_CONSOLE_CLIENTS:
        if not name.endswith(".py"):
            continue
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "console_session" in text, (
            f"{name} resolves the console session itself instead of using "
            "scripts/console_session.py; a second discovery path will drift"
        )


def test_the_helper_reads_the_file_the_agent_writes():
    """The name and the environment variable are the contract with the Go side."""
    text = (SCRIPTS / "console_session.py").read_text(encoding="utf-8")
    assert '"console-session"' in text or "'console-session'" in text or 'FILE_NAME = "console-session"' in text
    assert "TANGYING_CONSOLE_SESSION" in text
    # The header name has to match console/guard.go, and nothing generates it.
    guard = (REPOSITORY / "console" / "guard.go").read_text(encoding="utf-8")
    header = re.search(r'SessionHeaderName = "([^"]+)"', guard)
    assert header, "console/guard.go no longer declares SessionHeaderName"
    assert header.group(1) in text, (
        f"console_session.py does not use the header console/guard.go reads "
        f"({header.group(1)}); every request would be refused"
    )
    session_file = re.search(r'console-session"', (REPOSITORY / "cmd" / "local-agent" / "main.go").read_text(encoding="utf-8"))
    assert session_file, "the agent no longer writes console-session; update the helper and this test"

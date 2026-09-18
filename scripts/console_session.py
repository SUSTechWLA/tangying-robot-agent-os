"""Find the local console's session token.

The console requires a session on every mutating route. A browser gets it as a
SameSite=Strict cookie and never sees it; a command-line client reads the file the
agent writes at startup, `<data dir>/console-session`, mode 0600.

That file is the same boundary the task database already has: anything that can
read it can read every task's text, so it grants no new reach. This module is how
a script finds it without the caller having to thread a path through.

Resolution order, most explicit first:

1. an argument the caller passed (`--session-token`),
2. ``TANGYING_CONSOLE_SESSION`` in the environment,
3. ``console-session`` beside a known agent data directory,
4. the newest ``console-session`` under ``artifacts/``.

Nothing found is not an error here. The request goes out without the header and
the console answers 401 with a message saying where the token lives, which is a
better failure than a traceback from this module.
"""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path

ENVIRONMENT_VARIABLE = "TANGYING_CONSOLE_SESSION"
FILE_NAME = "console-session"
#: Written beside the token by the agent: the address of the console it belongs to.
ADDRESS_FILE_NAME = "console-address"

# Where the agent puts it. A deployment may move its data directory, so this is a
# starting point rather than a contract: the search below falls back to looking.
KNOWN_DATA_DIRECTORIES = (
    "artifacts/sim-stack/furnished-home/local-agent",
    "artifacts/sim-stack/stage1/local-agent",
    "artifacts/sim-stack/system-audit/local-agent",
    "artifacts/local-agent",
)


def repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _read(path: Path) -> str | None:
    try:
        token = path.read_text().strip()
    except OSError:
        return None
    return token or None


def _newest_under(root: Path) -> str | None:
    """The token from the most recently started agent under ``root``.

    Newest wins, and that rule is the whole point. The first version of this
    module walked a fixed list of data directories and returned the first one that
    had a file — so once a second deployment existed, the older one won and every
    request carried a dead token. It failed with ``CONSOLE_SESSION_REQUIRED``
    against a healthy console, which reads as "the guard is broken" rather than
    "you are holding last week's key".
    """
    candidates = []
    for path in root.glob(f"**/{FILE_NAME}"):
        try:
            candidates.append((path.stat().st_mtime, path))
        except OSError:
            continue
    for _, path in sorted(candidates, reverse=True):
        token = _read(path)
        if token:
            return token
    return None


def _address_of(token_path: Path) -> str | None:
    """The address the agent that wrote this token is listening on, if recorded."""
    text = _read(token_path.with_name(ADDRESS_FILE_NAME))
    return text.lower() if text else None


def _host_port(base_url: str) -> str:
    """The host:port a base URL names, in the form the agent records."""
    without_scheme = base_url.split("://", 1)[-1]
    return without_scheme.split("/", 1)[0].lower()


def resolve_token(explicit: str | None = None, base_url: str | None = None) -> str | None:
    """Return the console session token, or None when nothing can be found.

    The rule is *which console are you talking to*, not *which file is newest*.
    Both of the simpler rules were tried and both failed on the same situation —
    more than one console on one machine, which is ordinary:

    - "the first directory in a fixed list" returned an older deployment's token
      the moment a second one existed;
    - "the newest file" returned the wrong one when an older deployment restarted
      after the one being tested.

    Either way the request is refused with ``CONSOLE_SESSION_REQUIRED`` against a
    healthy console, which reads as a broken guard rather than as the wrong key.
    So each agent records its address, and a caller that knows its base URL gets
    the token belonging to that console. Newest remains the fallback for a caller
    that does not say.
    """
    if explicit:
        return explicit
    from_environment = os.environ.get(ENVIRONMENT_VARIABLE, "").strip()
    if from_environment:
        return from_environment
    root = repository_root()
    if base_url:
        wanted = _host_port(base_url)
        for path in sorted((root / "artifacts").glob(f"**/{FILE_NAME}")):
            if _address_of(path) == wanted:
                token = _read(path)
                if token:
                    return token
    newest = _newest_under(root / "artifacts")
    if newest:
        return newest
    known = [root / directory / FILE_NAME for directory in KNOWN_DATA_DIRECTORIES]
    best = None
    for path in known:
        token = _read(path)
        if not token:
            continue
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        if best is None or stamp > best[0]:
            best = (stamp, token)
    return best[1] if best else None


# The header the console reads. Kept as a literal rather than imported from Go,
# because there is no build step that could keep the two in sync and a wrong name
# here fails as a 401 with a message that names the right one.
HEADER = "X-Tangying-Session"


def headers(token: str | None, base: dict[str, str] | None = None) -> dict[str, str]:
    """Return base headers plus the session header when a token was found."""
    merged = dict(base or {})
    if token:
        merged[HEADER] = token
    return merged


def install_loopback_opener() -> None:
    """Send this process's console requests straight to loopback, never via a proxy.

    ``urllib`` reads the system proxy settings, and a machine with one configured
    — which is ordinary on macOS — routes ``http://127.0.0.1:8898`` through it. The
    result is ``Connection refused`` from a proxy that has never heard of the
    console, which reads as "the agent is down" and is not.

    The console binds loopback by policy (cmd/local-agent/listen.go), so a proxy
    can only be in the way. ``curl`` needs ``--noproxy 127.0.0.1`` for the same
    reason, and scripts/demo.sh already passes it.
    """
    urllib.request.install_opener(
        urllib.request.build_opener(urllib.request.ProxyHandler({}))
    )

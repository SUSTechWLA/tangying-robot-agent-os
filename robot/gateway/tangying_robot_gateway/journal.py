from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LookupResult:
    status: str
    events: list[str]


class RuntimeJournal:
    VERSION = 1

    def __init__(self, path: Path | str | None, max_commands: int = 128):
        self.path = Path(path) if path else None
        self.max_commands = max_commands
        self.estop_latched = False
        self.estop_reason = ""
        self._commands: dict[str, dict[str, object]] = {}
        self.resource_grants: dict[str, tuple[str, int]] = {}
        self._lock = threading.RLock()
        if self.path and self.path.exists():
            self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            if data.get("version") != self.VERSION:
                raise ValueError("unsupported runtime journal version")
            self.estop_latched = bool(data.get("estop_latched"))
            self.estop_reason = str(data.get("estop_reason", ""))
            commands = data.get("commands", {})
            if not isinstance(commands, dict):
                raise TypeError("invalid runtime journal commands")
            self._commands = dict(list(commands.items())[-self.max_commands :])
            resources = data.get("resource_grants", {})
            if not isinstance(resources, dict):
                raise TypeError("invalid runtime journal resource grants")
            loaded: dict[str, tuple[str, int]] = {}
            for resource_id, grant in resources.items():
                if (
                    not isinstance(resource_id, str)
                    or not isinstance(grant, dict)
                    or not isinstance(grant.get("owner"), str)
                    or not isinstance(grant.get("token"), int)
                    or grant["token"] <= 0
                ):
                    raise TypeError("invalid runtime journal resource grant")
                loaded[resource_id] = (grant["owner"], grant["token"])
            self.resource_grants = loaded
        except Exception as exc:  # noqa: BLE001 - corrupt safety state fails closed
            self.estop_latched = True
            self.estop_reason = f"RUNTIME_JOURNAL_INVALID: {exc}"
            self._commands = {}
            self.resource_grants = {}

    def set_estop(self, latched: bool, reason: str) -> None:
        with self._lock:
            self.estop_latched = latched
            self.estop_reason = reason
            self._persist()

    def set_resource_grant(self, resource_id: str, owner: str, token: int) -> None:
        if not resource_id or not owner or token <= 0:
            raise ValueError("resource id, owner and positive fencing token are required")
        with self._lock:
            current = self.resource_grants.get(resource_id)
            if current is not None and (
                token < current[1] or (token == current[1] and owner != current[0])
            ):
                raise ValueError("resource fencing grant must advance monotonically")
            self.resource_grants[resource_id] = (owner, token)
            self._persist()

    def lookup(self, key: str, fingerprint: str) -> LookupResult:
        record = self._commands.get(key)
        if record is None:
            return LookupResult("missing", [])
        if record.get("fingerprint") != fingerprint:
            return LookupResult("conflict", [])
        return LookupResult("replay", list(record.get("events", [])))

    def record(self, key: str, fingerprint: str, events: list[str]) -> None:
        self._commands.pop(key, None)
        self._commands[key] = {"fingerprint": fingerprint, "events": list(events)}
        while len(self._commands) > self.max_commands:
            del self._commands[next(iter(self._commands))]
        self._persist()

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = json.dumps(
            {
                "version": self.VERSION,
                "estop_latched": self.estop_latched,
                "estop_reason": self.estop_reason,
                "commands": self._commands,
                "resource_grants": {
                    resource_id: {"owner": owner, "token": token}
                    for resource_id, (owner, token) in self.resource_grants.items()
                },
            },
            separators=(",", ":"),
        ).encode()
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

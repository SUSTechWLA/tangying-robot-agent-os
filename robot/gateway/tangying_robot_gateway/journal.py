from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LookupResult:
    status: str
    events: list[str]


class RuntimeJournal:
    VERSION = 2

    def __init__(self, path: Path | str | None, max_commands: int = 128):
        self.path = Path(path) if path else None
        self.max_commands = max_commands
        self.estop_latched = False
        self.estop_reason = ""
        self._commands: dict[str, dict[str, object]] = {}
        self._record_blobs = {}
        self.resource_grants: dict[str, tuple[str, int]] = {}
        self._lock = threading.RLock()
        if self.path and self.path.exists():
            self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            if data.get("version") not in (1, self.VERSION):
                raise ValueError("unsupported runtime journal version")
            self.estop_latched = bool(data.get("estop_latched"))
            self.estop_reason = str(data.get("estop_reason", ""))
            commands = data.get("commands", {})
            if not isinstance(commands, dict):
                raise TypeError("invalid runtime journal commands")
            for key, record in commands.items():
                if not isinstance(record, dict):
                    raise TypeError("invalid runtime journal command")
                blob = record.pop("event_blob", None)
                if blob is not None:
                    if (data['version'] != self.VERSION or 'events' in record
                            or not isinstance(blob, str) or len(blob) != 64
                            or any(c not in '0123456789abcdef' for c in blob)):
                        raise ValueError("invalid runtime event blob reference")
                    path = self._blob_directory() / (blob+'.json')
                    if self._blob_directory().is_symlink() or path.is_symlink():
                        raise ValueError("runtime event blob symlink")
                    raw = path.read_bytes()
                    if hashlib.sha256(raw).hexdigest() != blob:
                        raise ValueError("runtime event blob digest mismatch")
                    record['events'] = json.loads(raw)
                    self._record_blobs[key] = (record, blob)
                if not isinstance(record.get('events', []), list) or any(
                        not isinstance(event, str) for event in record.get('events', [])):
                    raise ValueError("invalid runtime event data")
            terminal_keys = [key for key, value in commands.items() if not value.get("pending") and not value.get("reconciled")]
            retained = set(terminal_keys[-self.max_commands:])
            self._commands = {
                key: value for key, value in commands.items()
                if value.get("pending") or value.get("reconciled") or key in retained
            }
            if any(record.get("pending") for record in self._commands.values()):
                self.estop_latched = True
                self.estop_reason = "EXECUTION_OUTCOME_UNKNOWN"
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
        with self._lock:
            record = self._commands.get(key)
            if record is None:
                return LookupResult("missing", [])
            if record.get("fingerprint") != fingerprint:
                return LookupResult("conflict", [])
            if record.get("pending"):
                return LookupResult("pending", [])
            if record.get("reconciled"):
                return LookupResult("reconciled", [])
            return LookupResult("replay", list(record.get("events", [])))

    def reconcile_pending(self, *, operator: str, reason: str) -> None:
        """Attested local recovery retains unknown keys permanently; never replays motion."""
        if not operator.strip() or not reason.strip():
            raise ValueError("operator and reconciliation reason are required")
        with self._lock:
            previous = self._commands
            self._commands = {key: dict(value) for key, value in previous.items()}
            for record in self._commands.values():
                if record.get("pending"):
                    record.update(pending=False, events=[], reconciled={"operator": operator,
                        "reason": reason, "unix_ms": int(time.time() * 1000)})
            try:
                self._persist()
            except OSError:
                self._commands = previous
                raise

    def begin(self, key: str, fingerprint: str) -> None:
        """Durably reserve execution before a backend may produce side effects."""
        with self._lock:
            if key in self._commands:
                raise ValueError("command already recorded")
            self._commands[key] = {"fingerprint": fingerprint, "events": [], "pending": True}
            self._persist()

    def record(self, key: str, fingerprint: str, events: list[str]) -> None:
        with self._lock:
            previous = dict(self._commands)
            self._commands.pop(key, None)
            self._commands[key] = {"fingerprint": fingerprint, "events": list(events)}
            terminal = [k for k, value in self._commands.items() if not value.get("pending") and not value.get("reconciled")]
            for old_key in terminal[:max(0, len(terminal) - self.max_commands)]:
                del self._commands[old_key]
            try:
                self._persist()
            except OSError:
                self._commands = previous
                raise

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        records, referenced = {}, set()
        for key, record in self._commands.items():
            disk_record = {k: v for k, v in record.items() if k != 'events'}
            events = record.get('events', [])
            if events:
                cached = self._record_blobs.get(key)
                if cached is not None and cached[0] is record:
                    blob = cached[1]
                else:
                    raw = json.dumps(events, separators=(',', ':')).encode()
                    blob = hashlib.sha256(raw).hexdigest()
                    directory = self._blob_directory()
                    if directory.is_symlink():
                        raise OSError('runtime event blob directory is a symlink')
                    directory.mkdir(mode=0o700, exist_ok=True)
                    target = directory/(blob+'.json')
                    if target.is_symlink():
                        raise OSError('runtime event blob is a symlink')
                    if target.exists():
                        if hashlib.sha256(target.read_bytes()).hexdigest() != blob:
                            raise OSError('runtime event blob digest mismatch')
                    else:
                        self._atomic_write(target, raw)
                    self._record_blobs[key] = (record, blob)
                disk_record['event_blob'] = blob
                referenced.add(blob+'.json')
            else:
                disk_record['events'] = []
            records[key] = disk_record
        payload = json.dumps(
            {
                "version": self.VERSION,
                "estop_latched": self.estop_latched,
                "estop_reason": self.estop_reason,
                "commands": records,
                "resource_grants": {
                    resource_id: {"owner": owner, "token": token}
                    for resource_id, (owner, token) in self.resource_grants.items()
                },
            },
            separators=(",", ":"),
        ).encode()
        self._atomic_write(self.path, payload)
        # Only after the durable index commit can unreferenced event files be
        # removed. Pending/reconciled records remain outside terminal eviction.
        self._record_blobs = {k: v for k, v in self._record_blobs.items() if k in self._commands}
        directory = self._blob_directory()
        if directory.exists():
            for old in directory.glob('*.json'):
                if (old.name not in referenced and len(old.stem) == 64
                        and all(c in '0123456789abcdef' for c in old.stem)):
                    try:
                        old.unlink()
                    except OSError:
                        pass  # Collection failure must not invalidate a committed receipt.

    def _blob_directory(self) -> Path:
        return self.path.with_name(self.path.name+'.events')

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        temporary = path.with_suffix(path.suffix + '.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary.exists():
                temporary.unlink()

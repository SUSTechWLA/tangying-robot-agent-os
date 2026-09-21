"""Edge-local content-addressed bytes and durable report / uncertainty ledger."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from .model import EvidenceRef, EvidenceSample, StateReport, canonical


class EvidenceStore:
    def __init__(self, root, source="edge"):
        self.root = Path(root)
        self.source = source
        (self.root / "blobs").mkdir(parents=True, exist_ok=True)
        self.db = self.root / "reports.sqlite3"
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS reports (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id TEXT NOT NULL UNIQUE, task_id TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS logical_clock (id INTEGER PRIMARY KEY, value INTEGER NOT NULL);
                INSERT OR IGNORE INTO logical_clock VALUES(1,0);
                CREATE TABLE IF NOT EXISTS barrier (
                    robot_id TEXT PRIMARY KEY, report_id TEXT NOT NULL, body TEXT NOT NULL);
            """)

    def _connect(self):
        connection = sqlite3.connect(self.db, timeout=10)
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def put(self, data: bytes, kind, summary=None):
        digest = hashlib.sha256(data).hexdigest()
        path = self.root / "blobs" / digest
        try:
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise OSError("content-addressed evidence is corrupt")
        return EvidenceRef(
            uri=f"evidence://{self.source}/{digest}",
            sha256=digest,
            kind=kind,
            inline_summary=summary or {},
        )

    def exists(self, ref):
        if ref.uri != f"evidence://{self.source}/{ref.sha256}":
            return False
        try:
            return (
                hashlib.sha256((self.root / "blobs" / ref.sha256).read_bytes()).hexdigest()
                == ref.sha256
            )
        except OSError:
            return False

    def record_sample(self, **values):
        sample = EvidenceSample(**values)
        ref = self.put(
            canonical(sample.model_dump(exclude={"record_ref"})).encode(),
            "detection",
            {"sample_id": sample.sample_id, "source_id": sample.source_id},
        )
        return sample.model_copy(update={"record_ref": ref})

    def append(self, report: StateReport, robot_id="", *, release=False):
        body = canonical(report)
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO reports(report_id,task_id,body) VALUES(?,?,?)",
                (report.report_id, report.task_id, body),
            )
            if robot_id:
                if report.verdict != "VERIFIED" and not release:
                    connection.execute(
                        "INSERT OR REPLACE INTO barrier VALUES(?,?,?)",
                        (robot_id, report.report_id, body),
                    )
                elif release:
                    # Callers may only release after a fresh re-verification of the blocked action.
                    connection.execute("DELETE FROM barrier WHERE robot_id=?", (robot_id,))

    def blocked(self, robot_id):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT body FROM barrier WHERE robot_id=?", (robot_id,)
            ).fetchone()
        return StateReport.model_validate(json.loads(row[0])) if row else None

    def reports(self):
        with self._connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute("SELECT body FROM reports ORDER BY sequence")
            ]

    def next_clock(self):
        with self._connect() as connection:
            return connection.execute(
                "UPDATE logical_clock SET value=MAX(value,(SELECT COALESCE(MAX(sequence),0) FROM reports))+1 "
                "WHERE id=1 RETURNING value"
            ).fetchone()[0]

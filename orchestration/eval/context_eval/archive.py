"""Load and verify the code actually used by an archived experiment."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import sys
from pathlib import Path

from .dataset import canonical


def verify_archive(root: Path, responses=False):
    protocol = json.loads((root / "protocol.json").read_text())
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    if digest(root / "cases.jsonl") != protocol["dataset_sha256"]:
        raise ValueError("archived dataset digest differs")
    if digest(root / "context-render") != protocol["renderer_binary_sha256"]:
        raise ValueError("archived renderer digest differs")
    for relative, expected in protocol["source_sha256"].items():
        if digest(root / "source_snapshot" / relative) != expected:
            raise ValueError(f"archived source digest differs: {relative}")
    if responses:
        endpoint = json.loads((root / "model-config.json").read_text())["base_url"]
        for path in (root / "responses").glob("*.json"):
            record = json.loads(path.read_text())
            identity = hashlib.sha256(
                canonical({"endpoint": endpoint, "request": record["request"]}).encode()
            ).hexdigest()
            if identity != path.stem or record["request_hash"] != identity:
                raise ValueError(f"cached request digest differs: {path.name}")
            if (
                hashlib.sha256(canonical(record["response"]).encode()).hexdigest()
                != record["response_sha256"]
            ):
                raise ValueError(f"cached response digest differs: {path.name}")
            if record["response"] is not None:
                try:
                    answer = json.loads(record["response"]["choices"][0]["message"]["content"])
                except (KeyError, IndexError, ValueError, TypeError):
                    answer = None
                if record["answer"] != answer:
                    raise ValueError(f"cached answer differs: {path.name}")
    return protocol


def frozen_module(root: Path, name: str):
    verify_archive(root)
    folder = root / "source_snapshot/orchestration/eval/context_eval"
    package = "_context_archive_" + hashlib.sha256(str(root).encode()).hexdigest()[:12]
    if package not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            package, folder / "__init__.py", submodule_search_locations=[str(folder)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[package] = module
        spec.loader.exec_module(module)
    if name == "episodes":
        path = root / "source_snapshot/episodes.py"
        protocol = json.loads((root / "episodes-protocol.json").read_text())
        if hashlib.sha256(path.read_bytes()).hexdigest() != protocol["source_sha256"]:
            raise ValueError("archived episode source differs")
        spec = importlib.util.spec_from_file_location(package + ".episodes", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[package + ".episodes"] = module
        spec.loader.exec_module(module)
        return module
    return importlib.import_module(package + "." + name)

"""Run a bounded, local regression phase and retain commands, source identity and logs."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
OUTPUT = Path(__file__).resolve().parent / sys.argv[1]
COMMAND = sys.argv[2:]
OUTPUT.mkdir(exist_ok=False)


def source_identity():
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, text=True
    ).splitlines()
    files = {}
    for name in sorted(set(names)):
        path = ROOT / name
        if name.startswith(("artifacts/", "docs/", "book/")):
            continue
        if path.is_file() and (path.suffix in {".go", ".py", ".js", ".mjs", ".html"}
                               or name in {"go.mod", "go.sum", "web/package.json"}):
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"files": files, "digest": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}


def write(name, value):
    (OUTPUT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


before = source_identity()
write("source-before.json", before)
env = os.environ.copy()
for key in list(env):
    if key in {"GOFLAGS", "GORACE"} or key.startswith(("AGENT_", "EDGE_", "TANGYING_")):
        env.pop(key)
env.update(GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off")
write("metadata.json", {
    "command": COMMAND, "cwd": str(ROOT),
    "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    "startedAtUTC": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "scope": "Software regression; no live LLM or robot. Physical evidence is a separate campaign.",
    "environmentOverrides": {"GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off",
                             "cleared": "GOFLAGS,GORACE,AGENT_*,EDGE_*,TANGYING_*"},
})
started = time.monotonic()
with (OUTPUT / "output.txt").open("w") as stream:
    process = subprocess.Popen(COMMAND, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
    try:
        code = process.wait(timeout=360)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        code = 124
elapsed = time.monotonic() - started
after = source_identity()
write("source-after.json", after)
counts = {}
packages = []
failures = []
for line in (OUTPUT / "output.txt").read_text().splitlines():
    try:
        event = json.loads(line)
    except ValueError:
        continue
    if not isinstance(event, dict):
        continue
    action, name = event.get("Action"), event.get("Test", "")
    if action in {"pass", "fail", "skip"}:
        if name and "/" not in name:
            counts[action] = counts.get(action, 0) + 1
        if not name:
            packages.append({key: event.get(key) for key in ("Package", "Action", "Elapsed")})
    if action == "fail":
        failures.append(event)
result = {"exitCode": code, "elapsedSeconds": elapsed, "sourceStable": before == after,
          "sourceDigest": after["digest"], "topLevelGoTests": counts, "packages": packages,
          "failures": failures, "outputSHA256": hashlib.sha256((OUTPUT / "output.txt").read_bytes()).hexdigest()}
write("result.json", result)
print(json.dumps(result, ensure_ascii=False))
sys.exit(code if code else (0 if before == after else 2))

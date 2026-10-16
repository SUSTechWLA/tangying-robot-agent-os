"""Run the frozen offline verifier only with ordinary Python and exact dependencies."""
import hashlib
import json
import runpy
import sys
from pathlib import Path

if sys.flags.optimize:
    raise SystemExit("Refused: frozen verification requires ordinary Python without -O/-OO or PYTHONOPTIMIZE")
root=Path(__file__).resolve().parent
metadata=json.loads((root/"scripts/frozen-dependencies.json").read_text())
for name,record in metadata["files"].items():
    path=root/"scripts"/name
    if hashlib.sha256(path.read_bytes()).hexdigest()!=record["sha256"]:
        raise SystemExit("Frozen dependency checksum differs: "+name)
verifier=root/"frozen-revalidate_host_inspection.py"
if hashlib.sha256(verifier.read_bytes()).hexdigest()!=metadata["verifierSha256"]:
    raise SystemExit("Frozen verifier checksum differs")
case=sys.argv[1] if len(sys.argv)>1 else "host-inspection02"
sys.argv=[str(verifier),case]
runpy.run_path(str(verifier),run_name="__main__")

import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[3]
sys.path.insert(0, str(PACKAGE))
sys.path.insert(0, str(ROOT / "python"))

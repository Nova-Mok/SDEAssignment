import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT / "apps" / "agent", REPO_ROOT / "benchmarks"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

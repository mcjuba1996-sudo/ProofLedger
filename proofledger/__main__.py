"""Single check command: python -m proofledger  (tests, then policy checks)."""
import subprocess
import sys
from pathlib import Path

from . import verify


def main():
    root = Path(__file__).resolve().parent.parent
    if (root / "tests").is_dir():
        code = subprocess.call([sys.executable, "-m", "pytest", "-q", str(root / "tests")], cwd=root)
        if code != 0:
            print("TESTS FAILED")
            return code
    return verify.main([])


if __name__ == "__main__":
    sys.exit(main())

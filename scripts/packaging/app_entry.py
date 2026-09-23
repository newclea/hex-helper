"""Desktop executable entry point, including an offline package diagnostic."""
import sys
from pathlib import Path

for folder in ("recognition_overlay", "product", "phase4"):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / folder))

from app import main

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from package_check import check_app
        raise SystemExit(check_app(Path(sys.argv[2])))
    raise SystemExit(main())

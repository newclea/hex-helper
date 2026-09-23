import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "product"))
from offline_speech_worker import main

if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--self-test":
        from package_check import check_voice
        raise SystemExit(check_voice(Path(sys.argv[2]), Path(sys.argv[3])))
    raise SystemExit(main())

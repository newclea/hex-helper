"""Precompute fixed phrases without playing audio."""

from array import array
from pathlib import Path
import argparse

from fixed_speech_cache import FixedSpeechCache
from offline_speech_assets import resolve_offline_speech_paths
from offline_speech_worker import build_sherpa_tts, initialize_native_dependencies
from speech_policy import FIXED_SPEECH_TEXTS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    paths = resolve_offline_speech_paths(args.bundle_root)
    cache = FixedSpeechCache(args.bundle_root / "assets/speech/fixed", paths.model)
    initialize_native_dependencies()
    tts = None
    for text in FIXED_SPEECH_TEXTS:
        if cache.read(text) is None:
            if tts is None:
                tts = build_sherpa_tts(paths)
            audio = tts.generate(text, sid=0, speed=1.0)
            cache.write(text, array("f", audio.samples).tobytes(), int(audio.sample_rate))
        assert cache.read(text) is not None, "Fixed speech cache verification failed"
        print(cache.path_for(text), flush=True)


if __name__ == "__main__":
    main()

"""Persistent, model-specific WAV cache for fixed companion phrases."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import wave
from array import array
from pathlib import Path

from speech_policy import FIXED_SPEECH_TEXTS


class FixedSpeechCache:
    def __init__(self, directory: Path, model: Path) -> None:
        self.directory = directory
        self.model = model
        self._model_digest: str | None = None

    def path_for(self, text: str) -> Path:
        if self._model_digest is None:
            with self.model.open("rb") as stream:
                self._model_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        key = f"pcm16-v1:sid0:speed1:{self._model_digest}:{text}"
        return self.directory / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".wav")

    def read(self, text: str) -> tuple[bytes, int] | None:
        if text not in FIXED_SPEECH_TEXTS:
            return None
        try:
            with wave.open(str(self.path_for(text)), "rb") as audio:
                rate = audio.getframerate()
                frames = audio.getnframes()
                if (audio.getnchannels() != 1 or audio.getsampwidth() != 2
                        or not 8000 <= rate <= 96000 or not 0 < frames <= rate * 60):
                    return None
                raw = audio.readframes(frames)
                if len(raw) != frames * 2:
                    return None
            samples = array("h", raw)
            if sys.byteorder != "little":
                samples.byteswap()
            return array("f", (sample / 32768 for sample in samples)).tobytes(), rate
        except (OSError, EOFError, ValueError, wave.Error):
            return None

    def write(self, text: str, pcm: bytes, sample_rate: int) -> None:
        if text not in FIXED_SPEECH_TEXTS or not pcm:
            return
        target = self.path_for(text)
        self.directory.mkdir(parents=True, exist_ok=True)
        samples = array("h", (max(-32768, min(32767, round(value * 32768)))
                              for value in array("f", pcm)))
        if sys.byteorder != "little":
            samples.byteswap()
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
            with wave.open(str(temporary), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(sample_rate)
                audio.writeframes(samples.tobytes())
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

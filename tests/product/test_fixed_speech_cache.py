from array import array
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fixed_speech_cache import FixedSpeechCache
from speech_policy import STARTUP_GREETING


class FixedSpeechCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = self.root / "model.onnx"
        self.model.write_bytes(b"model-v1")
        self.cache = FixedSpeechCache(self.root / "cache", self.model)
        self.pcm = array("f", [0.0, 0.25, -0.5, 0.75]).tobytes()

    def test_cache_survives_a_new_process_instance(self):
        self.cache.write(STARTUP_GREETING, self.pcm, 24000)
        reopened = FixedSpeechCache(self.root / "cache", self.model)
        self.assertEqual((self.pcm, 24000), reopened.read(STARTUP_GREETING))

    def test_changed_model_or_text_cannot_reuse_audio(self):
        self.cache.write(STARTUP_GREETING, self.pcm, 24000)
        self.model.write_bytes(b"model-v2")
        changed = FixedSpeechCache(self.root / "cache", self.model)
        self.assertIsNone(changed.read(STARTUP_GREETING))
        self.assertIsNone(self.cache.read(STARTUP_GREETING + "新文案"))

    def test_missing_corrupt_and_truncated_cache_fall_back(self):
        self.assertIsNone(self.cache.read(STARTUP_GREETING))
        self.cache.write(STARTUP_GREETING, self.pcm, 24000)
        path = self.cache.path_for(STARTUP_GREETING)
        valid = path.read_bytes()
        for damaged in (b"not a wav", valid[:-2]):
            path.write_bytes(damaged)
            self.assertIsNone(self.cache.read(STARTUP_GREETING))

    def test_dynamic_text_is_not_persisted(self):
        self.cache.write("动态推荐", self.pcm, 24000)
        self.assertFalse(self.cache.directory.exists())

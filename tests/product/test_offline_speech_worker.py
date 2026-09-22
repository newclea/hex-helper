from __future__ import annotations

import inspect
import io
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from array import array
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from offline_speech_assets import OfflineSpeechPaths
from offline_speech_worker import (
    OfflineSpeechEngine,
    PlaybackAbort,
    PlaybackComplete,
    build_sherpa_tts,
    run_protocol,
    _speech_segments,
)


class FakeAudio:
    def __init__(self, samples=(0.25, -0.5, 1.0), sample_rate=24000):
        self.samples = samples
        self.sample_rate = sample_rate


class FakeTts:
    def __init__(self, audio=None):
        self.audio = audio or FakeAudio()
        self.calls = []

    @property
    def sample_rate(self):
        return self.audio.sample_rate

    def generate(self, text, sid=0, speed=1.0, callback=None):
        self.calls.append((text, sid, speed))
        if callback is not None:
            callback(self.audio.samples, 1.0)
        return self.audio


class FakeRawStream:
    def __init__(self, callback, finished_callback, frames=(1024, 1024), **kwargs):
        self.callback = callback
        self.finished_callback = finished_callback
        self.frames = frames
        self.received = bytearray()
        self.aborted = False

    def start(self):
        for index in range(200):
            frames = self.frames[index % len(self.frames)]
            output = bytearray(frames * 4)
            try:
                self.callback(output, frames, None, None)
            except PlaybackComplete:
                self.received.extend(output)
                self.finished_callback()
                break
            except PlaybackAbort:
                break
            self.received.extend(output)
            time.sleep(0.001)
        return self

    def abort(self):
        self.aborted = True

    def close(self):
        return None


class BlockingTts(FakeTts):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def generate(self, text, sid=0, speed=1.0, callback=None):
        self.entered.set()
        self.release.wait(2.0)
        return super().generate(text, sid, speed, callback)


def fake_paths(root):
    return OfflineSpeechPaths(
        model=root / "model.onnx",
        tokens=root / "tokens.txt",
        lexicon=root / "lexicon.txt",
        data_dir=root / "dict",
        rule_fsts=(root / "number.fst",),
        wheel_dir=root / "wheels",
    )


class OfflineSpeechWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.events = []
        self.tts = FakeTts()
        self.streams = []
        self.engine = self.make_engine(self.tts)
        self.addCleanup(self.engine.close)

    def make_engine(self, tts, stream_type=FakeRawStream):
        def make_stream(**kwargs):
            stream = stream_type(**kwargs)
            self.streams.append(stream)
            return stream

        return OfflineSpeechEngine(
            fake_paths(Path(self.temp.name)),
            self.events.append,
            tts_factory=lambda paths: tts,
            raw_output_stream_factory=make_stream,
            prewarm_texts=(),
        )

    def wait_for(self, predicate):
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        self.fail("condition was not reached")

    def test_start_loads_model_once_and_emits_ready(self):
        calls = []
        self.engine._tts_factory = lambda paths: calls.append(paths) or self.tts

        self.assertTrue(self.engine.start())
        self.assertTrue(self.engine.start())

        self.assertEqual(1, len(calls))
        self.assertEqual([{"event": "ready"}], self.events)

    def test_speak_streams_float32_chunks_and_emits_started_then_finished_for_request(self):
        self.tts.audio = FakeAudio(samples=tuple(index / 2048 for index in range(1025)))
        self.engine.start()
        self.assertTrue(self.engine.speak(1, "推荐选择珠光护手"))
        self.wait_for(lambda: any(item["event"] == "finished" for item in self.events))

        expected = array("f", self.tts.audio.samples).tobytes()
        received = bytes(self.streams[0].received)
        self.assertEqual(8192, len(received))
        self.assertEqual(expected, received[: len(expected)])
        self.assertEqual(bytes(8192 - len(expected)), received[len(expected) :])
        scoped = [event for event in self.events if "request_id" in event]
        self.assertEqual(
            [{"event": "started", "request_id": 1}, {"event": "finished", "request_id": 1}],
            scoped,
        )

    def test_streaming_playback_starts_before_generation_returns(self):
        playback_started = threading.Event()
        release_generation = threading.Event()

        class StreamingTts(FakeTts):
            def generate(tts_self, text, sid=0, speed=1.0, callback=None):
                tts_self.calls.append((text, sid, speed))
                callback(tts_self.audio.samples, 0.5)
                self.assertTrue(playback_started.wait(1.0))
                release_generation.wait(1.0)
                return tts_self.audio

        class SignallingStream(FakeRawStream):
            def start(stream_self):
                playback_started.set()
                return super().start()

        self.engine.close()
        self.engine = self.make_engine(StreamingTts(), SignallingStream)
        self.engine.start()
        self.engine.speak(1, "动态推荐")

        self.assertTrue(playback_started.wait(1.0))
        release_generation.set()
        self.wait_for(lambda: {"event": "finished", "request_id": 1} in self.events)

    def test_start_prewarms_fixed_phrase_for_cached_playback(self):
        tts = FakeTts()
        self.engine.close()
        self.engine = OfflineSpeechEngine(
            fake_paths(Path(self.temp.name)),
            self.events.append,
            tts_factory=lambda paths: tts,
            raw_output_stream_factory=lambda **kwargs: FakeRawStream(**kwargs),
            prewarm_texts=("正在为你查看可选英雄。",),
        )
        self.engine.start()
        self.wait_for(lambda: len(tts.calls) == 1)

        self.engine.speak(1, "正在为你查看可选英雄。")
        self.wait_for(lambda: {"event": "finished", "request_id": 1} in self.events)

        self.assertEqual(1, len(tts.calls))

    def test_dynamic_text_is_generated_in_short_playable_segments(self):
        self.assertEqual(
            ("推荐选择珠光护手，", "当前玩法胜率优先。"),
            _speech_segments("推荐选择珠光护手，当前玩法胜率优先。"),
        )

    def test_empty_text_emits_error_without_synthesis(self):
        self.engine.start()

        self.assertFalse(self.engine.speak(1, "   "))

        self.assertEqual([], self.tts.calls)
        self.assertEqual("error", self.events[-1]["event"])
        self.assertEqual(1, self.events[-1]["request_id"])

    def test_cancel_aborts_raw_stream_and_emits_cancelled(self):
        class DeferredStream(FakeRawStream):
            def start(stream_self):
                return stream_self

        self.engine.close()
        self.engine = self.make_engine(self.tts, DeferredStream)
        self.engine.start()
        self.engine.speak(1, "播放")
        self.wait_for(lambda: bool(self.streams))

        self.engine.cancel(1)

        self.wait_for(lambda: self.streams[0].aborted)
        self.assertTrue(self.streams[0].aborted)
        self.assertIn({"event": "cancelled", "request_id": 1}, self.events)

    def test_cancelled_synthesis_result_is_discarded(self):
        blocking = BlockingTts()
        self.engine.close()
        self.engine = self.make_engine(blocking)
        self.engine.start()
        self.engine.speak(1, "旧请求")
        self.assertTrue(blocking.entered.wait(1.0))
        self.engine.cancel(1)
        self.engine.speak(2, "新请求")
        blocking.release.set()
        self.wait_for(lambda: any(item.get("request_id") == 2 for item in self.events))
        time.sleep(0.02)

        stale = [event for event in self.events if event.get("request_id") == 1]
        self.assertEqual([{"event": "cancelled", "request_id": 1}], stale)
        self.assertIsNone(self.engine._cached_audio("旧请求"))

    def test_stale_raw_stream_callback_cannot_finish_newer_request(self):
        callbacks = []

        class DeferredStream(FakeRawStream):
            def start(stream_self):
                callbacks.append(stream_self)
                return stream_self

        self.engine.close()
        self.engine = self.make_engine(self.tts, DeferredStream)
        self.engine.start()
        self.engine.speak(1, "旧播放")
        self.wait_for(lambda: len(callbacks) == 1)
        self.engine.speak(2, "新播放")
        self.wait_for(lambda: len(callbacks) == 2)
        old = callbacks[0]
        with self.assertRaises(PlaybackAbort):
            old.callback(bytearray(4096), 1024, None, None)
        old.finished_callback()
        time.sleep(0.02)

        self.assertNotIn({"event": "finished", "request_id": 2}, self.events)

    def test_cancelled_callback_fills_complete_silent_block_before_abort(self):
        callbacks = []

        class DeferredStream(FakeRawStream):
            def start(stream_self):
                callbacks.append(stream_self)
                return stream_self

        self.engine.close()
        self.engine = self.make_engine(self.tts, DeferredStream)
        self.engine.start()
        self.engine.speak(1, "取消")
        self.wait_for(lambda: len(callbacks) == 1)
        self.engine.cancel(1)
        output = bytearray(b"\xff" * 4096)

        with self.assertRaises(PlaybackAbort):
            callbacks[0].callback(output, 1024, None, None)

        self.assertEqual(bytes(4096), bytes(output))

    def test_audio_blocks_are_prebuilt_before_callback(self):
        from offline_speech_worker import _RequestControl

        callback = self.engine._make_audio_callback(_RequestControl(1), b"\x01" * 4100)
        closure = dict(zip(callback.__code__.co_freevars, callback.__closure__))
        blocks = closure["blocks"].cell_contents
        silent_block = closure["silent_block"].cell_contents

        self.assertEqual((4096, 4096), tuple(len(block) for block in blocks))
        self.assertEqual(4096, len(silent_block))
        self.assertEqual(bytes(4092), bytes(blocks[-1][4:]))

    def test_close_aborts_raw_stream_and_joins_generation_thread(self):
        blocking = BlockingTts()
        self.engine.close()
        self.engine = self.make_engine(blocking)
        self.engine.start()
        self.engine.speak(1, "关闭")
        self.assertTrue(blocking.entered.wait(1.0))
        blocking.release.set()

        self.engine.close()

        self.assertFalse(any(thread.is_alive() for thread in self.engine._generation_threads))

    def test_stream_owner_serializes_abort_and_close(self):
        abort_entered = threading.Event()
        release_abort = threading.Event()

        class GuardedStream(FakeRawStream):
            def __init__(stream_self, **kwargs):
                super().__init__(**kwargs)
                stream_self.abort_count = 0
                stream_self.close_count = 0
                stream_self.in_abort = False
                stream_self.concurrent_close = False

            def start(stream_self):
                return stream_self

            def abort(stream_self):
                stream_self.abort_count += 1
                stream_self.in_abort = True
                abort_entered.set()
                release_abort.wait(1.0)
                stream_self.in_abort = False

            def close(stream_self):
                stream_self.close_count += 1
                stream_self.concurrent_close |= stream_self.in_abort

        self.engine.close()
        self.engine = self.make_engine(self.tts, GuardedStream)
        self.engine.start()
        self.engine.speak(1, "播放")
        self.wait_for(lambda: bool(self.streams))
        self.engine.cancel(1)
        self.assertTrue(abort_entered.wait(1.0))
        close_thread = threading.Thread(target=self.engine.close)
        close_thread.start()
        time.sleep(0.02)
        release_abort.set()
        close_thread.join(1.0)

        stream = self.streams[0]
        self.assertEqual(1, stream.abort_count)
        self.assertEqual(1, stream.close_count)
        self.assertFalse(stream.concurrent_close)

    def test_build_sherpa_tts_validates_config_without_vits_data_dir(self):
        calls = {}

        class Config:
            def __init__(self, **kwargs):
                calls["config"] = kwargs

            def validate(self):
                calls["validated"] = True
                return True

        module = SimpleNamespace(
            OfflineTtsVitsModelConfig=lambda **kwargs: calls.setdefault("vits", kwargs),
            OfflineTtsModelConfig=lambda **kwargs: calls.setdefault("model", kwargs),
            OfflineTtsConfig=Config,
            OfflineTts=lambda config: ("tts", config),
        )
        with patch.dict("sys.modules", {"sherpa_onnx": module}):
            result = build_sherpa_tts(fake_paths(Path(self.temp.name)))

        self.assertEqual("tts", result[0])
        self.assertTrue(calls["validated"])
        self.assertEqual(1, calls["config"]["max_num_sentences"])
        self.assertNotIn("data_dir", calls["vits"])

    def test_build_sherpa_tts_rejects_invalid_config(self):
        class Config:
            def __init__(self, **kwargs):
                pass

            def validate(self):
                return False

        constructed = []
        module = SimpleNamespace(
            OfflineTtsVitsModelConfig=lambda **kwargs: kwargs,
            OfflineTtsModelConfig=lambda **kwargs: kwargs,
            OfflineTtsConfig=Config,
            OfflineTts=lambda config: constructed.append(config),
        )
        with patch.dict("sys.modules", {"sherpa_onnx": module}):
            with self.assertRaisesRegex(ValueError, "configuration is invalid"):
                build_sherpa_tts(fake_paths(Path(self.temp.name)))

        self.assertEqual([], constructed)

    def test_worker_source_does_not_require_numpy_or_sounddevice_play(self):
        import offline_speech_worker

        source = inspect.getsource(offline_speech_worker)
        self.assertNotIn("import numpy", source)
        self.assertNotIn("sounddevice.play", source)
        self.assertIn("RawOutputStream", source)

    def test_main_protocol_reports_error_for_invalid_command(self):
        input_stream = io.StringIO('{"command":"invalid"}\n{"command":"close"}\n')
        output_stream = io.StringIO()

        class FakeEngine:
            def __init__(self, paths, emit):
                self.emit = emit

            def start(self):
                self.emit({"event": "ready"})
                return True

            def close(self):
                return None

        result = run_protocol(
            fake_paths(Path(self.temp.name)),
            input_stream,
            output_stream,
            engine_factory=FakeEngine,
        )

        events = [json.loads(line) for line in output_stream.getvalue().splitlines()]
        self.assertEqual(0, result)
        self.assertEqual("ready", events[0]["event"])
        self.assertEqual("error", events[1]["event"])

    def test_native_non_utf8_output_is_separated_from_protocol_stdout(self):
        worker_dir = Path(__file__).parents[2] / "scripts" / "product"
        source = "\n".join(
            (
                "import os, sys",
                f"sys.path.insert(0, {str(worker_dir)!r})",
                "import offline_speech_worker as worker",
                "worker.resolve_offline_speech_paths = lambda root: object()",
                "def fake_run(paths, input_stream, output_stream):",
                "    os.write(1, b'\\x80native-log\\n')",
                "    worker._write_emitter(output_stream)({'event': 'finished', 'request_id': 7})",
                "    return 0",
                "worker.run_protocol = fake_run",
                "raise SystemExit(worker.main(['--bundle-root', '.']))",
            )
        )

        completed = subprocess.run(
            [sys.executable, "-c", source],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

        self.assertEqual(
            {"event": "finished", "request_id": 7},
            json.loads(completed.stdout.decode("utf-8")),
        )
        self.assertIn(b"\x80native-log", completed.stderr)

    def test_main_decodes_utf8_protocol_input_under_gbk_process_encoding(self):
        worker_dir = Path(__file__).parents[2] / "scripts" / "product"
        source = "\n".join(
            (
                "import json, sys",
                f"sys.path.insert(0, {str(worker_dir)!r})",
                "import offline_speech_worker as worker",
                "worker.resolve_offline_speech_paths = lambda root: object()",
                "def fake_run(paths, input_stream, output_stream):",
                "    command = json.loads(input_stream.readline())",
                "    worker._write_emitter(output_stream)({'event': 'received', 'text': command['text']})",
                "    return 0",
                "worker.run_protocol = fake_run",
                "raise SystemExit(worker.main(['--bundle-root', '.']))",
            )
        )
        command = json.dumps(
            {"command": "speak", "text": "正在为你查看可选英雄。"},
            ensure_ascii=False,
        ).encode("utf-8") + b"\n"
        environment = {**os.environ, "PYTHONIOENCODING": "gbk:surrogateescape"}

        completed = subprocess.run(
            [sys.executable, "-c", source],
            input=command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=True,
        )

        event = json.loads(completed.stdout.decode("utf-8"))
        self.assertEqual("正在为你查看可选英雄。", event["text"])


if __name__ == "__main__":
    unittest.main()

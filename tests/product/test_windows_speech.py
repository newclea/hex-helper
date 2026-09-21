import json
import queue
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from windows_speech import WindowsSpeechAdapter


class FakeOutput:
    def __init__(self, events=None):
        self._lines = queue.Queue()
        for event in events or []:
            self.put(event)

    def put(self, event):
        self._lines.put(json.dumps(event, ensure_ascii=False) + "\n")

    def readline(self):
        return self._lines.get()

    def close(self):
        self._lines.put("")


class FakeInput:
    def __init__(self, process, broken=False):
        self._process = process
        self._broken = broken
        self.closed = False

    def write(self, line):
        if self._broken:
            raise BrokenPipeError("worker stopped")
        self._process.raw_lines.append(line)
        message = json.loads(line)
        self._process.writes.append(message)
        if message["command"] == "speak" and self._process.auto_finish:
            self._process.stdout.put({"event": "started"})
            self._process.stdout.put({"event": "finished"})

    def flush(self):
        if self._broken:
            raise BrokenPipeError("worker stopped")

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, events=None, wait_timeout=False, broken=False, auto_finish=False):
        self.stdout = FakeOutput(events)
        self.stdin = FakeInput(self, broken)
        self.writes = []
        self.raw_lines = []
        self.wait_timeout = wait_timeout
        self.auto_finish = auto_finish
        self.returncode = None
        self.terminate_count = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.wait_timeout and self.returncode is None:
            raise subprocess.TimeoutExpired("powershell", timeout)
        self.returncode = 0 if self.returncode is None else self.returncode
        self.stdout.close()
        return self.returncode

    def terminate(self):
        self.terminate_count += 1
        self.returncode = -15
        self.stdout.close()


class WindowsSpeechAdapterTests(unittest.TestCase):
    def make_adapter(self, process):
        factory = Mock(return_value=process)
        adapter = WindowsSpeechAdapter(process_factory=factory)
        return adapter, factory

    def test_start_reuses_one_process_and_writes_unicode_json_lines(self):
        process = FakeProcess([{"event": "ready"}])
        adapter, factory = self.make_adapter(process)
        self.addCleanup(adapter.close)

        self.assertTrue(adapter.start())
        self.assertTrue(adapter.speak("第一条"))
        self.assertTrue(adapter.speak("第二条"))

        factory.assert_called_once()
        self.assertEqual(["第一条", "第二条"], [item["text"] for item in process.writes])
        self.assertIn("第一条", process.raw_lines[0])
        args, kwargs = factory.call_args
        self.assertIn("-NoProfile", args[0])
        self.assertIn("-NonInteractive", args[0])
        self.assertIn("-File", args[0])
        self.assertEqual("utf-8", kwargs["encoding"])

    def test_wait_finished_handles_finished_and_error_events(self):
        process = FakeProcess([{"event": "ready"}], auto_finish=True)
        adapter, _ = self.make_adapter(process)
        self.addCleanup(adapter.close)

        self.assertTrue(adapter.speak("完成"))
        self.assertTrue(adapter.wait_finished(0.2))
        process.auto_finish = False
        self.assertTrue(adapter.speak("失败"))
        process.stdout.put({"event": "error", "message": "speech failed"})
        self.assertFalse(adapter.wait_finished(0.2))

    def test_cancel_sends_command_without_closing_process(self):
        process = FakeProcess([{"event": "ready"}])
        adapter, _ = self.make_adapter(process)
        self.addCleanup(adapter.close)

        self.assertTrue(adapter.start())
        adapter.cancel()

        self.assertIn({"command": "cancel"}, process.writes)
        self.assertIsNone(process.returncode)

    def test_ready_timeout_returns_false_and_terminates(self):
        process = FakeProcess()
        adapter, _ = self.make_adapter(process)

        with patch("windows_speech.READY_TIMEOUT_SECONDS", 0.01):
            self.assertFalse(adapter.start())

        self.assertEqual(1, process.terminate_count)

    def test_missing_executable_returns_false(self):
        factory = Mock(side_effect=OSError("powershell missing"))
        adapter = WindowsSpeechAdapter(process_factory=factory)

        self.assertFalse(adapter.start())
        adapter.cancel()
        adapter.close()

    def test_broken_pipe_returns_false_without_raising(self):
        process = FakeProcess([{"event": "ready"}], broken=True)
        adapter, _ = self.make_adapter(process)
        self.addCleanup(adapter.close)

        self.assertFalse(adapter.speak("断管"))
        self.assertFalse(adapter.wait_finished(0.01))

    def test_invalid_event_fails_current_wait_without_raising(self):
        process = FakeProcess([{"event": "ready"}])
        adapter, _ = self.make_adapter(process)
        self.addCleanup(adapter.close)
        self.assertTrue(adapter.start())

        process.stdout.put({"event": "unexpected"})

        self.assertFalse(adapter.wait_finished(0.2))

    def test_malformed_ready_event_fails_start(self):
        process = FakeProcess([{"not_event": "ready"}])
        adapter, _ = self.make_adapter(process)

        self.assertFalse(adapter.start())
        self.assertEqual(1, process.terminate_count)

    def test_close_waits_two_seconds_then_terminates_once(self):
        process = FakeProcess([{"event": "ready"}], wait_timeout=True)
        adapter, _ = self.make_adapter(process)
        self.assertTrue(adapter.start())

        adapter.close()
        adapter.close()

        self.assertEqual(1, process.writes.count({"command": "close"}))
        self.assertEqual(1, process.terminate_count)

    def test_old_reader_cannot_terminate_or_complete_new_process(self):
        first = FakeProcess([{"event": "ready"}])
        second = FakeProcess([{"event": "ready"}], auto_finish=True)
        factory = Mock(side_effect=[first, second])
        adapter = WindowsSpeechAdapter(process_factory=factory)
        self.addCleanup(adapter.close)
        self.assertTrue(adapter.start())

        first.returncode = 1
        self.assertTrue(adapter.start())
        first.stdout.put({"event": "finished"})
        self.assertFalse(adapter.wait_finished(0.01))
        first.stdout.close()

        self.assertTrue(adapter.speak("新进程"))
        self.assertTrue(adapter.wait_finished(0.2))
        self.assertEqual(0, second.terminate_count)
        self.assertEqual(2, factory.call_count)


class WindowsSpeechWorkerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        worker = Path(__file__).parents[2] / "scripts" / "product" / "windows_speech_worker.ps1"
        cls.source = worker.read_text("utf-8")

    def command_body(self, command, next_command=None):
        start = self.source.index(f'"{command}" {{')
        end = self.source.index(f'"{next_command}" {{', start) if next_command else len(self.source)
        return self.source[start:end]

    def test_voice_selection_prefers_configured_then_chinese_female_then_default(self):
        configured = self.source.index("if ($ConfiguredVoice)")
        chinese = self.source.index('$_.VoiceInfo.Culture.Name -eq "zh-CN"')
        self.assertLess(configured, chinese)
        self.assertEqual(2, self.source.count("$Synthesizer.SelectVoice("))

    def test_speak_is_async_without_automatic_cancel(self):
        speak = self.command_body("speak", "cancel")
        self.assertIn("SpeakAsync(", speak)
        self.assertNotIn("SpeakAsyncCancelAll", speak)

    def test_stdout_is_only_written_by_json_event_function(self):
        self.assertEqual(1, self.source.count("[Console]::Out.WriteLine"))
        self.assertGreaterEqual(self.source.count("Write-SpeechJsonEvent"), 4)

    def test_cancel_and_close_contract(self):
        cancel = self.command_body("cancel", "close")
        close = self.command_body("close")
        self.assertIn("SpeakAsyncCancelAll", cancel)
        self.assertIn("SpeakAsyncCancelAll", close)
        self.assertIn("Dispose", close)


if __name__ == "__main__":
    unittest.main()

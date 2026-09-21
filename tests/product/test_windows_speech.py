import json
import queue
import subprocess
import unittest
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


if __name__ == "__main__":
    unittest.main()

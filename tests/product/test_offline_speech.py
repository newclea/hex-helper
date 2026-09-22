from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from offline_speech import OfflineSpeechAdapter


class FakeOutput:
    def __init__(self, events=None):
        self._lines = queue.Queue()
        for event in events or []:
            self.put(event)

    def put(self, event):
        self._lines.put(json.dumps(event, ensure_ascii=False) + "\n")

    def put_raw(self, line):
        self._lines.put(line)

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
            request_id = message["request_id"]
            self._process.stdout.put({"event": "started", "request_id": request_id})
            self._process.stdout.put({"event": "finished", "request_id": request_id})

    def flush(self):
        if self._broken:
            raise BrokenPipeError("worker stopped")

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(
        self,
        events=None,
        wait_timeout=False,
        broken=False,
        auto_finish=False,
        terminate_stuck=False,
    ):
        self.stdout = FakeOutput(events)
        self.stdin = FakeInput(self, broken)
        self.writes = []
        self.raw_lines = []
        self.wait_timeout = wait_timeout
        self.auto_finish = auto_finish
        self.terminate_stuck = terminate_stuck
        self.returncode = None
        self.terminate_count = 0
        self.kill_count = 0
        self.wait_count = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_count += 1
        if self.wait_timeout and self.returncode is None:
            raise subprocess.TimeoutExpired("offline-worker", timeout)
        if self.terminate_stuck and self.terminate_count and not self.kill_count:
            raise subprocess.TimeoutExpired("offline-worker", timeout)
        self.returncode = 0 if self.returncode is None else self.returncode
        self.stdout.close()
        return self.returncode

    def terminate(self):
        self.terminate_count += 1
        if not self.terminate_stuck:
            self.returncode = -15
            self.stdout.close()

    def kill(self):
        self.kill_count += 1
        self.returncode = -9
        self.stdout.close()


class OfflineSpeechAdapterTests(unittest.TestCase):
    def make_adapter(self, process, **kwargs):
        factory = Mock(return_value=process)
        adapter = OfflineSpeechAdapter(process_factory=factory, **kwargs)
        return adapter, factory

    def test_start_reuses_process_and_retains_unicode_json(self):
        process = FakeProcess([{"event": "ready"}])
        adapter, factory = self.make_adapter(process)
        self.addCleanup(adapter.close)
        self.assertTrue(adapter.start())
        self.assertTrue(adapter.speak("第一条"))
        self.assertTrue(adapter.speak("第二条"))
        factory.assert_called_once()
        self.assertEqual(["第一条", "第二条"], [item["text"] for item in process.writes])
        self.assertIn("第一条", process.raw_lines[0])
        self.assertEqual([1, 2], [item["request_id"] for item in process.writes])
        self.assertEqual("utf-8", factory.call_args.kwargs["encoding"])

    def test_default_command_uses_python_worker_and_bundle_root(self):
        process = FakeProcess([{"event": "ready"}])
        bundle_root = Path("C:/gamebuddy")
        adapter, factory = self.make_adapter(process, bundle_root=bundle_root)
        self.addCleanup(adapter.close)
        self.assertTrue(adapter.start())
        command = factory.call_args.args[0]
        self.assertEqual(sys.executable, command[0])
        self.assertTrue(command[1].endswith("offline_speech_worker.py"))
        self.assertEqual(["--bundle-root", str(bundle_root)], command[2:])

    def test_wait_finished_handles_finished_error_and_cancelled(self):
        process = FakeProcess([{"event": "ready"}], auto_finish=True)
        adapter, _ = self.make_adapter(process)
        self.addCleanup(adapter.close)
        self.assertTrue(adapter.speak("完成"))
        self.assertTrue(adapter.wait_finished(0.2))
        process.auto_finish = False
        self.assertTrue(adapter.speak("失败"))
        process.stdout.put({"event": "error", "request_id": 2, "message": "failed"})
        self.assertFalse(adapter.wait_finished(0.2))
        self.assertFalse(adapter.speak("会话已停用"))

    def test_wait_started_tracks_real_worker_started_event(self):
        process = FakeProcess([{"event": "ready"}])
        adapter, _ = self.make_adapter(process)
        self.addCleanup(adapter.close)
        self.assertTrue(adapter.speak("开始播放"))
        self.assertIsNone(adapter.wait_started(0.01))

        process.stdout.put({"event": "started", "request_id": 1})

        self.assertTrue(adapter.wait_started(0.2))

    def test_worker_error_logs_cause_and_disables_session(self):
        process = FakeProcess([{"event": "ready"}])
        adapter, factory = self.make_adapter(process)
        self.assertTrue(adapter.speak("失败"))

        with self.assertLogs("offline_speech", level="WARNING") as captured:
            process.stdout.put({
                "event": "error",
                "request_id": 1,
                "message": "audio device unavailable",
            })
            self.assertFalse(adapter.wait_finished(0.2))

        self.assertIn("audio device unavailable", "\n".join(captured.output))
        deadline = time.monotonic() + 0.5
        while process.terminate_count == 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(1, process.terminate_count)
        self.assertFalse(adapter.speak("不应重试"))
        factory.assert_called_once()

    def test_startup_error_logs_cause_and_disables_session(self):
        process = FakeProcess([{
            "event": "error",
            "message": "model file is missing",
        }])
        adapter, factory = self.make_adapter(process)

        with self.assertLogs("offline_speech", level="WARNING") as captured:
            self.assertFalse(adapter.start())

        self.assertIn("model file is missing", "\n".join(captured.output))
        self.assertFalse(adapter.start())
        factory.assert_called_once()

    def test_cancelled_event_returns_false(self):
        process = FakeProcess([{"event": "ready"}])
        adapter, _ = self.make_adapter(process)
        self.addCleanup(adapter.close)
        self.assertTrue(adapter.speak("取消"))
        process.stdout.put({"event": "cancelled", "request_id": 1})
        self.assertFalse(adapter.wait_finished(0.2))

    def test_cancel_is_request_scoped_and_stale_finish_is_ignored(self):
        process = FakeProcess([{"event": "ready"}])
        adapter, _ = self.make_adapter(process)
        self.addCleanup(adapter.close)
        self.assertTrue(adapter.speak("旧请求"))
        adapter.cancel()
        self.assertIn({"command": "cancel", "request_id": 1}, process.writes)
        self.assertTrue(adapter.speak("新请求"))
        process.stdout.put({"event": "finished", "request_id": 1})
        self.assertIsNone(adapter.wait_finished(0.05))
        process.stdout.put({"event": "finished", "request_id": 2})
        self.assertTrue(adapter.wait_finished(0.2))

    def test_ready_timeout_returns_false_and_terminates(self):
        process = FakeProcess()
        adapter, _ = self.make_adapter(process)
        with patch("offline_speech.READY_TIMEOUT_SECONDS", 0.01):
            self.assertFalse(adapter.start())
        self.assertEqual(1, process.terminate_count)

    def test_close_wakes_start_waiter_without_ready_timeout_delay(self):
        process = FakeProcess()
        adapter, factory = self.make_adapter(process)
        result = []
        thread = threading.Thread(target=lambda: result.append(adapter.start()))
        started_at = time.monotonic()
        thread.start()
        deadline = started_at + 0.5
        while factory.call_count == 0 and time.monotonic() < deadline:
            time.sleep(0.005)

        adapter.close()
        thread.join(0.5)

        self.assertFalse(thread.is_alive())
        self.assertEqual([False], result)
        self.assertEqual(1, process.terminate_count)
        self.assertLess(time.monotonic() - started_at, 1.0)

    def test_close_wakes_completion_waiter(self):
        process = FakeProcess([{"event": "ready"}])
        adapter, _ = self.make_adapter(process)
        self.assertTrue(adapter.speak("等待关闭"))
        result = []
        thread = threading.Thread(target=lambda: result.append(adapter.wait_finished()))
        thread.start()

        adapter.close()
        thread.join(0.5)

        self.assertFalse(thread.is_alive())
        self.assertEqual([False], result)

    def test_disabled_environment_does_not_spawn(self):
        factory = Mock()
        adapter = OfflineSpeechAdapter(process_factory=factory)
        with patch.dict("os.environ", {"GAMEBUDDY_OFFLINE_SPEECH_DISABLED": "1"}):
            self.assertFalse(adapter.start())
        factory.assert_not_called()

    def test_missing_executable_and_broken_pipe_return_false(self):
        missing = OfflineSpeechAdapter(process_factory=Mock(side_effect=OSError("missing")))
        self.assertFalse(missing.start())
        missing.close()
        process = FakeProcess([{"event": "ready"}], broken=True)
        broken, _ = self.make_adapter(process)
        self.addCleanup(broken.close)
        self.assertFalse(broken.speak("断管"))
        self.assertFalse(broken.wait_finished(0.01))

    def test_malformed_and_unknown_events_fail_current_wait(self):
        for event in ("not json\n", json.dumps({"event": "unexpected"}) + "\n"):
            with self.subTest(event=event):
                process = FakeProcess([{"event": "ready"}])
                adapter, _ = self.make_adapter(process)
                self.assertTrue(adapter.speak("测试"))
                process.stdout.put_raw(event)
                self.assertFalse(adapter.wait_finished(0.2))
                adapter.close()

    def test_close_is_idempotent_and_terminates_stuck_worker(self):
        process = FakeProcess(
            [{"event": "ready"}],
            wait_timeout=True,
            terminate_stuck=True,
        )
        adapter, _ = self.make_adapter(process)
        self.assertTrue(adapter.start())
        adapter.close()
        adapter.close()
        self.assertEqual(1, process.writes.count({"command": "close"}))
        self.assertEqual(1, process.terminate_count)
        self.assertEqual(1, process.kill_count)
        self.assertGreaterEqual(process.wait_count, 3)
        self.assertTrue(process.stdin.closed)


if __name__ == "__main__":
    unittest.main()

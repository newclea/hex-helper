"""Real model/cache, real-time silent output sink (does not test audio hardware)."""
import json
import sys
import threading
import time
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'scripts/product'))
from offline_speech_assets import resolve_offline_speech_paths
from offline_speech_worker import (OfflineSpeechEngine, PlaybackComplete, PlaybackAbort,
                                   initialize_native_dependencies)
from speech_policy import STARTUP_GREETING, WIN_GREETING, LOSS_GREETING


class SilentStream:
    def __init__(self, samplerate, callback, finished_callback, **kwargs):
        self.rate, self.callback, self.finished = samplerate, callback, finished_callback
        self.stop = threading.Event()

    def start(self):
        def consume():
            try:
                while not self.stop.is_set():
                    self.callback(bytearray(4096), 1024, None, None)
                    self.stop.wait(1024 / self.rate)
            except (PlaybackComplete, PlaybackAbort):
                pass
            finally:
                self.finished()
        self.thread = threading.Thread(target=consume, daemon=True)
        self.thread.start()

    def abort(self):
        self.stop.set()

    def close(self):
        self.stop.set()
        self.thread.join(1)


started = time.perf_counter()
events = []
done = threading.Event()
requested = {}
def emit(event):
    now = time.perf_counter()
    record = {**event, 'from_launch_seconds': round(now - started, 4)}
    if event.get('request_id') in requested:
        record['from_request_seconds'] = round(now - requested[event['request_id']], 4)
    events.append(record)
    print(json.dumps(record), flush=True)
    if event['event'] in ('finished', 'error'):
        done.set()

initialize_native_dependencies()
engine = OfflineSpeechEngine(resolve_offline_speech_paths(root), emit,
                             raw_output_stream_factory=SilentStream)
try:
    assert engine.start()
    for request_id, text in enumerate((STARTUP_GREETING, WIN_GREETING, LOSS_GREETING,
            '根据当前英雄强度，推荐选择妮蔻、亚索、盖伦三个英雄哦。'), 1):
        done.clear()
        requested[request_id] = time.perf_counter()
        assert engine.speak(request_id, text)
        assert done.wait(30)
        assert not any(e['event'] == 'error' for e in events)
finally:
    engine.close()
    Path(__file__).with_name('results').joinpath('pipeline-probe.json').write_text(
        json.dumps({'output': 'real-time silent sink; no hardware playback', 'events': events},
                   indent=2), encoding='utf-8')

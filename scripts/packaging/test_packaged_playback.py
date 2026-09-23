"""Explicit audible test of the shipped voice worker (fixed and dynamic speech)."""
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

root = Path(sys.argv[1]).resolve()
report = Path(sys.argv[2]).resolve()
report.parent.mkdir(parents=True, exist_ok=True)
events = []
incoming = queue.Queue()
start = time.monotonic()
with report.with_suffix('.stderr.log').open('wb') as errors:
    process = subprocess.Popen([str(root / 'OfflineSpeechWorker.exe'), '--bundle-root', str(root / '_internal')],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
        text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
    def read():
        for line in process.stdout:
            incoming.put(json.loads(line))
        incoming.put({'event': 'unexpected_exit'})
    threading.Thread(target=read, daemon=True).start()
    def expect(name, request_id=None):
        while True:
            event = incoming.get(timeout=45)
            events.append({**event, 'seconds': round(time.monotonic() - start, 4)})
            if event['event'] in ('error', 'unexpected_exit'):
                raise RuntimeError(event)
            if event['event'] == name and event.get('request_id') == request_id:
                return
    def send(command):
        process.stdin.write(json.dumps(command, ensure_ascii=False) + '\n')
        process.stdin.flush()
    ok = False
    try:
        expect('ready')
        for request_id, text in enumerate(('耶，赢啦！', '你好，悠米语音播放测试完成。'), 1):
            send({'command': 'speak', 'request_id': request_id, 'text': text})
            expect('started', request_id)
            expect('finished', request_id)
        ok = True
    finally:
        if process.poll() is None:
            send({'command': 'close'})
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        report.write_text(json.dumps({'ok': ok, 'audio_played': True, 'events': events}, indent=2), encoding='utf-8')
print(report)

"""Verify the recommended FP32 model through the real worker audio engine."""
import json
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'scripts/product'))
import numpy
import sounddevice
import sherpa_onnx
from offline_speech_assets import resolve_offline_speech_paths
from offline_speech_worker import OfflineSpeechEngine

paths = replace(resolve_offline_speech_paths(root), model=Path(__file__).parent / 'models/vits-melo-tts-zh_en/model.onnx')

def factory(paths):
    return sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(paths.model), tokens=str(paths.tokens), lexicon=str(paths.lexicon)),
            num_threads=8, provider='cpu'),
        rule_fsts=','.join(str(p) for p in paths.rule_fsts), max_num_sentences=1))

events = []
done = threading.Event()
start = time.perf_counter()
def emit(event):
    item = {**event, 'elapsed_seconds': time.perf_counter() - start}
    events.append(item)
    print(json.dumps(item), flush=True)
    if event['event'] in ('finished', 'error'):
        done.set()

engine = OfflineSpeechEngine(paths, emit, tts_factory=factory, prewarm_texts=())
try:
    assert engine.start()
    start = time.perf_counter()
    assert engine.speak(1, '根据当前英雄强度，推荐选择妮蔻、亚索、盖伦三个英雄哦。')
    assert done.wait(20)
    assert any(e['event'] == 'started' for e in events)
    assert any(e['event'] == 'finished' for e in events)
    assert not any(e['event'] == 'error' for e in events)
finally:
    engine.close()
    (Path(__file__).parent / 'results/playback-probe.json').write_text(
        json.dumps(events, indent=2), encoding='utf-8')

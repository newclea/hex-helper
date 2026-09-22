"""Run one isolated CPU TTS benchmark; no cache and no audio playback."""
import argparse
import hashlib
import json
import re
import time
import wave
from pathlib import Path

import numpy as np
import sherpa_onnx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--piper', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    resources = args.resources
    options = {'model': str(args.model), 'tokens': str(resources / 'tokens.txt')}
    options['lexicon'] = str(resources / 'lexicon.txt')
    rules = ','.join(str(resources / name) for name in
                     ['date.fst', 'new_heteronym.fst', 'number.fst', 'phone.fst']
                     if (resources / name).exists())
    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(**options),
            num_threads=args.threads, provider='cpu', debug=False),
        rule_fsts=rules, max_num_sentences=1)
    assert config.validate()
    start = time.perf_counter()
    tts = sherpa_onnx.OfflineTts(config)
    load_seconds = time.perf_counter() - start
    with args.model.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    metadata = {'name': args.name, 'model': str(args.model.resolve()),
                'model_bytes': args.model.stat().st_size, 'sha256': digest,
                'threads': args.threads, 'provider': 'cpu',
                'load_seconds': load_seconds, 'sample_rate': tts.sample_rate,
                'runtime': sherpa_onnx.__version__, 'numpy': np.__version__}
    print(json.dumps({'metadata': metadata}), flush=True)
    texts = [
        ('champions', '根据当前英雄强度，推荐选择妮蔻、亚索、盖伦三个英雄哦。'),
        ('hex', '推荐选择珠光护手，当前玩法胜率优先。'),
        ('greeting', '召唤师你好，我是你的联盟专属陪玩悠米！快去开启一场紧张刺激的海克斯大乱斗吧。'),
    ]
    rows = []
    for trial in (1, 2):
        for kind, text in texts:
            chunks = []
            buffers = []
            begin = time.perf_counter()
            def receive(samples, progress):
                chunks.append({'ready_seconds': time.perf_counter() - begin,
                               'audio_seconds': len(samples) / tts.sample_rate})
                return 1
            # Match the app's punctuation-based segmented synthesis.
            for segment in re.findall(r'[^，,。！？!?；;]+[，,。！？!?；;]?', text):
                audio = tts.generate(segment, sid=0, speed=1.0, callback=receive)
                buffers.append(np.asarray(audio.samples, dtype=np.float32).copy())
            elapsed = time.perf_counter() - begin
            samples = np.concatenate(buffers)
            duration = len(samples) / tts.sample_rate
            previous_audio = 0.0
            continuous_start = 0.0
            for chunk in chunks:
                continuous_start = max(continuous_start, chunk['ready_seconds'] - previous_audio)
                previous_audio += chunk['audio_seconds']
            row = {'kind': kind, 'trial': trial, 'text': text,
                   'chinese_chars': len(re.findall(r'[\u4e00-\u9fff]', text)),
                   'compute_seconds': elapsed, 'audio_seconds': duration,
                   'rtf': elapsed / duration,
                   'first_chunk_seconds': chunks[0]['ready_seconds'] if chunks else None,
                   'continuous_start_seconds': continuous_start, 'chunks': chunks,
                   'rms': float(np.sqrt(np.mean(samples ** 2))),
                   'peak': float(np.max(np.abs(samples)))}
            rows.append(row)
            if trial == 2:
                with wave.open(str(args.output / f'{args.name}-{kind}.wav'), 'wb') as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(tts.sample_rate)
                    wav.writeframes((np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes())
            (args.output / f'{args.name}.json').write_text(
                json.dumps({'metadata': metadata, 'results': rows}, ensure_ascii=False, indent=2),
                encoding='utf-8')
            print(json.dumps({k: v for k, v in row.items() if k not in ['text', 'chunks']}), flush=True)


if __name__ == '__main__':
    main()

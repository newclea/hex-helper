from pathlib import Path

root = Path(SPECPATH).resolve().parents[1]
paths = [str(root / 'scripts' / name) for name in ('packaging', 'product', 'recognition_overlay', 'phase4')]
data = [(str(root / name), name) for name in ('assets', 'data', 'vendor/speech/wheels/cp311-win_amd64', 'third_party/speech/licenses')]
data = [(source, target) for source, target in data if Path(source).exists()]
data.append((str(root / 'BUILD.txt'), '.'))
engine = str(root / 'outputs/tmp/build/bin/lol_augment_assistant.exe')

app = Analysis([str(root / 'scripts/packaging/app_entry.py')], pathex=paths,
               binaries=[(engine, '.')], datas=data, hiddenimports=['_cffi_backend'],
               excludes=[], noarchive=False)
voice = Analysis([str(root / 'scripts/packaging/worker_entry.py')], pathex=paths,
                 hiddenimports=['_cffi_backend'], noarchive=False)
app_exe = EXE(PYZ(app.pure), app.scripts, [], exclude_binaries=True,
              name='GameBuddy', console=False, upx=False)
voice_exe = EXE(PYZ(voice.pure), voice.scripts, [], exclude_binaries=True,
                name='OfflineSpeechWorker', console=True, upx=False)
COLLECT(app_exe, voice_exe, app.binaries, app.datas, voice.binaries, voice.datas,
        name='GameBuddy', upx=False)

"""Extract a release outside the source tree and test with clean user/environment state."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

archive = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2]).resolve()
sandbox = Path(tempfile.mkdtemp(prefix='GameBuddy-portable-'))
with zipfile.ZipFile(archive) as bundle:
    for entry in bundle.infolist():
        target = (sandbox / entry.filename).resolve()
        if not target.is_relative_to(sandbox):
            raise ValueError('Archive contains unsafe path')
    bundle.extractall(sandbox)
root = sandbox / 'GameBuddy'
manifest = json.loads((root / 'PACKAGE-SHA256.json').read_text(encoding='utf-8'))
for relative, expected in manifest.items():
    with (root / relative).open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == expected, relative
environment = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(('PYTHON', '_PYI', 'PYINSTALLER', 'LOL_ASSISTANT', 'GAMEBUDDY'))
               and key.upper() not in ('VIRTUAL_ENV', 'CONDA_PREFIX')}
environment['PATH'] = os.path.join(environment['SYSTEMROOT'], 'System32')
environment['LOCALAPPDATA'] = str(sandbox / 'fresh-local-appdata')
environment['APPDATA'] = str(sandbox / 'fresh-appdata')
report = sandbox / 'diagnostic.json'
result = subprocess.run([str(root / 'GameBuddy.exe'), '--self-test', str(report)],
                        cwd=sandbox, env=environment, capture_output=True, timeout=120,
                        creationflags=subprocess.CREATE_NO_WINDOW)
check = json.loads(report.read_text(encoding='utf-8')) if report.exists() else {}
voice_path = report.with_name('diagnostic-voice.json')
voice = json.loads(voice_path.read_text(encoding='utf-8')) if voice_path.exists() else {}
summary = {'ok': result.returncode == 0 and check.get('ok') is True,
           'extracted_to': str(sandbox), 'verified_files': len(manifest),
           'path': environment['PATH'], 'fresh_user_config': True,
           'exit_code': result.returncode, 'app': check, 'voice': voice,
           'stderr': result.stderr.decode('utf-8', errors='replace')}
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=True, indent=2))
raise SystemExit(0 if summary['ok'] else 1)

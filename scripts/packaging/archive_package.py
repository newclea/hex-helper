"""Create a portable ZIP and an integrity manifest without local configuration."""
import hashlib
import importlib.metadata
import json
import shutil
import sys
import zipfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
license_root = root / 'licenses'
license_root.mkdir(exist_ok=True)
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if python_license.is_file():
    shutil.copy2(python_license, license_root / 'Python-LICENSE.txt')
for name in ('numpy', 'sherpa-onnx', 'sherpa-onnx-core', 'sounddevice', 'cffi', 'pycparser', 'pyinstaller'):
    distribution = importlib.metadata.distribution(name)
    for relative in distribution.files or ():
        if relative.name.lower().startswith(('license', 'copying', 'notice')):
            source = Path(distribution.locate_file(relative))
            if source.is_file():
                target = license_root / name / relative.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
for relative in ('tcl/tcl8.6/license.terms', 'tcl/tk8.6/license.terms'):
    source = Path(sys.base_prefix) / relative
    if source.is_file():
        shutil.copy2(source, license_root / (source.parent.name + '-license.terms'))
files = sorted(p for p in root.rglob('*') if p.is_file() and p.name != 'PACKAGE-SHA256.json')
assert not any(p.name in {'overlay.json', 'txtt.txt'} for p in files), 'Local config in package'
manifest = {}
for path in files:
    with path.open('rb') as stream:
        manifest[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
manifest_path = root / 'PACKAGE-SHA256.json'
manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
archive = root.with_name('GameBuddy-Windows-x64.zip')
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as output:
    for path in [*files, manifest_path]:
        output.write(path, path.relative_to(root.parent))
with archive.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
print(archive)

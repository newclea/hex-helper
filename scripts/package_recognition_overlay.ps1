[CmdletBinding()]
param([switch]$SkipInstall)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$buildRoot = Join-Path $repoRoot 'outputs\local-build'
$environmentRoot = Join-Path $buildRoot 'package-venv'
$python = Join-Path $environmentRoot 'Scripts\python.exe'
$distRoot = Join-Path $buildRoot 'dist'
$workRoot = Join-Path $buildRoot 'pyinstaller'
$wheelRoot = Join-Path $repoRoot 'vendor\speech\wheels\cp311-win_amd64'
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null

function Assert-NativeSuccess([string]$Action) {
    if ($LASTEXITCODE -ne 0) { throw "$Action failed: exit $LASTEXITCODE" }
}

if (-not (Test-Path -LiteralPath $python)) {
    & py -3.11 -m venv $environmentRoot
    Assert-NativeSuccess 'Create isolated build environment'
}
if (-not $SkipInstall) {
    & $python -m pip install -r (Join-Path $PSScriptRoot 'packaging\requirements-build.txt')
    Assert-NativeSuccess 'Install build tools'
    & $python -m pip install --no-index --no-deps --find-links $wheelRoot `
        sherpa-onnx==1.13.8 sherpa-onnx-core==1.13.8 numpy==1.26.4 `
        sounddevice==0.5.3 cffi==2.1.1 pycparser==3.0
    Assert-NativeSuccess 'Install bundled runtime dependencies'
}
& $python -B (Join-Path $repoRoot 'scripts\product\offline_speech_assets.py') --verify --bundle-root $repoRoot
Assert-NativeSuccess 'Verify speech resources'

# PyInstaller may replace its own output directories. They are fixed paths
# beneath outputs/local-build; never accept an arbitrary deletion target.
& $python -m PyInstaller --noconfirm --distpath $distRoot --workpath $workRoot `
    (Join-Path $PSScriptRoot 'packaging\GameBuddy.spec')
Assert-NativeSuccess 'Build desktop and voice executables'
$appRoot = Join-Path $distRoot 'GameBuddy'
Copy-Item -LiteralPath (Join-Path $repoRoot 'docs\portable-app-readme.txt') `
    -Destination (Join-Path $appRoot '使用说明.txt')
& $python -m pip freeze | Set-Content -LiteralPath (Join-Path $appRoot 'BUILD-DEPENDENCIES.txt') -Encoding UTF8

$diagnostic = Join-Path $buildRoot 'package-self-test.json'
$process = Start-Process -FilePath (Join-Path $appRoot 'GameBuddy.exe') `
    -ArgumentList @('--self-test', ('"' + $diagnostic + '"')) -PassThru -WindowStyle Hidden
if (-not $process.WaitForExit(120000)) { $process.Kill(); throw 'Package self-test timed out' }
if ($process.ExitCode -ne 0) { throw "Package self-test failed; see $diagnostic" }
$check = Get-Content -LiteralPath $diagnostic -Raw | ConvertFrom-Json
if (-not $check.ok) { throw "Package self-test failed; see $diagnostic" }
& $python (Join-Path $PSScriptRoot 'packaging\archive_package.py') $appRoot
Assert-NativeSuccess 'Create distributable ZIP'
Write-Host "Ready: $appRoot"

[CmdletBinding()]
param(
    [string]$LeagueRoot = '',
    [string]$VisionExe = '',
    [ValidateSet('KIWI', 'KIWI_JADE')]
    [string]$Mode = 'KIWI',
    [ValidateRange(1, 86400)]
    [int]$MaxSeconds = 86400,
    [ValidateSet('', 'game-result', 'speech')]
    [string]$DebugSubmode = '',
    [switch]$LegacyUi
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$app = Join-Path $workspaceRoot 'scripts\recognition_overlay\app.py'
$assetVerifier = Join-Path $workspaceRoot 'scripts\product\offline_speech_assets.py'
$wheelDirectory = Join-Path $workspaceRoot 'vendor\speech\wheels\cp311-win_amd64'

function Resolve-Python {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $py) {
        return @{ File = $py.Source; Prefix = @('-3.11', '-B') }
    }
    foreach ($name in @('python', 'python3')) {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $found) {
            return @{ File = $found.Source; Prefix = @('-B') }
        }
    }
    throw 'Python was not found. Install Python 3.11 or run scripts/package_recognition_overlay.ps1 first.'
}

$python = Resolve-Python

function Test-SpeechBundle {
    $verifyArguments = @()
    $verifyArguments += $python.Prefix
    $verifyArguments += @($assetVerifier, '--verify', '--bundle-root', $workspaceRoot)
    & $python.File @verifyArguments
    return $LASTEXITCODE -eq 0
}

function Initialize-SpeechRuntime {
    $runtimeParent = Join-Path $env:LOCALAPPDATA 'LoLRecognitionOverlay\speech-runtime'
    $runtimeDirectory = Join-Path $runtimeParent '1.13.8-py311'
    $successStamp = Join-Path $runtimeDirectory '.installed'
    if (-not (Test-Path -LiteralPath $successStamp -PathType Leaf)) {
        New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
        $installArguments = @()
        $installArguments += $python.Prefix
        $installArguments += @(
            '-m', 'pip', 'install', '--no-index', '--disable-pip-version-check',
            '--no-deps', '--find-links', $wheelDirectory, '--target', $runtimeDirectory,
            'sherpa-onnx==1.13.8', 'sherpa-onnx-core==1.13.8',
            'sounddevice==0.5.3', 'cffi==2.1.1', 'pycparser==3.0'
        )
        & $python.File @installArguments | Out-Host
        if ($LASTEXITCODE -ne 0) {
            return $null
        }
        Set-Content -LiteralPath $successStamp -Value 'verified' -Encoding Ascii
    }
    return $runtimeDirectory
}

$speechRuntime = $null
try {
    if (Test-SpeechBundle) {
        $speechRuntime = Initialize-SpeechRuntime
    }
} catch {
    Write-Warning "Offline speech preparation failed: $($_.Exception.Message)"
}
if ($null -eq $speechRuntime) {
    $env:GAMEBUDDY_OFFLINE_SPEECH_DISABLED = '1'
    Write-Warning 'Offline speech is unavailable; GameBuddy will continue without speech.'
} else {
    $existingPythonPath = $env:PYTHONPATH
    if ([string]::IsNullOrWhiteSpace($existingPythonPath)) {
        $env:PYTHONPATH = $speechRuntime
    } else {
        $env:PYTHONPATH = "$speechRuntime;$existingPythonPath"
    }
}

$arguments = @()
$arguments += $python.Prefix
$arguments += $app
if (-not [string]::IsNullOrWhiteSpace($LeagueRoot)) {
    $arguments += @('--league-root', (Resolve-Path -LiteralPath $LeagueRoot).Path)
}
if (-not [string]::IsNullOrWhiteSpace($VisionExe)) {
    $arguments += @('--vision-exe', (Resolve-Path -LiteralPath $VisionExe).Path)
}
$arguments += @('--mode', $Mode, '--max-seconds', "$MaxSeconds")
if (-not [string]::IsNullOrWhiteSpace($DebugSubmode)) {
    $arguments += @('--debug-submode', $DebugSubmode)
}
if ($LegacyUi) {
    $arguments += '--legacy-ui'
}

& $python.File @arguments
exit $LASTEXITCODE

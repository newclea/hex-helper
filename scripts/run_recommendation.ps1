[CmdletBinding(DefaultParameterSetName = 'WindowTitle')]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Hero,

    [Parameter(ParameterSetName = 'WindowTitle')]
    [ValidateNotNullOrEmpty()]
    [string]$WindowTitle = 'League of Legends (TM) Client',

    [Parameter(Mandatory = $true, ParameterSetName = 'Hwnd')]
    [ValidateRange(1, [long]::MaxValue)]
    [long]$Hwnd,

    [Parameter(Mandatory = $true, ParameterSetName = 'Replay')]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$Replay,

    [Parameter(ParameterSetName = 'WindowTitle')]
    [Parameter(ParameterSetName = 'Hwnd')]
    [ValidateSet('auto', 'wgc', 'desktop')]
    [string]$CaptureBackend = 'auto',

    [Parameter(ParameterSetName = 'WindowTitle')]
    [Parameter(ParameterSetName = 'Hwnd')]
    [ValidateSet('auto', 'off')]
    [string]$LcuContext = 'auto',

    [Parameter(ParameterSetName = 'WindowTitle')]
    [Parameter(ParameterSetName = 'Hwnd')]
    [string]$LeagueRoot = '',

    [ValidateRange(1, 86400)]
    [int]$MaxSeconds = 7200,

    [string[]]$ResumeOwned = @(),

    [switch]$NoHotkeys,
    [Parameter(ParameterSetName = 'WindowTitle')]
    [Parameter(ParameterSetName = 'Hwnd')]
    [switch]$CollectSamples,
    [switch]$NoSidecar,
    [Parameter(ParameterSetName = 'WindowTitle')]
    [Parameter(ParameterSetName = 'Hwnd')]
    [switch]$NoForceRecognitionHotkey,
    [switch]$VisionDebug
)

$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$bridge = Join-Path $workspaceRoot 'scripts\phase3\realtime_recommendation.py'

$python = Get-Command py -ErrorAction SilentlyContinue
if ($null -eq $python) {
    throw 'Python launcher "py" was not found on PATH.'
}

$bridgeArgs = @(
    '-3.11', '-B', $bridge,
    '--hero', $Hero,
    '--max-seconds', $MaxSeconds
)

switch ($PSCmdlet.ParameterSetName) {
    'Hwnd' {
        $bridgeArgs += @('--hwnd', $Hwnd)
    }
    'Replay' {
        $bridgeArgs += @('--replay', (Resolve-Path -LiteralPath $Replay).Path)
    }
    default {
        $bridgeArgs += @('--window-title', $WindowTitle)
    }
}

if ($PSCmdlet.ParameterSetName -ne 'Replay') {
    $bridgeArgs += @('--capture-backend', $CaptureBackend)
    $bridgeArgs += @('--lcu-context', $LcuContext)
    if (-not [string]::IsNullOrWhiteSpace($LeagueRoot)) {
        $resolvedLeagueRoot = (Resolve-Path -LiteralPath $LeagueRoot).Path
        $leagueClientDirectory = Join-Path $resolvedLeagueRoot 'LeagueClient'
        if (-not (Test-Path -LiteralPath $leagueClientDirectory -PathType Container)) {
            throw "LeagueRoot does not contain a LeagueClient directory: '$resolvedLeagueRoot'."
        }
        $env:LOL_ASSISTANT_LEAGUE_ROOT = $resolvedLeagueRoot
    }
}

if ($NoHotkeys) {
    $bridgeArgs += '--no-hotkeys'
}
if ($CollectSamples) {
    $bridgeArgs += '--collect-samples'
}
if ($NoSidecar) {
    $bridgeArgs += '--no-sidecar'
}
if ($NoForceRecognitionHotkey) {
    $bridgeArgs += '--no-force-recognition-hotkey'
}
if ($VisionDebug) {
    $bridgeArgs += '--vision-debug'
}
foreach ($augment in $ResumeOwned) {
    if ([string]::IsNullOrWhiteSpace($augment)) {
        throw 'ResumeOwned entries must be AUGMENT_ID or AUGMENT_ID=NAME.'
    }
    $bridgeArgs += @('--resume-owned', $augment)
}

Push-Location -LiteralPath $workspaceRoot
try {
    & $python.Source @bridgeArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}

[CmdletBinding()]
param(
    [string]$LeagueRoot = '',
    [string]$VisionExe = '',
    [ValidateSet('KIWI', 'KIWI_JADE')]
    [string]$Mode = 'KIWI',
    [ValidateRange(1, 86400)]
    [int]$MaxSeconds = 86400,
    [switch]$LegacyUi
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$app = Join-Path $workspaceRoot 'scripts\recognition_overlay\app.py'

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
if ($LegacyUi) {
    $arguments += '--legacy-ui'
}

& $python.File @arguments
exit $LASTEXITCODE

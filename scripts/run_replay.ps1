[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReplayPath,
    [ValidateSet('Debug', 'Release', 'RelWithDebInfo', 'MinSizeRel')]
    [string]$Configuration = 'Release',
    [string]$BuildDirectory = '',
    [string]$Champion = '',
    [ValidateSet('KIWI', 'KIWI_JADE')]
    [string]$Mode = 'KIWI',
    [string]$Knowledge = '',
    [string]$Workspace = '',
    [ValidateRange(0.001, 86400.0)]
    [double]$MaxSeconds = 30.0,
    [switch]$Preview,
    [switch]$Once,
    [ValidateSet('', 'left', 'center', 'right')]
    [string]$Selected = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not [System.IO.Path]::IsPathRooted($ReplayPath)) {
    $ReplayPath = Join-Path $repoRoot $ReplayPath
}
$arguments = @(
    '--replay', [System.IO.Path]::GetFullPath($ReplayPath),
    '--mode', $Mode,
    '--max-seconds', $MaxSeconds.ToString([Globalization.CultureInfo]::InvariantCulture)
)
if (-not [string]::IsNullOrWhiteSpace($Workspace)) {
    if (-not [System.IO.Path]::IsPathRooted($Workspace)) {
        $Workspace = Join-Path $repoRoot $Workspace
    }
    $arguments += @('--workspace', [System.IO.Path]::GetFullPath($Workspace))
}
if (-not [string]::IsNullOrWhiteSpace($Champion)) {
    $arguments += @('--champion', $Champion)
}
if (-not [string]::IsNullOrWhiteSpace($Knowledge)) {
    if (-not [System.IO.Path]::IsPathRooted($Knowledge)) {
        $Knowledge = Join-Path $repoRoot $Knowledge
    }
    $arguments += @('--knowledge', [System.IO.Path]::GetFullPath($Knowledge))
}
if ($Preview) {
    $arguments += '--preview'
}
if ($Once) {
    $arguments += '--once'
}
if (-not [string]::IsNullOrWhiteSpace($Selected)) {
    $arguments += @('--selected', $Selected)
}

$runParameters = @{
    Configuration = $Configuration
    ApplicationArguments = $arguments
}
if (-not [string]::IsNullOrWhiteSpace($BuildDirectory)) {
    $runParameters.BuildDirectory = $BuildDirectory
}
& (Join-Path $PSScriptRoot 'run.ps1') @runParameters
exit $LASTEXITCODE

[CmdletBinding(PositionalBinding = $false)]
param(
    [ValidateSet('Debug', 'Release', 'RelWithDebInfo', 'MinSizeRel')]
    [string]$Configuration = 'Release',
    [string]$BuildDirectory = '',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ApplicationArguments = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$packagedApplication = Join-Path $repoRoot 'bin\lol_augment_assistant.exe'
$isPackaged = Test-Path -LiteralPath $packagedApplication -PathType Leaf
if ($isPackaged) {
    $application = $packagedApplication
} else {
    if ([string]::IsNullOrWhiteSpace($BuildDirectory)) {
        $BuildDirectory = Join-Path $repoRoot 'outputs\tmp\build'
    } elseif (-not [System.IO.Path]::IsPathRooted($BuildDirectory)) {
        $BuildDirectory = Join-Path $repoRoot $BuildDirectory
    }
    $buildRoot = [System.IO.Path]::GetFullPath($BuildDirectory)
    $application = Join-Path $buildRoot 'bin\lol_augment_assistant.exe'
}
if (-not (Test-Path -LiteralPath $application -PathType Leaf)) {
    throw "Application not found at '$application'. Run scripts/build.ps1 first."
}

$commandOnly = @($ApplicationArguments | Where-Object {
    $_ -in @('--help', '-h', '--version', '--list-windows', '--probe-live-client')
})
$effectiveArguments = @($ApplicationArguments)
if ($commandOnly.Count -eq 0) {
    if ($effectiveArguments -notcontains '--knowledge') {
        $effectiveArguments = @(
            '--knowledge', (Join-Path $repoRoot 'data\knowledge\augments.zh-CN.json')
        ) + $effectiveArguments
    }
    if ($effectiveArguments -notcontains '--workspace') {
        $workspace = if ($isPackaged) {
            Join-Path $repoRoot 'runtime'
        } else {
            Join-Path $repoRoot 'outputs\runtime'
        }
        $effectiveArguments = @('--workspace', $workspace) + $effectiveArguments
    }
}

$exitCode = 1
Push-Location -LiteralPath $repoRoot
try {
    & $application @effectiveArguments
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $exitCode

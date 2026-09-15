[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release', 'RelWithDebInfo', 'MinSizeRel')]
    [string]$Configuration = 'Release',
    [string]$BuildDirectory = '',
    [string]$WindowsSdkVersion = '',
    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$allowedBuildRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $repoRoot 'outputs\tmp'))
if ([string]::IsNullOrWhiteSpace($BuildDirectory)) {
    $BuildDirectory = Join-Path $allowedBuildRoot 'build'
} elseif (-not [System.IO.Path]::IsPathRooted($BuildDirectory)) {
    $BuildDirectory = Join-Path $repoRoot $BuildDirectory
}
$buildRoot = [System.IO.Path]::GetFullPath($BuildDirectory)
$allowedPrefix = $allowedBuildRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
if (-not $buildRoot.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "BuildDirectory must be below '$allowedBuildRoot'; got '$buildRoot'."
}

$buildArguments = @{
    Configuration = $Configuration
    BuildDirectory = $buildRoot
}
if (-not [string]::IsNullOrWhiteSpace($WindowsSdkVersion)) {
    $buildArguments.WindowsSdkVersion = $WindowsSdkVersion
}
if ($Clean) {
    $buildArguments.Clean = $true
}
& (Join-Path $PSScriptRoot 'build.ps1') @buildArguments

$cachePath = Join-Path $buildRoot 'CMakeCache.txt'
$cmakeEntry = Select-String -LiteralPath $cachePath `
    -Pattern '^CMAKE_COMMAND:INTERNAL=(.+)$' | Select-Object -First 1
if ($null -eq $cmakeEntry) {
    throw "CMAKE_COMMAND was not found in '$cachePath'."
}
$cmakePath = $cmakeEntry.Matches[0].Groups[1].Value
$ctest = Join-Path (Split-Path -Parent $cmakePath) 'ctest.exe'
if (-not (Test-Path -LiteralPath $ctest -PathType Leaf)) {
    throw "CTest was not found next to CMake at '$ctest'."
}

$tempRoot = Join-Path $buildRoot 'temp'
New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
$env:TEMP = $tempRoot
$env:TMP = $tempRoot

& $ctest --test-dir $buildRoot -C $Configuration --output-on-failure
$ctestExitCode = $LASTEXITCODE
Write-Host "[test] ctest exit code: $ctestExitCode"
if ($ctestExitCode -ne 0) {
    throw "CTest failed with exit code $ctestExitCode."
}

$application = Join-Path $buildRoot 'bin\lol_augment_assistant.exe'
if (-not (Test-Path -LiteralPath $application -PathType Leaf)) {
    throw "Application stub was not found at '$application'."
}
foreach ($argument in @('--help', '--version')) {
    & $application $argument
    $smokeExitCode = $LASTEXITCODE
    Write-Host "[test] stub $argument exit code: $smokeExitCode"
    if ($smokeExitCode -ne 0) {
        throw "Stub smoke '$argument' failed with exit code $smokeExitCode."
    }
}

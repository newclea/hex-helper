[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory = $true)]
    [string]$BuildDirectory,
    [string]$DestinationDirectory = 'result'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-PathBelow {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $prefix = $Root.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    return $Path.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Copy-PackageFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$RelativePath,
        [Parameter(Mandatory = $true)]
        [string]$DestinationRoot
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Required package input not found: '$Source'."
    }
    $destination = Join-Path $DestinationRoot $RelativePath
    $parent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Copy-Item -LiteralPath $Source -Destination $destination -Force
}

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not [System.IO.Path]::IsPathRooted($BuildDirectory)) {
    $BuildDirectory = Join-Path $repoRoot $BuildDirectory
}
$buildRoot = [System.IO.Path]::GetFullPath($BuildDirectory)
if (-not (Test-PathBelow -Path $buildRoot -Root $repoRoot)) {
    throw "BuildDirectory must be inside '$repoRoot'; got '$buildRoot'."
}
if (-not [System.IO.Path]::IsPathRooted($DestinationDirectory)) {
    $DestinationDirectory = Join-Path $repoRoot $DestinationDirectory
}
$destinationRoot = [System.IO.Path]::GetFullPath($DestinationDirectory)
$resultRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot 'result'))
$temporaryRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot 'outputs\tmp'))
$destinationIsResult = $destinationRoot.Equals(
    $resultRoot, [System.StringComparison]::OrdinalIgnoreCase)
if (-not $destinationIsResult -and
    -not (Test-PathBelow -Path $destinationRoot -Root $temporaryRoot)) {
    throw "DestinationDirectory must be '$resultRoot' or below '$temporaryRoot'; got '$destinationRoot'."
}

$application = Join-Path $buildRoot 'bin\lol_augment_assistant.exe'
$cache = Join-Path $buildRoot 'CMakeCache.txt'
if (-not (Test-Path -LiteralPath $cache -PathType Leaf)) {
    throw "CMakeCache.txt was not found in the specified build: '$buildRoot'."
}
if (-not (Test-Path -LiteralPath $application -PathType Leaf)) {
    throw "Release executable was not found at '$application'."
}
$versionOutput = (& $application --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or
    $versionOutput -ne 'lol_augment_assistant 0.2.0 (Phase2 portable)') {
    throw "Build is not the expected Phase2 Release executable: '$versionOutput'."
}

$sourceManifest = Join-Path $repoRoot 'data\knowledge\augment_icons\manifest.json'
$iconRoot = Split-Path -Parent $sourceManifest
$manifest = Get-Content -LiteralPath $sourceManifest -Raw -Encoding UTF8 |
    ConvertFrom-Json
$templateFiles = @($manifest.templates | ForEach-Object { [string]$_.file } |
    Sort-Object -Unique)
if ([int]$manifest.summary.imported_template_count -ne 245 -or
    $templateFiles.Count -ne 163) {
    throw 'Icon manifest must contain 245 templates backed by exactly 163 unique files.'
}

$applicationBytes = [System.IO.File]::ReadAllBytes($application)
$applicationHex = [System.BitConverter]::ToString($applicationBytes).Replace('-', '')
$forbiddenNative = [System.IO.Path]::GetFullPath($sourceManifest)
$forbiddenForward = $forbiddenNative.Replace('\', '/')
foreach ($forbidden in @($forbiddenNative, $forbiddenForward)) {
    foreach ($encoding in @([System.Text.Encoding]::UTF8, [System.Text.Encoding]::Unicode)) {
        $patternHex = [System.BitConverter]::ToString(
            $encoding.GetBytes($forbidden)).Replace('-', '')
        if ($applicationHex.IndexOf(
                $patternHex, [System.StringComparison]::Ordinal) -ge 0) {
            throw "Executable contains forbidden source manifest path: '$forbidden'."
        }
    }
}

if (Test-Path -LiteralPath $destinationRoot) {
    $resolvedDestination = (Resolve-Path -LiteralPath $destinationRoot).Path
    if (-not $resolvedDestination.Equals(
            $resultRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
        -not (Test-PathBelow -Path $resolvedDestination -Root $temporaryRoot)) {
        throw "Refusing to replace unapproved destination '$resolvedDestination'."
    }
    Remove-Item -LiteralPath $resolvedDestination -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $destinationRoot | Out-Null

$fixedInputs = @(
    [pscustomobject]@{ Source = $application; Relative = 'bin\lol_augment_assistant.exe' }
    [pscustomobject]@{ Source = (Join-Path $repoRoot 'data\knowledge\augments.zh-CN.json'); Relative = 'data\knowledge\augments.zh-CN.json' }
    [pscustomobject]@{ Source = $sourceManifest; Relative = 'data\knowledge\augment_icons\manifest.json' }
    [pscustomobject]@{ Source = (Join-Path $repoRoot 'data\dataset\augment_offers\README.md'); Relative = 'data\dataset\augment_offers\README.md' }
    [pscustomobject]@{ Source = (Join-Path $repoRoot 'data\dataset\augment_offers\schema.json'); Relative = 'data\dataset\augment_offers\schema.json' }
    [pscustomobject]@{ Source = (Join-Path $repoRoot 'data\dataset\augment_offers\real\.gitkeep'); Relative = 'data\dataset\augment_offers\real\.gitkeep' }
    [pscustomobject]@{ Source = (Join-Path $repoRoot 'data\dataset\augment_offers\synthetic\.gitkeep'); Relative = 'data\dataset\augment_offers\synthetic\.gitkeep' }
    [pscustomobject]@{ Source = (Join-Path $repoRoot 'scripts\run.ps1'); Relative = 'scripts\run.ps1' }
    [pscustomobject]@{ Source = (Join-Path $repoRoot 'scripts\run_replay.ps1'); Relative = 'scripts\run_replay.ps1' }
    [pscustomobject]@{ Source = (Join-Path $repoRoot 'scripts\phase2\annotate_dataset.py'); Relative = 'scripts\phase2\annotate_dataset.py' }
    [pscustomobject]@{ Source = (Join-Path $repoRoot 'scripts\phase2\validate_dataset.py'); Relative = 'scripts\phase2\validate_dataset.py' }
    [pscustomobject]@{ Source = (Join-Path $repoRoot 'scripts\phase2\run_dataset_replay.py'); Relative = 'scripts\phase2\run_dataset_replay.py' }
    [pscustomobject]@{ Source = (Join-Path $repoRoot 'scripts\benchmark_phase2.py'); Relative = 'scripts\benchmark_phase2.py' }
    [pscustomobject]@{ Source = (Join-Path $repoRoot 'scripts\import_augments.py'); Relative = 'scripts\import_augments.py' }
    [pscustomobject]@{ Source = (Join-Path $repoRoot 'scripts\import_augment_icons.py'); Relative = 'scripts\import_augment_icons.py' }
    [pscustomobject]@{ Source = (Join-Path $repoRoot 'README.md'); Relative = 'README.md' }
    [pscustomobject]@{ Source = (Join-Path $repoRoot 'README.md'); Relative = 'Exp\README.md' }
)
foreach ($input in $fixedInputs) {
    Copy-PackageFile -Source $input.Source -RelativePath $input.Relative `
        -DestinationRoot $destinationRoot
}

$iconPrefix = $iconRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
foreach ($relativeIcon in $templateFiles) {
    if ([System.IO.Path]::IsPathRooted($relativeIcon)) {
        throw "Icon path must be relative: '$relativeIcon'."
    }
    $sourceIcon = [System.IO.Path]::GetFullPath((Join-Path $iconRoot $relativeIcon))
    if (-not $sourceIcon.StartsWith(
            $iconPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Icon path escapes the icon root: '$relativeIcon'."
    }
    Copy-PackageFile -Source $sourceIcon `
        -RelativePath (Join-Path 'data\knowledge\augment_icons' $relativeIcon) `
        -DestinationRoot $destinationRoot
}

$configSource = Join-Path $repoRoot 'config\default.json'
$configText = Get-Content -LiteralPath $configSource -Raw -Encoding UTF8
$configText = $configText.Replace(
    '"phase": "phase1-connected-pipeline"',
    '"phase": "phase2-portable-runtime"')
$configText = $configText.Replace(
    '"product_version": "0.1.0"',
    '"product_version": "0.2.0"')
$configText = $configText.Replace(
    '"note": "Reference manifest for connected Phase1 defaults; runtime configuration is passed through CLI flags."',
    '"note": "Reference manifest for the portable Phase2 runtime; runtime configuration is passed through CLI flags."')
$configText = $configText.Replace(
    '"icon_matcher": "unavailable_no_templates"',
    '"icon_matcher": "difference_hash_templates_connected"')
$configText = $configText.Replace(
    '"icon_matcher": { "state": "unavailable", "reason": "template_unavailable" }',
    '"icon_matcher": { "state": "available", "template_count": 245, "unique_image_files": 163 }')
$configText = $configText -replace ',\r?\n    "icon_matcher_unavailable"', ''
$configDestination = Join-Path $destinationRoot 'config\default.json'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $configDestination) |
    Out-Null
[System.IO.File]::WriteAllText(
    $configDestination, $configText,
    [System.Text.UTF8Encoding]::new($false))

$relativeFiles = [System.Collections.Generic.List[string]]::new()
foreach ($file in @(Get-ChildItem -LiteralPath $destinationRoot -Recurse -File)) {
    $relative = $file.FullName.Substring($destinationRoot.Length).TrimStart('\', '/')
    $relativeFiles.Add($relative.Replace('\', '/'))
}
$relativeFiles.Sort([System.StringComparer]::Ordinal)
$hashLines = foreach ($relative in $relativeFiles) {
    $nativeRelative = $relative.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
    $hash = (Get-FileHash -LiteralPath (Join-Path $destinationRoot $nativeRelative) `
        -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $relative"
}
$hashManifest = Join-Path $destinationRoot 'SHA256SUMS.txt'
[System.IO.File]::WriteAllText(
    $hashManifest, (($hashLines -join "`n") + "`n"),
    [System.Text.UTF8Encoding]::new($false))

$packagedIconCount = @(Get-ChildItem -LiteralPath (
        Join-Path $destinationRoot 'data\knowledge\augment_icons\icons') -File).Count
if ($packagedIconCount -ne 163) {
    throw "Package contains $packagedIconCount icon files; expected 163."
}
$packageFileCount = @(Get-ChildItem -LiteralPath $destinationRoot -Recurse -File).Count
$hashManifestSha256 = (Get-FileHash -LiteralPath $hashManifest -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "[package] destination: $destinationRoot"
Write-Host "[package] files: $packageFileCount"
Write-Host "[package] icons: $packagedIconCount"
Write-Host "[package] SHA256SUMS.txt: $hashManifestSha256"

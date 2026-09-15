[CmdletBinding()]
param(
    [string]$DestinationDirectory = 'outputs\recognition_overlay',
    [string]$VisionExe = '',
    [switch]$SkipInstall,
    [switch]$SkipVisionBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not [System.IO.Path]::IsPathRooted($DestinationDirectory)) {
    $DestinationDirectory = Join-Path $repoRoot $DestinationDirectory
}
$destination = [System.IO.Path]::GetFullPath($DestinationDirectory)
New-Item -ItemType Directory -Force -Path $destination | Out-Null

function Resolve-Python {
    $localPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
    $candidates = @(
        @{ Command = $localPython; Args = @() },
        @{ Command = 'py'; Args = @('-3.11') },
        @{ Command = 'py'; Args = @('-3') },
        @{ Command = 'python'; Args = @() },
        @{ Command = 'python3'; Args = @() }
    )
    foreach ($item in $candidates) {
        $file = $item.Command
        if (-not [System.IO.Path]::IsPathRooted($file)) {
            $found = Get-Command $file -ErrorAction SilentlyContinue
            if ($null -eq $found) {
                continue
            }
            $file = $found.Source
        } elseif (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            continue
        }
        $version = & $file @($item.Args + @('--version')) 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0 -and $version -match 'Python 3\.(1[1-9]|[2-9]\d)') {
            return @{ File = $file; Args = $item.Args }
        }
    }
    throw 'Python 3.11+ was not found. Install Python 3.11 before packaging.'
}

function Find-VisionExe {
    param([string]$Explicit)
    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($Explicit)) {
        $candidates += $Explicit
    }
    $candidates += @(
        (Join-Path $repoRoot 'outputs\tmp\build\bin\lol_augment_assistant.exe'),
        (Join-Path $repoRoot 'result\bin\lol_augment_assistant.exe'),
        (Join-Path $repoRoot 'bin\lol_augment_assistant.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

function Build-VisionEngine {
    $buildScript = Join-Path $repoRoot 'scripts\build.ps1'
    if (-not (Test-Path -LiteralPath $buildScript -PathType Leaf)) {
        throw "Missing $buildScript"
    }
    Write-Host 'Building embedded vision engine...'
    & powershell -NoProfile -ExecutionPolicy Bypass -File $buildScript -Configuration Release
    if ($LASTEXITCODE -ne 0) {
        throw "Vision engine build failed with exit code $LASTEXITCODE."
    }
}

$python = Resolve-Python
if (-not $SkipInstall) {
    $pipArgs = @('-m', 'pip', 'install', '--upgrade', 'pip', 'pyinstaller')
    & $python.File @($python.Args + $pipArgs)
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to install PyInstaller.'
    }
}

$resolvedVision = Find-VisionExe -Explicit $VisionExe
if ($null -eq $resolvedVision -and -not $SkipVisionBuild) {
    Build-VisionEngine
    $resolvedVision = Find-VisionExe -Explicit $VisionExe
}
if ($null -eq $resolvedVision) {
    throw 'lol_augment_assistant.exe was not found. A single EXE requires the vision engine.'
}

$work = Join-Path $repoRoot 'outputs\tmp\recognition_overlay_pack'
if (Test-Path -LiteralPath $work) {
    Remove-Item -LiteralPath $work -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $work | Out-Null

$championSource = Join-Path $repoRoot 'Scrape\raw\shared\champions_zh_CN.json'
$bundledChampionSource = Join-Path $repoRoot 'data\champions\champions_zh_CN.json'
if (-not (Test-Path -LiteralPath $championSource -PathType Leaf)) {
    $championSource = $bundledChampionSource
}
$knowledgeSource = Join-Path $repoRoot 'data\knowledge\augments.zh-CN.json'
$kiwiKnowledge = Join-Path $repoRoot 'data\knowledge\kiwi_augments.zh-CN.json'
$iconSource = Join-Path $repoRoot 'data\knowledge\augment_icons'
$recommendationSource = Join-Path $repoRoot 'data\recommendation'
$catSource = Join-Path $repoRoot 'assets\gamebuddy-cat.png'
if (-not (Test-Path -LiteralPath $championSource -PathType Leaf)) {
    throw "Missing champion catalog: $championSource"
}
if (-not (Test-Path -LiteralPath $kiwiKnowledge -PathType Leaf)) {
    throw "Missing KIWI hexcore catalog: $kiwiKnowledge"
}
if (-not (Test-Path -LiteralPath $recommendationSource -PathType Container)) {
    throw "Missing recommendation data: $recommendationSource"
}
if (-not (Test-Path -LiteralPath $catSource -PathType Leaf)) {
    throw "Missing GameBuddy cat asset: $catSource"
}

$addData = @(
    "$championSource;data/champions",
    "$kiwiKnowledge;data/knowledge",
    "$recommendationSource;data/recommendation",
    "$catSource;assets"
)
if (Test-Path -LiteralPath $knowledgeSource -PathType Leaf) {
    $addData += "$knowledgeSource;data/knowledge"
}
if (Test-Path -LiteralPath $iconSource -PathType Container) {
    $addData += "$iconSource;data/knowledge/augment_icons"
}

$hidden = @(
    'champion_catalog',
    'lcu_champ_select',
    'augment_catalog',
    'history_store',
    'view_model',
    'overlay_window',
    'vision_client',
    'league_root',
    'paths',
    'live_client',
    'hexcore_gate',
    'hexcore_database',
    'click_flag',
    'hid_mouse',
    'dinput_mouse',
    'cat_overlay',
    'controller',
    'recommendation_engine',
    'strategy_store'
)
$pyinstallerArgs = @(
    '-m', 'PyInstaller',
    '--noconfirm',
    '--clean',
    '--onefile',
    '--windowed',
    '--name', 'LoLRecognitionOverlay',
    '--distpath', $destination,
    '--workpath', (Join-Path $work 'build'),
    '--specpath', $work,
    '--paths', (Join-Path $repoRoot 'scripts\recognition_overlay'),
    '--paths', (Join-Path $repoRoot 'scripts\phase4'),
    '--paths', (Join-Path $repoRoot 'scripts\product')
)
foreach ($name in $hidden) {
    $pyinstallerArgs += @('--hidden-import', $name)
}
foreach ($item in $addData) {
    $pyinstallerArgs += @('--add-data', $item)
}
$pyinstallerArgs += @('--add-binary', "$resolvedVision;.")

$app = Join-Path $repoRoot 'scripts\recognition_overlay\app.py'
& $python.File @($python.Args + $pyinstallerArgs + $app)
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller failed.'
}

# Keep the published folder as a single double-click EXE.
# overlay.json is user config (league_root) and must not be deleted.
$sidecar = @(
    (Join-Path $destination 'lol_augment_assistant.exe'),
    (Join-Path $destination 'data')
)
foreach ($path in $sidecar) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
}

$exe = Join-Path $destination 'LoLRecognitionOverlay.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw "Package finished but missing $exe"
}
Write-Host "Created single EXE: $exe"
Write-Host "Embedded vision engine: $resolvedVision"

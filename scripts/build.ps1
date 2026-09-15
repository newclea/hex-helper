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

function Resolve-NativeTool {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [string[]]$FallbackPaths = @()
    )

    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $command) {
        return $command.Source
    }
    foreach ($candidate in $FallbackPaths) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Required tool '$Name' was not found."
}

function Resolve-Python311 {
    $localPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
    if (Test-Path -LiteralPath $localPython -PathType Leaf) {
        return (Resolve-Path -LiteralPath $localPython).Path
    }
    $launchers = @()
    foreach ($command in @(Get-Command python.exe -CommandType Application -All -ErrorAction SilentlyContinue)) {
        $launchers += [pscustomobject]@{ Path = $command.Source; Prefix = @() }
    }
    foreach ($command in @(Get-Command py.exe -CommandType Application -All -ErrorAction SilentlyContinue)) {
        $launchers += [pscustomobject]@{ Path = $command.Source; Prefix = @('-3.11') }
    }

    foreach ($launcher in $launchers) {
        $probe = & $launcher.Path @($launcher.Prefix) -c `
            'import pathlib,sys; print(pathlib.Path(sys.executable).resolve()); print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>$null
        if ($LASTEXITCODE -ne 0 -or $probe.Count -lt 2 -or $probe[-1] -ne '3.11') {
            continue
        }
        $resolved = [string]$probe[-2]
        if (Test-Path -LiteralPath $resolved -PathType Leaf) {
            return (Resolve-Path -LiteralPath $resolved).Path
        }
    }
    throw 'CPython 3.11 was not found through python.exe or py.exe.'
}

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

$programFilesX86 = [Environment]::GetFolderPath('ProgramFilesX86')
$vswhere = Join-Path $programFilesX86 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) {
    throw "vswhere was not found at '$vswhere'."
}
$vswhereArgs = @(
    '-latest', '-products', '*',
    '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
    '-requires', 'Microsoft.Component.MSBuild'
)
$installationPath = [string](& $vswhere @vswhereArgs -property installationPath | Select-Object -First 1)
$installationVersionText = [string](& $vswhere @vswhereArgs -property installationVersion | Select-Object -First 1)
if ([string]::IsNullOrWhiteSpace($installationPath)) {
    $fallback = Join-Path $programFilesX86 'Microsoft Visual Studio\2022\BuildTools'
    if (Test-Path -LiteralPath (Join-Path $fallback 'VC\Tools\MSVC') -PathType Container) {
        $installationPath = $fallback
        $installationVersionText = '17.14.0'
    }
}
if ([string]::IsNullOrWhiteSpace($installationPath)) {
    throw 'No Visual Studio 2022 instance with MSVC x64 and MSBuild was found.'
}
$installationVersion = [version]$installationVersionText
if ($installationVersion.Major -ne 17) {
    throw "Visual Studio 2022 (major 17) is required; found $installationVersion."
}
$visualStudio = [pscustomobject]@{
    installationPath = $installationPath.Trim()
    installationVersion = $installationVersionText.Trim()
}

$vsCMake = Join-Path $visualStudio.installationPath `
    'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
$cmake = Resolve-NativeTool -Name 'cmake.exe' -FallbackPaths @($vsCMake)
$python = Resolve-Python311

$sdkRoot = Join-Path $programFilesX86 'Windows Kits\10'
if (-not (Test-Path -LiteralPath $sdkRoot -PathType Container)) {
    throw "Windows 10 SDK root was not found at '$sdkRoot'."
}
if ([string]::IsNullOrWhiteSpace($WindowsSdkVersion)) {
    $sdkCandidates = foreach ($directory in @(Get-ChildItem -LiteralPath (Join-Path $sdkRoot 'Include') -Directory)) {
        $parsedVersion = $null
        if ([version]::TryParse($directory.Name, [ref]$parsedVersion) -and
            (Test-Path -LiteralPath (Join-Path $directory.FullName 'cppwinrt\winrt\Windows.Graphics.Capture.h') -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $sdkRoot "Lib\$($directory.Name)\um\x64\kernel32.lib") -PathType Leaf)) {
            [pscustomobject]@{ Name = $directory.Name; Version = $parsedVersion }
        }
    }
    $selectedSdk = $sdkCandidates | Sort-Object Version -Descending | Select-Object -First 1
    if ($null -eq $selectedSdk) {
        throw 'No Windows 10/11 SDK with x64 libraries and C++/WinRT projections was found.'
    }
    $WindowsSdkVersion = $selectedSdk.Name
}
$sdkCppWinRt = Join-Path $sdkRoot "Include\$WindowsSdkVersion\cppwinrt\winrt\Windows.Graphics.Capture.h"
$sdkX64Lib = Join-Path $sdkRoot "Lib\$WindowsSdkVersion\um\x64\kernel32.lib"
if (-not (Test-Path -LiteralPath $sdkCppWinRt -PathType Leaf) -or
    -not (Test-Path -LiteralPath $sdkX64Lib -PathType Leaf)) {
    throw "Windows SDK '$WindowsSdkVersion' lacks its C++/WinRT projection or x64 libraries."
}

if ($Clean -and (Test-Path -LiteralPath $buildRoot)) {
    $resolvedBuildRoot = (Resolve-Path -LiteralPath $buildRoot).Path
    if (-not $resolvedBuildRoot.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean build directory outside '$allowedBuildRoot'."
    }
    Remove-Item -LiteralPath $resolvedBuildRoot -Recurse -Force
}
$tempRoot = Join-Path $buildRoot 'temp'
New-Item -ItemType Directory -Force -Path $buildRoot, $tempRoot | Out-Null
$env:TEMP = $tempRoot
$env:TMP = $tempRoot

Write-Host "[build] workspace: $repoRoot"
Write-Host "[build] Visual Studio: $($visualStudio.installationPath) ($installationVersion)"
Write-Host '[build] generator: Visual Studio 17 2022 (x64, host=x64)'
Write-Host "[build] Windows SDK: $WindowsSdkVersion"
Write-Host "[build] CMake: $cmake"
Write-Host "[build] Python: $python"
Write-Host "[build] build root: $buildRoot"

$configureArguments = @(
    '-S', $repoRoot,
    '-B', $buildRoot,
    '-G', 'Visual Studio 17 2022',
    '-A', 'x64',
    '-T', 'host=x64',
    "-DCMAKE_GENERATOR_INSTANCE=$($visualStudio.installationPath)",
    "-DCMAKE_SYSTEM_VERSION=$WindowsSdkVersion",
    "-DLOL_ASSISTANT_WINDOWS_SDK_ROOT=$($sdkRoot.Replace('\', '/'))",
    "-DPython3_EXECUTABLE=$($python.Replace('\', '/'))"
)
& $cmake @configureArguments
$configureExitCode = $LASTEXITCODE
Write-Host "[build] configure exit code: $configureExitCode"
if ($configureExitCode -ne 0) {
    throw "CMake configure failed with exit code $configureExitCode."
}

& $cmake --build $buildRoot --config $Configuration --parallel
$buildExitCode = $LASTEXITCODE
Write-Host "[build] build exit code: $buildExitCode"
if ($buildExitCode -ne 0) {
    throw "CMake build failed with exit code $buildExitCode."
}

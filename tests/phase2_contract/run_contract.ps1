$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$buildRoot = Join-Path $repoRoot "outputs\tmp\phase2_contract"
$objectRoot = Join-Path $buildRoot "obj"
New-Item -ItemType Directory -Force -Path $buildRoot, $objectRoot | Out-Null

$msvcCandidates = @(
    "D:\Downloads\VisualStudio\Enterprise\VC\Tools\MSVC",
    "${env:ProgramFiles}\Microsoft Visual Studio\2022\Enterprise\VC\Tools\MSVC",
    "${env:ProgramFiles}\Microsoft Visual Studio\2022\Professional\VC\Tools\MSVC",
    "${env:ProgramFiles}\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC",
    "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC"
)
$msvcRoot = $msvcCandidates |
    Where-Object { Test-Path -LiteralPath $_ -PathType Container } |
    ForEach-Object { Get-ChildItem -LiteralPath $_ -Directory } |
    Sort-Object Name -Descending |
    Select-Object -First 1
if ($null -eq $msvcRoot) {
    throw "MSVC toolchain not found"
}
$cl = Join-Path $msvcRoot.FullName "bin\Hostx64\x64\cl.exe"
if (-not (Test-Path -LiteralPath $cl -PathType Leaf)) {
    throw "x64 cl.exe not found: $cl"
}

$sdkRoot = "${env:ProgramFiles(x86)}\Windows Kits\10"
$sdkVersion = Get-ChildItem -LiteralPath (Join-Path $sdkRoot "Include") -Directory |
    Where-Object {
        (Test-Path -LiteralPath (Join-Path $_.FullName "ucrt")) -and
        (Test-Path -LiteralPath (Join-Path $_.FullName "um"))
    } |
    Sort-Object Name -Descending |
    Select-Object -First 1
if ($null -eq $sdkVersion) {
    throw "Windows SDK not found"
}

$env:INCLUDE = @(
    (Join-Path $repoRoot "include"),
    (Join-Path $msvcRoot.FullName "include"),
    (Join-Path $sdkVersion.FullName "ucrt"),
    (Join-Path $sdkVersion.FullName "shared"),
    (Join-Path $sdkVersion.FullName "um"),
    (Join-Path $sdkVersion.FullName "winrt")
) -join ";"
$env:LIB = @(
    (Join-Path $msvcRoot.FullName "lib\x64"),
    (Join-Path $sdkRoot "Lib\$($sdkVersion.Name)\ucrt\x64"),
    (Join-Path $sdkRoot "Lib\$($sdkVersion.Name)\um\x64")
) -join ";"

$fixture = Join-Path $buildRoot "collector_contract_fixture.exe"
$arguments = @(
    "/nologo",
    "/std:c++20",
    "/W4",
    "/WX",
    "/permissive-",
    "/EHsc",
    "/utf-8",
    "/Zc:__cplusplus",
    "/DNOMINMAX",
    "/DWIN32_LEAN_AND_MEAN",
    "/DUNICODE",
    "/D_UNICODE",
    "/D_WIN32_WINNT=0x0A00",
    "/DWINVER=0x0A00",
    "tests\phase2_contract\collector_contract_fixture.cpp",
    "src\collection\sample_collector.cpp",
    "src\common\contracts.cpp",
    "src\detector\roi.cpp",
    "src\vision\icon_matcher.cpp",
    "src\replay\wic_image_codec.cpp",
    "/Fo$objectRoot\",
    "/Fd$buildRoot\collector_contract_fixture.pdb",
    "/Fe$fixture",
    "/link",
    "ole32.lib",
    "windowscodecs.lib",
    "/PDB:$buildRoot\collector_contract_fixture_link.pdb"
)

Push-Location $repoRoot
try {
    & $cl @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "collector contract fixture compilation failed: $LASTEXITCODE"
    }
    $env:PHASE2_COLLECTOR_FIXTURE = $fixture
    python -B -m unittest discover -s tests\phase2_contract -p "*_test.py" -v
    if ($LASTEXITCODE -ne 0) {
        throw "Phase2 contract tests failed: $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

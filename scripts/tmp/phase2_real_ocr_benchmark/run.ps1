[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path
$buildDir = Join-Path $repoRoot 'outputs\tmp\phase2_real_ocr_benchmark\build'

cmake -S $PSScriptRoot -B $buildDir -G 'Visual Studio 17 2022' -A x64
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed with exit code $LASTEXITCODE" }

cmake --build $buildDir --config Release --target phase2_real_ocr_benchmark
if ($LASTEXITCODE -ne 0) { throw "CMake build failed with exit code $LASTEXITCODE" }

$exe = Join-Path $buildDir 'bin\Release\phase2_real_ocr_benchmark.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    $exe = Join-Path $buildDir 'bin\phase2_real_ocr_benchmark.exe'
}
& $exe $repoRoot
if ($LASTEXITCODE -ne 0) { throw "Benchmark failed with exit code $LASTEXITCODE" }

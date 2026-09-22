$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location -LiteralPath $workspaceRoot
$speechTarget = Join-Path $env:LOCALAPPDATA 'LoLRecognitionOverlay\speech-runtime\1.13.8-py311-numpy1.26.4'
$env:PYTHONPATH = $speechTarget
$env:PYTHONDONTWRITEBYTECODE = '1'
$bench = Join-Path $PSScriptRoot 'benchmark.py'
$results = Join-Path $PSScriptRoot 'results'
$fp32 = Join-Path $PSScriptRoot 'models\vits-melo-tts-zh_en\model.onnx'
$meloResources = Join-Path $workspaceRoot 'assets\speech\melo-tts-zh_en'
foreach ($threadCount in @(1, 2, 4, 8)) {
    py -3.11 -B $bench --name "melo-fp32-t$threadCount" --threads $threadCount --model $fp32 --resources $meloResources --output $results
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
py -3.11 -B $bench --name melo-int8 --model (Join-Path $PSScriptRoot 'models\baseline-model.int8.onnx') --resources $meloResources --output $results
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$piper = Join-Path $PSScriptRoot 'piper-download\vits-piper-zh_CN-xiao_ya-medium'
py -3.11 -B $bench --name piper-xiaoya-fp32 --piper --model (Join-Path $piper 'zh_CN-xiao_ya-medium.onnx') --resources $piper --output $results
exit $LASTEXITCODE

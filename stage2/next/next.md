# Phase2 → 下一轮交接

## 当前结论

Phase2 的工程与可移植链路已经闭环：同一 `outputs/tmp/build_phase2_final_verify` Release exe 完成 21/21 CTest 和最终 headless producer/replay，末次 package 后 `result/SHA256SUMS.txt` 的 181 个条目逐项验证全部匹配；final build、final replay 与 packaged exe 的 SHA-256 同为 `b0726f47230e1f98ee4124091fec0de66e7bfa9d9bfffc71f6ffa1fda1a26075`。

修复后最终独立签收审计 v2 结论为 **PASS / P0=0 / P1=0**。Stage2 完整副本见 [`../outputs/phase2_final_independent_audit_v2.md`](../outputs/phase2_final_independent_audit_v2.md)，精简 handoff 副本见 [`files/reports/phase2_final_independent_audit_v2.md`](files/reports/phase2_final_independent_audit_v2.md)。该 PASS 严格受审计内 n=1 original-WGC offer 与其他真实性边界约束，不是稳定准确率声明。

真实数据验收仍不足：9 个 real bundle 中，6 个是 2560x1600 original-WGC（5 个 modal 遮挡 skip、1 个 clear n5），3 个是 1280x800 preview-derived 校准样本。最终 original-WGC benchmark 只有 1 个独立 offer/3 张相关卡，UI scale unknown：Screen 1/1、OCR 3/3 normalized exact、Icon 0/3 UNKNOWN、Final 3/3、three-card 1/1、unknown 0/3、false match 0/3、wall avg/p95 934.8124 ms。它证明 final 路径在这个样本上闭环，不证明稳定准确率、误报/漏报率或跨环境泛化。

**Phase3 暂缓。** 下一轮先扩充经授权的 original-WGC 分层 holdout，并优先解决 icon UNKNOWN；未达到预先定义的数据量与分层门槛前，不进入功能扩张。

## Stage2 结构

```text
stage2/
├── outputs/                         # 根 outputs 的末次完整副本
├── scripts/                         # 根 scripts 的末次完整副本
├── SHA256SUMS.txt                   # 整个 stage2 的相对路径/长度/SHA-256，不含自身
└── next/
    ├── next.md                      # 本交接路线
    └── files/
        ├── README.md                # 精简资产说明
        ├── SHA256SUMS.txt           # next/files 清单，不含自身
        ├── result/                  # 末次完整 portable package
        ├── reports/                 # final benchmark/validation/producer/report
        └── dataset/clear-n5/        # RAW + annotation + metadata
```

快照规模：`outputs` 为 16,566 文件、1,750,642,133 字节；`scripts` 为 18 文件、405,150 字节。163 个 icon 仅约 0.31 MiB，已完整保存在 `files/result/data/knowledge/augment_icons/icons`，manifest 位于其上级目录；更完整的构建、测试、OCR/icon 诊断、独立签收审计和历史图像证据请从 `../outputs` 查阅。

## 接手先验证

先验证整个 Stage2 清单，再验证嵌入 portable package 的原始清单。两个清单都不包含自身，避免循环引用。

```powershell
$stage = 'F:\Realworld\lol\stage2'

function Test-Sha256Manifest([string]$root, [string]$manifest) {
  $failures = @()
  foreach ($line in Get-Content -LiteralPath $manifest) {
    if ($line -notmatch '^([0-9a-f]{64})  ([0-9]+)  (.+)$') {
      $failures += "malformed: $line"
      continue
    }
    $expectedHash = $Matches[1]
    $expectedLength = [int64]$Matches[2]
    $relative = $Matches[3]
    $path = Join-Path $root ($relative -replace '/', '\')
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
      $failures += "missing: $relative"
      continue
    }
    $item = Get-Item -LiteralPath $path
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
    if ($item.Length -ne $expectedLength -or $actualHash -ne $expectedHash) {
      $failures += "mismatch: $relative"
    }
  }
  if ($failures.Count) { throw ($failures -join "`n") }
}

Test-Sha256Manifest $stage (Join-Path $stage 'SHA256SUMS.txt')

$package = Join-Path $stage 'next\files\result'
$packageFailures = @()
foreach ($line in Get-Content -LiteralPath (Join-Path $package 'SHA256SUMS.txt')) {
  if ($line -notmatch '^([0-9a-f]{64})  (.+)$') { throw "malformed package line: $line" }
  $path = Join-Path $package ($Matches[2] -replace '/', '\')
  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
  if ($actual -ne $Matches[1]) { $packageFailures += $Matches[2] }
}
if ($packageFailures.Count) { throw "package mismatch: $($packageFailures -join ', ')" }

& (Join-Path $package 'bin\lol_augment_assistant.exe') --version
```

预期版本为 `lol_augment_assistant 0.2.0 (Phase2 portable)`。不要运行 preview，不要复用历史 HWND；Live 只使用 exact title `League of Legends (TM) Client`，selector 会对 launcher、子串、case mismatch、歧义和二次枚举失效 fail closed。

## 可复用入口

`files/result` 已包含下一轮必需的全部小型规范文件：

- `bin/lol_augment_assistant.exe` 与 package `SHA256SUMS.txt`；
- `data/knowledge/augments.zh-CN.json`；
- `data/knowledge/augment_icons/manifest.json` 与 163 个引用 PNG；
- `data/dataset/augment_offers/schema.json` 与 README；
- `scripts/phase2/validate_dataset.py`、`annotate_dataset.py`、`run_dataset_replay.py`；
- `scripts/benchmark_phase2.py`、`run.ps1`、`run_replay.ps1`。

最终证据固定在 `files/reports`。Clear n5 固定在 `files/dataset/clear-n5/sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6`。

## 下一轮路线

1. 先写并冻结验收计划：独立 offer 数量、patch、resolution、UI scale、HDR/SDR、hover/highlight、modal/无遮挡、KIWI/KIWI_JADE 分层及每层最低样本数；在看到结果前确定门槛。
2. 只采集得到明确授权的 original-WGC。默认 headless，不传 `--preview`；每次按 exact title 动态选择，不传历史 HWND。保存原始像素及 typed `source.kind=windows_graphics_capture`、`derived_from_preview=false`、`capture_boundary=original_wgc`、`benchmark_use=original_wgc_metrics`。
3. 使用 schema/annotator 完成三卡人工标签；对 modal、AFK、断线、非 offer 页面使用明确 skip reason。Preview-derived 继续只放 calibration bucket，绝不改写为 original-WGC。
4. 在 split 前做同一局与近重复隔离；固定 holdout 后，校准数据与 holdout 不得互相泄漏。
5. 使用同一候选 exe headless 运行 producer。必须保留 staged OCR/icon/final；不得用 annotation、final ID 或另一阶段回填缺失阶段。
6. 报告总体和全部预定义分层的 denominator、UNKNOWN、false match、FPR/FNR、avg/P95 latency 与典型失败。当前首要技术目标是解决 icon 0/3 UNKNOWN，同时保持 OCR/icon 冲突 fail closed。
7. 数据量达标后再做长时 WGC、resize/device-loss、资源趋势与 crash-recovery/跨 SQLite-JSONL-artifacts 一致性验证。
8. 只有固定 holdout 与长时门槛通过后，才召开 Phase3 准入评审；在此之前保持 Phase3 暂缓。

## 精确命令

以下命令不启动游戏，也不启用 preview；Live/collect 仅在操作者已自行启动游戏且明确授权时使用。

```powershell
$stage = 'F:\Realworld\lol\stage2'
$handoff = Join-Path $stage 'next\files'
$package = Join-Path $handoff 'result'
$sample = Join-Path $handoff 'dataset\clear-n5\sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6'
$workspace = 'F:\Realworld\lol\outputs\runtime\stage2-next'

# help / version
& (Join-Path $package 'scripts\run.ps1') --help
& (Join-Path $package 'scripts\run.ps1') --version

# clear n5 replay, headless
& (Join-Path $package 'scripts\run_replay.ps1') `
  -ReplayPath (Join-Path $sample 'RAW.png') `
  -Mode KIWI `
  -Workspace $workspace `
  -MaxSeconds 30 `
  -Once

# dataset validation and non-opening annotation listing
py -3.11 -B (Join-Path $package 'scripts\phase2\validate_dataset.py') `
  --dataset-root 'D:\authorized-lol-augment-dataset\augment_offers' `
  --knowledge (Join-Path $package 'data\knowledge\augments.zh-CN.json')
py -3.11 -B (Join-Path $package 'scripts\phase2\annotate_dataset.py') `
  --dataset-root 'D:\authorized-lol-augment-dataset\augment_offers' `
  --knowledge (Join-Path $package 'data\knowledge\augments.zh-CN.json') `
  list --all

# dataset -> replay -> benchmark，所有输出写到 dataset 外
py -3.11 -B (Join-Path $package 'scripts\phase2\run_dataset_replay.py') `
  --exe (Join-Path $package 'bin\lol_augment_assistant.exe') `
  --dataset-root 'D:\authorized-lol-augment-dataset\augment_offers' `
  --knowledge (Join-Path $package 'data\knowledge\augments.zh-CN.json') `
  --output-dir 'F:\Realworld\lol\outputs\tmp\stage2-next-benchmark' `
  --timeout-seconds 30

# authorized headless live collection；移除 --no-hotkey 才启用人工 F8 观察
& (Join-Path $package 'scripts\run.ps1') `
  --window-title "League of Legends (TM) Client" `
  --collect-samples `
  --dataset-root 'D:\authorized-lol-augment-dataset\augment_offers' `
  --no-hotkey `
  --max-seconds 600
```

## 仍为 OPEN

- original-WGC 独立 offer 只有 1 个；稳定准确率、置信区间、FPR/FNR 与跨环境泛化未知。
- Icon 为 0/3 UNKNOWN；top-2 truth 诊断不是 accepted accuracy。
- 只有 2560x1600 clear 正例且 UI scale unknown；其他 resolution/UI scale/HDR/hover/patch 未形成 holdout。
- 长时 WGC 稳定性和断电/强杀级 SQLite/JSONL/artifacts crash atomicity 未关闭。
- Preview-derived 只可作校准；5 个 modal 遮挡样本只可作 UNKNOWN/失败案例，不可扩充 clear benchmark denominator。

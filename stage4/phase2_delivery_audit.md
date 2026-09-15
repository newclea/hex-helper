# Phase2 交付 / 运行路径审计

审计工作区：`F:\Realworld\lol`  
审计日期：2026-08-26（Asia/Shanghai）  
结论：**当前源码树内的 Phase2 功能有一套 20/20 CTest 历史通过证据，但交付包、可移植运行和真实 benchmark 仍未闭环，不能按“可复制到新机器直接验收”的 Phase2 Release 签收。**

两项优先结论：

1. **P0 / icon manifest：**当前 Phase2 exe 确实内嵌 `F:/Realworld/lol/data/knowledge/augment_icons/manifest.json`，离开该源码绝对路径即无法初始化 pipeline；`result` 又没有携带 manifest。
2. **旧 HWND 命令作废：**`outputs/phase2_integration_report.md` 把 `0x70E0A` 误称为游戏窗口；后续审计已确认它是 `LeagueClientUx` launcher。真实游戏采集当时使用的是 `0x7210E8`，但该 HWND 也已失效。**两者都只能作为历史证据，严禁复制进新的运行/验收命令。**

## 1. 审计边界与证据口径

- 工作区没有 `.git`，无法用 commit、tag 或 `git diff` 证明源码、二进制、数据和报告来自同一版本。
- 按本次只写审计报告的约束，没有执行会产生文件的 configure/build/CTest、replay、采集或 benchmark，也没有启动游戏采集；唯一新增文件是本报告。
- 本次实际执行的无业务写入检查包括：`result` 与现有 build 的 `--help`/`--version`、PE 依赖扫描、文件/hash/时间戳盘点、manifest 静态完整性复核，以及 dataset validator。
- `outputs/tmp/build_phase2_integration_clean/Testing/Temporary/LastTest.log` 是 2026-08-25 23:58 的既有证据，不是本轮 fresh run。它记录当前 Phase2 executable 对应的 20/20 CTest 通过。
- 审计期间有另一个外部流程写入 `outputs/phase2_live_game_20260826_000313.*`，并在 `data/dataset/augment_offers/real` 新增 6 个 bundle、随后写入 annotation。本审计没有启动该流程或修改这些数据。以下 dataset 结论以外部流程结束后的最终稳定快照为准。

## 2. 一页结论

| 路径 | 状态 | 判定 |
|---|---|---|
| Fresh Release build | 命令存在；当前机器有 CMake 3.31.5、Python 3.11.6、VS2022/MSVC；已有当前源码的 clean build 证据 | **可执行，但本轮未 fresh 复跑** |
| CTest | 当前 CMake 注册 20 项；既有日志为 20/20 | **核心回归可跑，但统一验收不完整**：漏掉 dataset tools 与跨模块 contract |
| 源码树 `--help`/运行 | Phase2 build 的 help 含 collection 参数 | **源码树内可用** |
| Replay | 单图会执行最多 5 个静态 pass；已有可用 RAW.png | **源码树内可跑，但没有发布包自带 smoke fixture/统一断言脚本** |
| Live/采集 | CLI 与既有 WGC 日志证明调用路径存在 | **可启动，但 incoming/accepted 数据没有隔离；当前 canonical root 校验失败** |
| HWND 选择 | 旧报告硬编码 `0x70E0A`；真实 run 曾用 `0x7210E8` | **旧命令错误且 HWND 均具瞬时性；每次必须重新枚举并核对窗口/PID** |
| Dataset validate | 最终快照 9 个 real；4 个 benchmark eligible；5 个 skipped | **当前命令 exit 1，不是绿色交付基线** |
| Benchmark 工具 | 只接受专用 JSON bundle 或三份 split JSONL | **P0：没有 canonical dataset + replay output → benchmark input 的生产器** |
| `result` 发布包 | 只有 6 个文件；exe 是旧 Phase1；缺 icon manifest；无打包脚本 | **P0：不是 Phase2 发布包** |
| exe 可移植性 | 当前 Phase2 exe 内嵌 `F:/Realworld/lol/.../manifest.json` | **P0：离开本源码绝对路径后启动 pipeline 必然失败** |

## 3. 准确文件清单

### 3.1 构建、入口和数据工具

构建/文档入口：

- `CMakeLists.txt`
- `README.md`
- `config/default.json`
- `scripts/build.ps1`
- `scripts/test.ps1`
- `scripts/run.ps1`
- `scripts/run_replay.ps1`

Phase2/data/icon/benchmark 工具：

- `scripts/benchmark_phase2.py`
- `scripts/phase2/annotate_dataset.py`
- `scripts/phase2/validate_dataset.py`
- `scripts/import_augment_icons.py`
- `scripts/import_augments.py`
- `scripts/tmp/phase2_real_vision_probe.cpp`（临时、硬编码工作区输出路径，不是正式 benchmark producer）

相关 Phase2 测试：

- `tests/phase2_benchmark/test_benchmark_phase2.py`
- `tests/phase2_benchmark/fixtures/empty_dataset.json`
- `tests/phase2_benchmark/fixtures/mixed_dataset.json`
- `tests/phase2_benchmark/fixtures/joined/metadata.jsonl`
- `tests/phase2_benchmark/fixtures/joined/annotations.jsonl`
- `tests/phase2_benchmark/fixtures/joined/recognition_results.jsonl`
- `tests/phase2_python/dataset_tools_test.py`
- `tests/phase2_contract/run_contract.ps1`
- `tests/phase2_contract/collector_contract_fixture.cpp`
- `tests/phase2_contract/collection_dataset_contract_test.py`

### 3.2 `result` 发布树：当前恰好 6 个文件

| 文件 | bytes | SHA-256 |
|---|---:|---|
| `result/bin/lol_augment_assistant.exe` | 731648 | `9c045f1ec9e3f3e695afb3df1556737613972695dcb35a71f468e2518b70661e` |
| `result/config/default.json` | 3085 | `c604f6e99a0604828bc6b50137eb52846c0752d6e09d7ab417e61a01dc1687cd` |
| `result/data/knowledge/augments.zh-CN.json` | 201411 | `aa8d5bf621241f749feac0cd3a7e1138c8f5253afd35694edeced1f6bb5839a7` |
| `result/Exp/README.md` | 7806 | `dd8b0845aa9f5c5496bc8102d47a95c20a2b847dec25f522187cd8b3a007d4e8` |
| `result/scripts/run_replay.ps1` | 2072 | `2eae1738712fda2cca94d7313d62e5b967ab6c83e1c30f67bb7b006de0cd0980` |
| `result/scripts/run.ps1` | 2088 | `a7a0523e38d54dd3fbb9f71c8fe2b111b5bf80d1a5dc262d58ccf760e6b293cf` |

两份 packaged PowerShell 脚本、catalog 和 config 与源码树对应文件逐字节相同；但 exe 与文档/配置是旧 Phase1 交付，且发布树没有 `data/knowledge/augment_icons/manifest.json`。

### 3.3 Knowledge / icon manifest

- `data/knowledge/augments.zh-CN.json`：655 records，201411 bytes，SHA-256 为 `aa8d...9a7`。
- `data/knowledge/augment_icons/manifest.json`：177427 bytes；schema 1；patch `16.16.805.442`；245 templates；163 unique images；missing 0；content hash `4f47c954...83d25`。
- `data/knowledge/augment_icons/icons/*.png`：恰好 163 文件，合计 329498 bytes。
- 复核结果：manifest content hash 与 `scripts/import_augment_icons.py::canonical_bytes` 一致；catalog hash 一致；163 个 PNG 文件名均等于文件内容 SHA-256；manifest 无缺图、无额外图、template ID 唯一。
- manifest 的 `catalog.source` 与 4 条 `sources[].path` 全是本机绝对路径；其中包括 `F:/Realworld/lol/outputs/tmp/game_assets` 和 3.52 GB 的本机 LoL WAD。它们适合 provenance，但不能作为异机可复现入口。
- 当前产品运行时只读取 manifest 内的 augment id、mode 和 dHash；不读取 163 张 PNG。因此 manifest 是**运行时必需品**，PNG 是**复现/审计交付品**。

### 3.4 Dataset 最终快照

`data/dataset/augment_offers` 现有 58 个文件：根部 `README.md`、`schema.json`；`real/.gitkeep`；`synthetic/.gitkeep`；9 个 real sample，每个 6 个文件。

旧 legacy bundle（均 complete，使用 `LEFT.png/CENTER.png/RIGHT.png`，validator 给非阻断 deprecated warning）：

- `real-preview-1787670257978-f010`
- `real-preview-1787670257978-f020`
- `real-preview-1787670572085-f0025`

新 canonical bundle（使用 `LEFT_CARD.png/CENTER_CARD.png/RIGHT_CARD.png`）：

- `sample_1787673873611452_f2_p30896_n0_0309c4711657f988` — skipped
- `sample_1787673874882312_f69_p30896_n1_77c5f72fbff68bea` — skipped
- `sample_1787673876034130_f134_p30896_n2_7d20cfaed875701d` — skipped
- `sample_1787673877200290_f199_p30896_n3_47d9c66595f20b78` — skipped
- `sample_1787673880393715_f382_p30896_n4_d56184b2891fa1ac` — skipped
- `sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6` — complete

每个 sample 的精确文件集均为 `RAW.png`、三张 card PNG、`metadata.json`、`annotation.json`。最终 validator 快照：exit 1，`valid=false`，real=9，complete/eligible=4，skipped=5，issues=`annotation_skipped:5`，legacy warnings=3。

### 3.5 顶层 Phase2 outputs（本报告写入前已有 15 个）

- `phase2_benchmark_report.md`
- `phase2_collection_report.md`
- `phase2_contract_report.md`
- `phase2_dataset_report.md`
- `phase2_icon_report.md`
- `phase2_integration_report.md`
- `phase2_live_collection_final.jsonl`
- `phase2_live_collection_final.stderr.jsonl`
- `phase2_live_game_20260826_000313.jsonl`
- `phase2_live_game_20260826_000313.stderr.log`
- `phase2_real_benchmark.json`
- `phase2_real_benchmark.md`
- `phase2_real_dataset_validation.json`
- `phase2_real_ingest_report.md`
- `phase2_roi_ocr_report.md`

非发布目录混在同一 `outputs` 下：`phase2_emergency_real` 217 files / 237215315 bytes；`runtime` 379 files / 4415804 bytes；`tmp` 13850 files / 1267296078 bytes。没有交付 manifest 指明哪一组 build/report/data 才是签收基线。

审计报告写入后，外部并行流程又新增了 `outputs/phase2_headless_safety_audit.md`。该报告明确撤销 `0x70E0A` 的“游戏 HWND”说法；它不属于上述写入前 15-file 快照。

## 4. Build / CTest 审计

### 正向事实

- `scripts/build.ps1` 固定 Windows x64、VS2022、MSVC、CMake、CPython 3.11，并把 build root 限制在 `outputs/tmp/**`；默认 Release，支持 `-Clean`。
- 当前 `CMakeLists.txt` 注册 20 个 CTest：17 个 C++ tests，加 `augment_import_determinism`、`json_loads_validation`、`phase2_benchmark_contract`。
- 当前源码之后生成的 `outputs/tmp/build_phase2_integration_clean/bin/lol_augment_assistant.exe` 时间为 23:57:58；对应既有 LastTest.log 为 20/20 passed。

### 未闭环

- `tests/phase2_python/dataset_tools_test.py` 没有注册到 CMake/CTest，也没有由 `scripts/test.ps1` 补跑。
- `tests/phase2_contract/run_contract.ps1` 和 `collection_dataset_contract_test.py` 也没有进入统一 test 入口。报告中的“20/20”不包含这两组交付关键测试。
- contract runner 自行找 `cl.exe`，首个候选硬编码 `D:\Downloads\VisualStudio\Enterprise\...`，并调用 PATH 上的裸 `python`；它没有复用 `build.ps1` 已解析的 VS/Python，异机行为不确定。
- CTest 并不完全 hermetic：`session_runtime_test` 和 `augment_frame_processor_test` 的路径由 `CMAKE_CURRENT_SOURCE_DIR` 注入，测试会写源码树下 `outputs/runtime` 与 `outputs/tmp/...`，而不是全部落在 `${CMAKE_BINARY_DIR}/artifacts`。
- `outputs/tmp/build` 的 exe（23:18）和生成的 16-test CTest 文件早于当前 CMake/main，属于旧 build，不能用于当前源码签收；默认 `scripts/run.ps1` 恰好指向这个旧 build。

## 5. 运行与发布可移植性

### P0：Phase2 exe 内嵌源码绝对路径

`CMakeLists.txt`：

```cmake
LOL_ASSISTANT_ICON_MANIFEST_PATH=L"${CMAKE_CURRENT_SOURCE_DIR}/data/knowledge/augment_icons/manifest.json"
```

`src/app/main.cpp` 在 pipeline 初始化时无条件打开该路径，失败即抛异常。当前 Phase2 exe 的 Release vcxproj 和 PE 均实际包含：

```text
F:/Realworld/lol/data/knowledge/augment_icons/manifest.json
```

PE 中 UTF-16LE 命中 offset 578144。这不是调试字符串推断，而是产品运行时路径。即使把 manifest 复制进 `result/data/...`，只要原 `F:\Realworld\lol` 不存在，exe 仍不会读取 package 内的 manifest。

catalog/workspace 的 packaged 路径处理本身是相对脚本根目录的；问题只在 icon manifest 没有 CLI/config/package-relative resolver。

### P0：`result` 不是当前 Phase2 binary

- `result/bin/lol_augment_assistant.exe` 时间为 21:45，早于 Phase2 CMake/main；`--help` 没有 `--collect-samples`、`--dataset-root`、`--sample-hotkey`、`--no-hotkey`、`--collect-suspect-confidence`。
- `result --version` 实际输出 `lol_augment_assistant 0.1.0 (Phase1 PoC)`。
- 当前 Phase2 build 的 help 已含上述参数；两者显然不是同一功能版本。
- `result` 缺运行时必需的 icon manifest，也没有 install/package/Copy-Item 生成脚本；CMake 无 `install()`/CPack。当前 `result` 是无法从源码确定性重建的手工目录。

### 其他可移植性缺口

- Release PE 动态依赖 `MSVCP140.dll`、`VCRUNTIME140.dll`、`VCRUNTIME140_1.dll`。`result` 未带 app-local VC runtime，README 也未声明安装 Microsoft Visual C++ Redistributable 的前置条件。
- `README.md`、`result/Exp/README.md`、两份 `config/default.json` 仍声称 Phase1、16/16、无 icon templates、仅 16:9；与当前 20 tests、245 templates、16:10/collection 行为冲突。
- 根 README 示例 `./sample.png` 在仓库根不存在；唯一明确的合成 replay fixture 位于 `stage1/next/files/samples/synthetic_detector_frame_320x180.png`，未进入正式发布包。

## 6. Replay / 采集 / Dataset / Benchmark 闭环

### Replay

源码实现对单图会复用同一 owning frame 执行最多 5 个 pass，因此可覆盖 stable detector/recognition 尝试；`run_replay.ps1` 能显式指定 build、knowledge 和 workspace。源码树内链路成立。

缺口是：没有发布包 smoke fixture、没有统一脚本断言 `icon_template_count=245`、`static_passes_processed=5`、session 正常关闭；当前 `result` 又是旧 exe。

### 采集与 Dataset

Phase2 build 支持 headless WGC + F8/manual + suspect auto collect。既有日志 `phase2_live_game_20260826_000313.jsonl` 记录 6 saved、5 duplicate、0 error，collector 写 canonical bundle。

但 collector 直接写 `data/dataset/augment_offers/real`；5 个合法人工 skipped 样本使整个 validator exit 1。当前没有 `incoming/quarantine` 与 `accepted benchmark corpus` 的目录或索引隔离，也没有“只验证/导出 eligible records”的命令。因此采集负样本/遮挡样本会持续破坏 canonical root 的绿色验收状态。

### 旧错误 HWND 命令

- `outputs/phase2_integration_report.md:98-142` 中两条 `--hwnd 0x70E0A` 命令作废。该 HWND 当时对应 `LeagueClientUx`，旧 run 只收到 2 帧、保存 0 样本，不能作为 LoL 游戏采集通过证据。
- `outputs/phase2_live_game_20260826_000313.jsonl` 和 6 个新 sample metadata 证明真正的游戏 run 当时使用 `0x7210E8`，标题为 `League of Legends (TM) Client`，收到 599 / 转换 597 帧并保存 6 个 bundle。
- `0x7210E8` 在审计收口时也已不是现存 HWND；Windows 可回收复用 HWND 值，所以它同样不能成为未来命令中的常量。
- 产品当前对直接 `--hwnd` 只验证窗口存在/可见，对 `--window-title` 只做可见标题唯一子串匹配；两者都不会验证目标进程确实是游戏而非 launcher。这是目标选择的 P1 缺口。
- `result/Exp/README.md` 的 `--hwnd 0x123456` 也应改成显式占位符，并强制先执行 `--list-windows`；否则读者容易把示例值当成可复用句柄。

### P0：真实 benchmark producer 缺失

`benchmark_phase2.py` 只接受：

1. `phase2_benchmark_input_v1` 的 `dataset.json`/`samples.json`；或
2. 同目录的 `metadata.jsonl` + `annotations.jsonl` + `recognition_results.jsonl`。

collector/annotator 实际产物是每个 sample 目录中的 `metadata.json` + `annotation.json` + PNG；app replay 输出是 session stdout/events/SQLite。仓库没有工具将二者按 sample_id 连接并生成 benchmark schema，也没有批量 replay canonical RAW、抽取 detector/OCR/icon/final ID/latency 的 runner。

因此下列现有命令只会得到 `source_state=no_benchmark_records`，不会把 4 个 eligible real samples 计入分母：

```powershell
python -B scripts\benchmark_phase2.py `
  --dataset data\dataset\augment_offers `
  --json-output outputs\phase2_real_benchmark.json `
  --markdown-output outputs\phase2_real_benchmark.md
```

现有 `outputs/phase2_real_benchmark.*` 正是 `sample_count=0`、所有率/延迟为 null 的不足数据报告，不是识别 benchmark。

## 7. P0 / P1 交付差距

### P0（阻断 Phase2 签收）

1. **发布包错误版本且缺运行时 manifest。** `result` 的 exe 是 Phase1，help 无 collection 参数，发布树没有 icon manifest。
2. **当前 Phase2 exe 不可移植。** icon manifest 使用编译期源码绝对路径，package-relative 文件不会被读取。
3. **采集/标注到真实 benchmark 的生产链缺失。** 没有 canonical dataset + replay recognition → benchmark input 的 adapter/runner，现有 real benchmark 分母为 0。
4. **没有可重建发布流程。** CMake/install/package target 和 release assembly/checksum manifest 均不存在，无法证明 `result` 与 fresh tested Release 相同。

### P1（应在交付前收口）

1. 统一 `scripts/test.ps1`/CTest 漏掉 dataset-tools 10 tests 与跨模块 contract 2 tests；测试还会写源码树 outputs。
2. 采集直接污染 accepted dataset root；skipped/incoming 没有隔离，当前 validator exit 1。
3. 旧 integration 报告把 launcher `0x70E0A` 当成游戏并留下可复制命令；当前 selector 又不能鉴别 launcher/game。固定 HWND 命令必须全部撤销，每次重新枚举并记录 title/PID/process identity。
4. README、result Exp、config、版本字符串均停留 Phase1，与当前功能和 20-test 口径冲突。
5. VC++ runtime 依赖没有 app-local 交付，也没有 redistributable 前置条件说明。
6. 没有正式 packaged replay fixture 与自动 smoke assertions；根 README 的 `sample.png` 不存在。
7. icon provenance 使用机器绝对路径；虽已检查入 manifest+163 PNG，但异机无法按记录命令重放导入，且 `--check` 仍依赖本机 3.52 GB WAD、wadtools 和 hashtable。
8. 顶层 `outputs` 同时承载报告、真实/临时素材、runtime 和 1.27 GB build scratch，没有 deliverables index；旧 `phase2_real_dataset_validation.json` 仍是 3-sample 快照，已落后于当前 9-sample 状态。

## 8. 可执行验收命令

以下命令均从 `F:\Realworld\lol` 的 PowerShell 执行。带“当前预期 FAIL”的门禁是为了让差距可机械复现。

### 8.1 Fresh Release + 当前 CTest

```powershell
.\scripts\test.ps1 `
  -Configuration Release `
  -BuildDirectory outputs\tmp\phase2_delivery_release `
  -Clean
```

该命令会 fresh configure/build，再运行当前注册的 20 CTest，并做 exe `--help/--version` smoke。签收要求 exit 0、20/20；本轮因只写报告约束未执行。

必须追加当前未纳入 CTest 的两组：

```powershell
python -B -m unittest discover -s tests\phase2_python -p '*_test.py' -v
.\tests\phase2_contract\run_contract.ps1
```

在修复前，这三条仍不是一个原子统一验收命令。

### 8.2 Phase2 CLI 与单图 replay

```powershell
.\scripts\run.ps1 `
  -BuildDirectory outputs\tmp\phase2_delivery_release `
  --help

.\scripts\run_replay.ps1 `
  -BuildDirectory outputs\tmp\phase2_delivery_release `
  -ReplayPath data\dataset\augment_offers\real\real-preview-1787670257978-f010\RAW.png `
  -Workspace outputs\tmp\phase2_delivery_replay_runtime `
  -MaxSeconds 30
```

验收 stdout 至少应包含 `icon_template_count=245`，并在 `session_end.source_summary` 中包含 `static_replay=true`、`static_passes_processed=5`、正常关闭状态。

### 8.3 Live 运行 smoke（不采集）

```powershell
$phase2Exe = (Resolve-Path `
  .\outputs\tmp\phase2_delivery_release\bin\lol_augment_assistant.exe).Path
$windowList = (& $phase2Exe --list-windows | ConvertFrom-Json).windows
$windowList | Format-Table hwnd,title,process_id

$gameCandidates = @($windowList | Where-Object {
  $_.title -eq 'League of Legends (TM) Client'
})
if ($gameCandidates.Count -ne 1) {
  throw "Expected exactly one current game window; got $($gameCandidates.Count)"
}
$gameProcess = Get-Process -Id $gameCandidates[0].process_id -ErrorAction Stop
$gameCandidates[0] | Format-List
$gameProcess | Select-Object Id,ProcessName,Path | Format-List
$freshGameHwnd = $gameCandidates[0].hwnd

& $phase2Exe `
  --hwnd $freshGameHwnd `
  --workspace outputs\tmp\phase2_delivery_live_runtime `
  --max-seconds 10
if ($LASTEXITCODE -ne 0) { throw "Live smoke failed: $LASTEXITCODE" }
```

必须由操作者核对本次枚举出的 title、PID 和 process identity 后再继续；不应使用 `0x70E0A`、`0x7210E8` 或其他历史常量。该 smoke 不应启动 LoL、preview 或采集。

### 8.4 隔离目录中的采集验收（本轮未执行）

```powershell
$acceptDataset = Join-Path `
  (Resolve-Path .\outputs\tmp).Path `
  'phase2_delivery_collection\augment_offers'
New-Item -ItemType Directory -Force `
  -Path $acceptDataset, "$acceptDataset\real", "$acceptDataset\synthetic" |
  Out-Null
Copy-Item -LiteralPath data\dataset\augment_offers\schema.json `
  -Destination "$acceptDataset\schema.json"

& $phase2Exe `
  --hwnd $freshGameHwnd `
  --workspace outputs\tmp\phase2_delivery_collection_runtime `
  --collect-samples `
  --dataset-root $acceptDataset `
  --sample-hotkey F8 `
  --collect-suspect-confidence 1 `
  --max-seconds 60

python -B scripts\phase2\annotate_dataset.py `
  --dataset-root $acceptDataset `
  list --all

python -B scripts\phase2\validate_dataset.py `
  --dataset-root $acceptDataset `
  --knowledge data\knowledge\augments.zh-CN.json
```

该流程只验证采集 bundle 和人工标注；按一次 F8 后应出现一个 `real/<sample-id>` canonical bundle。若标为 skipped，当前 validator 会 exit 1，这正是 incoming/accepted 隔离缺失的复现。

### 8.5 当前 dataset gate（当前预期 FAIL）

```powershell
python -B scripts\phase2\validate_dataset.py `
  --dataset-root data\dataset\augment_offers `
  --knowledge data\knowledge\augments.zh-CN.json
```

审计最终快照预期：exit 1；9 real；4 eligible；5 个 `annotation_skipped` issues；3 个 legacy warnings。

### 8.6 Benchmark 工具 contract（可跑）

```powershell
python -B scripts\benchmark_phase2.py `
  --dataset tests\phase2_benchmark\fixtures\mixed_dataset.json `
  --json-output outputs\tmp\phase2_benchmark_contract.json `
  --markdown-output outputs\tmp\phase2_benchmark_contract.md
```

这只验 benchmark 计算器，不验产品识别。真实闭环所需的下一条命令当前不存在，期望接口应类似：

```text
<missing benchmark producer> \
  --exe outputs/tmp/phase2_delivery_release/bin/lol_augment_assistant.exe \
  --dataset-root data/dataset/augment_offers \
  --output outputs/tmp/phase2_benchmark_input/dataset.json
```

producer 生成 `phase2_benchmark_input_v1` 后，才能执行：

```powershell
python -B scripts\benchmark_phase2.py `
  --dataset outputs\tmp\phase2_benchmark_input\dataset.json `
  --json-output outputs\phase2_real_benchmark.json `
  --markdown-output outputs\phase2_real_benchmark.md
```

### 8.7 发布包门禁（当前预期 FAIL）

```powershell
$required = @(
  'result\bin\lol_augment_assistant.exe',
  'result\scripts\run.ps1',
  'result\scripts\run_replay.ps1',
  'result\data\knowledge\augments.zh-CN.json',
  'result\data\knowledge\augment_icons\manifest.json'
)
$missing = @($required | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
if ($missing.Count -ne 0) { throw "Missing package files: $($missing -join ', ')" }

$helpText = & .\result\scripts\run.ps1 --help | Out-String
if ($LASTEXITCODE -ne 0 -or $helpText -notmatch '--collect-samples') {
  throw 'result executable is not the Phase2 CLI'
}

if (Select-String -LiteralPath CMakeLists.txt `
    -SimpleMatch 'LOL_ASSISTANT_ICON_MANIFEST_PATH=L"${CMAKE_CURRENT_SOURCE_DIR}') {
  throw 'product embeds the source-tree icon manifest path'
}
```

修复后还必须在**没有 `F:\Realworld\lol` 源码树、已满足明确 VC runtime 前置条件的 clean Windows x64 VM** 中执行：

```powershell
.\scripts\run.ps1 --help
.\scripts\run_replay.ps1 -ReplayPath C:\acceptance\frame.png -MaxSeconds 30
```

两条均 exit 0，replay 报 `icon_template_count=245`，且所有 runtime 文件只出现在复制后的 package `runtime/`，才算发布可移植性闭环。

## 9. 签收判定

当前判定：**FAIL / 不可签收为 Phase2 可移植 Release**。

最低解除条件：

1. 生成与当前源码/20-test build 同源的 `result`，携带 package-relative manifest，消除 exe 中源码绝对路径；
2. 提供确定性 package target/脚本和 checksum/deliverables manifest；
3. 将 dataset tools + collection contract 纳入统一验收；
4. 提供 canonical dataset → batch replay → recognition_results → benchmark input 的正式 producer；
5. 隔离 incoming/skipped 与 accepted benchmark corpus，并让 accepted gate 可稳定 exit 0；
6. 在 clean Windows x64 环境完成 package help/replay smoke，并记录 VC runtime 前置条件或 app-local 依赖。
7. 删除/更正所有把 `0x70E0A` 当游戏的命令；后续采集记录必须包含当次 freshly enumerated HWND、title、PID/process identity，且不得把历史 HWND 写成可重跑命令。

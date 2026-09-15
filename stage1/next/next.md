# Stage1 → Next handoff

## 当前思路与边界

Phase 1 已完成工程链路与回归验证，适合作为离线、只读、边界明确的 PoC 继续验收。下一阶段不是继续扩大功能，而是先补齐经授权的真实 League of Legends 三选一海克斯截图数据，以版本化标注建立 detector / OCR / 最终 ID benchmark，再依据分层结果校准。

本 handoff 不包含、也不声称包含任何真实 LoL 截图。`files/samples/synthetic_detector_frame_320x180.png` 是明确的 **synthetic 320×180 RAW 测试图**，只可用于 plumbing / replay 烟测，不可作为真实准确率证据。后续保持只读屏幕或离线 replay 边界；不要逆向客户端，不要读取游戏内存，不要注入、Hook、模拟输入或联网下载数据。

## 已确认结论

- 统一 Release CTest：**16/16 passed，0 failed**。
- Wave5：**10/10 CLOSED（10 CLOSED / 0 PARTIAL / 0 OPEN）**；这里仅指 Wave3 的 10 项确定性 P1 回归项。
- 单图 synthetic smoke 以 5 passes 走完稳定门和 OCR/共识状态机，结果为 `completed_unknown`；它证明流程能结束，不证明识别准确。
- 非 LoL 可见窗口 Live smoke 记录为 `received=150`、`converted=149`、`frames_processed=8`；它只证明短时 WGC → CPU → pipeline 链路可运行。
- Phase 1 没有运行真实 LoL，也没有真实海克斯截图或真实标注集。因此 detector、OCR、最终 ID 的真实准确率、误报率、漏报率、unknown rate、延迟和长时稳定性均为 **UNKNOWN / 未验收**。
- icon matcher 没有模板，当前状态固定为 **`unavailable/template_unavailable`**；没有图标识别或图标消歧成功证据。
- detector 是固定近似 **16:9** 的启发式布局；多分辨率、DPI / UI scale、HDR / SDR、hover / 高亮状态均未支持或未校准。

详细证据见：

- `files/docs/outputs_final_verification.md`
- `files/docs/outputs_wave5_regression_review.md`
- `files/docs/result_Exp_README.md`
- `files/docs/root_README.md`

## Stage1 快照与验证点

- `../outputs/`：根目录 `outputs/` 的完整复制，预期 9,342 个文件、830,423,844 字节。
- `../scripts/`：根目录 `scripts/` 的完整复制，预期 5 个文件、23,602 字节。
- `files/`：自包含运行载荷、知识库、默认配置、运行脚本、审计文档和明确标注的 synthetic 样本。
- `files/SHA256SUMS.txt`：10 个关键 handoff 文件的相对路径、字节长度和 SHA-256。

接手时先验证：

```powershell
$stage = 'F:\Realworld\lol\stage1'
Get-Content -LiteralPath "$stage\next\files\SHA256SUMS.txt"
& "$stage\next\files\bin\lol_augment_assistant.exe" --help
if ($LASTEXITCODE -ne 0) { throw "handoff exe --help failed: $LASTEXITCODE" }
```

如需复跑仅用于 plumbing 的 synthetic smoke，使用自包含 handoff；不要把结果表述为真实 LoL 验收：

```powershell
$handoff = 'F:\Realworld\lol\stage1\next\files'
& "$handoff\scripts\run_replay.ps1" `
  -ReplayPath "$handoff\samples\synthetic_detector_frame_320x180.png" `
  -Mode KIWI `
  -Workspace "$handoff\runtime\synthetic-handoff-smoke" `
  -MaxSeconds 30 `
  -Once
if ($LASTEXITCODE -ne 0) { throw "synthetic replay failed: $LASTEXITCODE" }
```

## 精确续跑命令：授权真实数据 benchmark

先让数据所有者提供或授权采集真实三选一截图，并将 `$dataset` 改为该数据集的单图、图片目录或 JSONL manifest 绝对路径。模式必须与截图来源一致；下例为 `KIWI`，玉剑模式改为 `KIWI_JADE`。完整数据集 benchmark 不加 `-Once`。

```powershell
$repo = 'F:\Realworld\lol'
$dataset = 'D:\authorized-lol-augment-dataset\manifest.jsonl'
$benchmarkWorkspace = "$repo\outputs\runtime\phase2-real-benchmark"

& "$repo\result\scripts\run_replay.ps1" `
  -ReplayPath $dataset `
  -Mode KIWI `
  -Knowledge "$repo\result\data\knowledge\augments.zh-CN.json" `
  -Workspace $benchmarkWorkspace `
  -MaxSeconds 600
if ($LASTEXITCODE -ne 0) { throw "real-data replay benchmark failed: $LASTEXITCODE" }
```

单张截图诊断可精确续跑为：

```powershell
$repo = 'F:\Realworld\lol'
$frame = 'D:\authorized-lol-augment-dataset\frames\example.png'
& "$repo\result\scripts\run_replay.ps1" `
  -ReplayPath $frame `
  -Mode KIWI `
  -Knowledge "$repo\result\data\knowledge\augments.zh-CN.json" `
  -Workspace "$repo\outputs\runtime\phase2-single-frame" `
  -MaxSeconds 30 `
  -Once
if ($LASTEXITCODE -ne 0) { throw "single-frame replay failed: $LASTEXITCODE" }
```

## OPEN 风险

- **跨 SQLite / JSONL / artifacts 的 crash atomicity = OPEN。** 现有 SQLite transaction、JSONL rollback / truncate 和 artifact 清理覆盖函数级正常失败，不覆盖进程被强杀、断电或 SQLite `COMMIT` 窗口。需要 crash injection、重启 reconciliation，或 outbox / commit marker / 原子 rename 设计与验证后才能关闭。
- **真实准确率 = UNKNOWN。** 无真实标注集，不能从 16/16、synthetic smoke 或非 LoL Live smoke 推导 precision / recall、OCR top-1、最终 ID accuracy 或 unknown rate。
- **icon = unavailable。** 尚无模板和真实同名消歧验证。
- **布局泛化未验收。** 固定 16:9；多分辨率、UI scale、HDR / SDR、hover / 高亮均未校准。

## Phase2 推荐路线

1. 先获得明确授权，再采集真实三选一页面截图；保留原始像素，不混入 synthetic，并记录数据来源与同意范围。
2. 为每张图建立版本化标注，至少包含：LoL patch、分辨率、DPI / UI scale、HDR / SDR、hover / 高亮状态、模式、左/中/右三项真实 augment ID / 文本，以及是否应检测到页面。
3. 按 patch × resolution × UI scale × HDR/SDR × hover 分层切分 train/calibration 与固定 holdout，避免同一局或近重复帧跨集合泄漏。
4. 使用上面的 `result/scripts/run_replay.ps1` 对 manifest / 图片目录跑 benchmark，将 detector 命中、三 ROI、OCR 文本、最终 ID、unknown、误报/漏报和延迟与标注逐项比对；同时报告总体指标和各分层指标。
5. 根据失败样本校准 ROI / 阈值和 OCR 文本匹配，再加入有来源、可版本化的 icon templates，专门验证同名或文字模糊场景；每次改动都复跑固定 holdout。
6. 独立设计 crash injection 与重启恢复测试，关闭跨 SQLite / JSONL / artifacts 的一致性风险。该工作不要与真实准确率结论混写。

只有真实 holdout 的分层 benchmark 和长时稳定性验证达到预先定义的门槛后，才可声称真实 LoL 流程通过。

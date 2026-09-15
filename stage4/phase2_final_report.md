# Phase 2 最终报告与 Stage2 交接基线

日期：2026-08-26（Asia/Shanghai）  
工作区：`F:\Realworld\lol`  
最终判定：**Phase 2 工程链路与同版本 final build/replay 已闭环；真实 original-WGC 准确率样本不足，禁止宣称稳定准确率。Phase 3 暂缓。**

## 一页结论

- 最终 Release 构建目录：`outputs/tmp/build_phase2_final_verify`；CTest **21/21 passed**。
- 最终 build exe 与 `result/bin/lol_augment_assistant.exe` SHA-256 完全相同：`b0726f47230e1f98ee4124091fec0de66e7bfa9d9bfffc71f6ffa1fda1a26075`；版本 `lol_augment_assistant 0.2.0 (Phase2 portable)`。
- 最终 producer 明确记录 `headless=true`，未请求 preview；replay 4 个完整标注样本，`status=ok`、failure=0。
- Dataset 共 9 个 real bundle：6 个 original-WGC 2560x1600，3 个 preview-derived 1280x800。original-WGC 中 5 个受 disconnect/AFK modal 遮挡而 skip，只有 clear n5 的 1 个独立 offer/3 张卡可进入指标。
- 最终 benchmark 对 clear n5：screen 1/1、OCR 3/3（normalized exact）、icon 0/3（三槽 UNKNOWN）、最终 augment ID 3/3、all-three 1/1、UNKNOWN 0/3、false match 0/3、wall latency avg/p95=934.8124 ms。**这些分母都只有 1 个 offer，不能外推。**
- 最终产品 staged OCR 已导出：`不 动 如 山`、`我 们 的 治 疗`、`星 界 躯 体` 经 NFKC+casefold 并去除 Unicode 空白/标点后与标签 3/3 normalized exact。Icon staged 输出如实为 0/3 UNKNOWN，未用 final 或 annotation 回填。

## 用户要求 10 项逐条交付

| # | 要求 | 状态 | 最终证据与真实性边界 |
|---:|---|---|---|
| 1 | 只读屏幕方案与安全边界 | 完成 | WGC/replay only；无进程内存、注入、Hook、私有协议、联网下载、模拟输入或启动游戏能力。 |
| 2 | Headless、不抢焦点 | 完成 | collect 与 preview 独立；preview 仅显式 opt-in，使用 `WS_EX_NOACTIVATE` 与 `SW_SHOWNOACTIVATE`；无 foreground/focus/input-injection API。F8 只读观察。 |
| 3 | 精确游戏窗口选择 | 完成 | Live 仅接受可见顶层窗口 exact title `League of Legends (TM) Client`；launcher、子串、case mismatch、ambiguous、ephemeral handle 均拒绝。不得把任何历史 HWND 写进新命令。 |
| 4 | 独立 SampleCollector | 完成 | detector suspect/F8 可在 accepted gate 外采样；五文件事务、exact+dHash 去重、路径约束、失败回滚已测试。 |
| 5 | Dataset schema/annotation/validation | 完成 | schema v1、annotator、validator 可复用；typed capture boundary 把 preview-derived 永久隔离出 original-WGC 指标。最终 9/9 结构 valid、20 warnings、0 error。 |
| 6 | 真实 detector/ROI | 定向完成 | clear n5 2560x1600 `visible=true/confidence=0.993798`；5 个 modal 遮挡 original-WGC 均 UNKNOWN；10 个既有正常画面负例 0/10 visible。未证明多分辨率 FPR/FNR。 |
| 7 | OCR 与文本匹配 | 链路完成、验收不足 | Windows.Media.Ocr zh-CN + exact/normalized/bounded fuzzy 已接入；final staged OCR 在 clear n5 为 3/3 normalized exact。样本仍只有 1 个 offer，不能定产品最优参数或稳定准确率。 |
| 8 | Icon 与 OCR/icon 融合 | 链路完成、验收不足 | manifest 245 records/163 PNG；portable resolve 已测。n5 生产 matcher 0/3 accepted、3/3 UNKNOWN；truth-in-top2 仅诊断。OCR-only 可存活但冲突仍 fail closed。 |
| 9 | Replay producer 与 benchmark 隔离 | 完成 | 严格 JSONL、sample_id join、operational failure 显式化；preview-derived、synthetic、skip 不进入 original-WGC denominator。 |
| 10 | 同版本 build/test/package/docs | 完成 | final build exe、final replay executable、result exe hash 同为 `B0726F...A26075`；21/21 CTest；末次打包后按 `result/SHA256SUMS.txt` 逐项验证；最终文档和 stage2 另建 handoff hash。 |

## 样本、分辨率与失败案例

| 分桶 | 数量 | RAW 分辨率 | 用途/结果 |
|---|---:|---|---|
| original-WGC | 6 | 2560x1600 | 5 个 modal 遮挡人工 skip；1 个 clear n5 benchmark eligible。 |
| preview-derived real | 3 | 1280x800（metadata active picture 1280x720） | 仅真实场景校准；禁止进入 original-WGC 指标。 |
| synthetic / unknown | 0 / 0 | — | 最终 dataset 无此类样本。 |

Clear n5：`sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6`，UI scale 未知，三项 truth 为 LEFT `ARAM_Impassable`、CENTER `Equilibrium`、RIGHT `ARAM_CelestialBody`。

典型失败/限制：

- n0..n4 的弹窗遮挡破坏至少一卡亮框，detector 按设计输出 UNKNOWN；它们不是误识别样本，也不能加入准确率分母。
- n5 staged OCR 3/3 normalized exact，最终 ID 3/3；这仍是同一 offer 内三张高度相关卡，不是 3 个独立 offer。
- n5 icon 的 left/center top1/top2 距离并列，right 的 truth 虽排第一但超最大距离且 margin 不足，因此生产 matcher 0/3 accepted、三槽 UNKNOWN。
- 3 个 preview-derived 样本保留 `provenance=real`，但 effective boundary=`preview_derived`、use=`real_scenario_calibration`、eligible=false。

## Headless 与 exact-title 运行规则

默认 run/replay/collect 均不要传 `--preview`。collect 的 F8 是只读键状态轮询，不发送键鼠事件；如需纯自动 suspect 采集，传 `--no-hotkey`。Live 目标使用 exact title：

```powershell
.\result\scripts\run.ps1 --window-title "League of Legends (TM) Client" --max-seconds 30
```

任何历史 HWND 都只属于旧日志，已失效且可能指向 launcher；不得复制到运行、采集或验收命令。每次都由当前可见窗口快照按 exact title 选择并二次确认。

## Build / test / run / replay / collect / benchmark

```powershell
# source build and all registered tests
.\scripts\build.ps1 -Configuration Release -BuildDirectory .\outputs\tmp\build_phase2_final_verify
.\scripts\test.ps1 -Configuration Release -BuildDirectory .\outputs\tmp\build_phase2_final_verify

# portable inspection and headless run
.\result\scripts\run.ps1 --help
.\result\scripts\run.ps1 --version
.\result\scripts\run.ps1 --window-title "League of Legends (TM) Client" --max-seconds 30

# headless collection; remove --no-hotkey only when operator-authorized F8 capture is wanted
$dataset = (Resolve-Path .\data\dataset\augment_offers).Path
.\result\scripts\run.ps1 --window-title "League of Legends (TM) Client" --collect-samples --dataset-root $dataset --no-hotkey --max-seconds 600

# clear n5 replay, no preview
$frame = (Resolve-Path .\data\dataset\augment_offers\real\sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6\RAW.png).Path
.\result\scripts\run_replay.ps1 -ReplayPath $frame -Mode KIWI -Workspace .\result\runtime\n5-replay -MaxSeconds 30 -Once

# validator
py -3.11 -B .\scripts\phase2\validate_dataset.py --dataset-root .\data\dataset\augment_offers --knowledge .\data\knowledge\augments.zh-CN.json

# dataset -> headless replay -> benchmark
py -3.11 -B .\scripts\phase2\run_dataset_replay.py --exe .\result\bin\lol_augment_assistant.exe --dataset-root .\data\dataset\augment_offers --knowledge .\data\knowledge\augments.zh-CN.json --output-dir .\outputs\tmp\phase2-replay-benchmark --timeout-seconds 30

# standalone benchmark over producer output
py -3.11 -B .\scripts\benchmark_phase2.py --dataset .\outputs\tmp\phase2-replay-benchmark\benchmark_input --json-output .\outputs\tmp\phase2-replay-benchmark\benchmark_report.json --markdown-output .\outputs\tmp\phase2-replay-benchmark\benchmark_report.md
```

## 全部真实性边界与 OPEN 项

- **不声称稳定准确率。** 100% 只表示 1 个独立 offer 的观测；三卡高度相关，重复 replay/计时不增加独立样本量。
- **不声称 OCR/icon 稳定通过。** 本轮 staged OCR 仅在一个 offer 上 3/3 normalized exact；icon 明确 0/3 UNKNOWN。Final ID、OCR、icon 是不同指标，任何阶段都不得由 annotation 或另一阶段回填。
- **不把 preview-derived 当 original-WGC。** 其真实来源属性不等于 benchmark eligibility。
- **不把弹窗遮挡 skip 当 detector 正确率分母。** 它们用于失败案例和 UNKNOWN 行为审计。
- **不声称跨环境泛化。** 当前清晰正例只有 2560x1600、UI scale unknown；多分辨率、DPI/UI scale、HDR/SDR、hover/highlight、不同 patch 均未形成 holdout。
- **不声称长期稳定。** WGC 队列/失败状态和短程运行有覆盖，但长时 frame loss、resize/device-loss 恢复与资源趋势未作真实游戏验收。
- **crash atomicity 仍 OPEN。** SQLite/JSONL/artifacts 的正常错误补偿不等于断电/强杀级跨介质原子性。
- `result/SHA256SUMS.txt` 不记录自身哈希；末次打包后必须逐项重算并验证每个列出文件。Exe 条目应为 `b0726f47230e1f98ee4124091fec0de66e7bfa9d9bfffc71f6ffa1fda1a26075`，本轮最终 handoff 另由 Stage2 独立 SHA-256 清单覆盖。

## Phase 3 暂缓与下一轮准入条件

Phase 3 不在本次启动。下一轮先在明确授权下扩大 original-WGC 数据：至少按 patch × resolution × UI scale × HDR/SDR × hover/modal 分层，做近重复/同局隔离的固定 holdout；保持 staged OCR/icon 协议并优先解决 icon UNKNOWN，再做长期 WGC/crash-recovery 验证。只有预先定义门槛、足够独立样本和分层结果均满足后，才允许讨论稳定准确率。

Stage2 的具体资产清单、hash 校验和下一轮路线见 `stage2/next/next.md`。

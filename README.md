# LoL Augment Assistant / GameBuddy

> 本快照：2026-09-15。识别弹窗 + 暗色海克斯牌在场检测 + OCR 原文显示。  
> 下面「历史 Phase 2」章节仍是 2026-08-30 的基础设施说明；`result/` 是旧发布包，不代表这一版构建。

## GameBuddy 小猫与推荐

默认启动界面已切换为右上角小猫：选定英雄后可选择“胜率优先、趣味玩法 1、趣味玩法 2”，识别到三张海克斯后只通过小猫气泡给出推荐，不修改或突出游戏原生卡牌。推荐会结合本局已经识别并保存的海克斯选择。

推荐数据位于 `data/recommendation/`。详细规则、数据边界和 OCR 联动契约见 `docs/technical-design.md`。如需查看原有诊断文本窗口，启动时增加 `--legacy-ui`。

## 这一版怎么用

置顶弹窗显示待选席、当前三选一、**读取原文**（左/中/右 OCR 原文和对齐结果）、已选海克斯。轮次按本地第 1–4 轮计数，不采用 C++ 的 `accepted_offer_count`。选完后当前三选一收起，已选全程保留。

视觉引擎抓 `League of Legends (TM) Client`：红框判断整张牌还在不在，蓝框只读名字。Mayhem 牌面近黑、金边很细，这一版按 16:9 实拍把红框贴紧金边，并放宽暗色牌亮度门槛，避免「牌在画面上却报 `no_offer_on_screen`、原文全空」。

### 前置

- 64-bit Windows
- Python 3.11
- Visual C++ 2015–2022 x64 运行库（跑已编译的 exe）
- 从源码重建另需 VS 2022 Build Tools、CMake、Windows SDK / C++/WinRT

### 启动识别弹窗（本包已带 exe）

```powershell
$py = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
& $py -B .\scripts\recognition_overlay\app.py `
  --vision-exe .\outputs\tmp\build\bin\lol_augment_assistant.exe `
  --mode KIWI
```

或：

```powershell
.\scripts\run_recognition_overlay.ps1 `
  -VisionExe .\outputs\tmp\build\bin\lol_augment_assistant.exe `
  -Mode KIWI
```

国服客户端打开后，弹窗会读 LCU 待选席和 Live Client 等级/阵亡。海克斯三选一仍只来自屏幕 OCR。

### 本包内容

| 路径 | 说明 |
| --- | --- |
| `src/` `include/` `scripts/` `tests/` `CMakeLists.txt` | 当前源码 |
| `data/knowledge/kiwi_augments.zh-CN.json` | 海克斯知识库 |
| `data/champions/champions_zh_CN.json` | 英雄中文名（若存在） |
| `outputs/tmp/build/bin/lol_augment_assistant.exe` | 本版 Release 视觉引擎 |
| `outputs/tmp/build/SHA256SUMS.txt` | exe 校验 |

本版 exe SHA-256：

`CEE929E6EB02F04DB418859877AB1DBFAC832D180E9CDDBA015602D36AFCF50D`

### 从源码重建

```powershell
.\scripts\build.ps1 -Configuration Release -BuildDirectory .\outputs\tmp\build
.\scripts\test.ps1
python -m unittest tests.recognition_overlay.test_recognition_overlay tests.recognition_overlay.test_card_pick_replay -q
```

单实例 mutex：`Local\LoLRecognitionOverlay.Single`。重建 exe 前先关掉弹窗和 `lol_augment_assistant.exe`，否则可能 LNK1104。

用户数据在 `%LOCALAPPDATA%\LoLRecognitionOverlay\`（已选记录、日志、vision workspace）。

---

## 历史：Phase 2 与推荐桥（2026-08-30）

> 当前源码树（2026-08-30）已经在 Phase 2 视觉基础设施之上接入推荐桥、Riot Live Client Data API 和只读 LCU 上下文。`result/` 仍是历史 Phase 2 发布包，不代表当前源码构建。

当前实时上下文分工：LCU 读取 `gameflowPhase`，并在 ChampSelect 读取 `championId`；局内 `championName/level/isDead/respawnTimer/currentHealth/maxHealth/healthPercent` 由固定的 `https://127.0.0.1:2999` 读取；海克斯三选一仍只来自屏幕 OCR/图标识别。三路事件彼此独立，任一路不可用都不会伪造数据或覆盖手工英雄。

ARAM: Mayhem 选牌时序已接入主动调度：第一轮直接监测界面；后续三轮分别在达到 7/11/15 级后布防，并在首次死亡上升沿开启有界的 20 FPS 视觉检测窗口。它只调整屏幕检测频率，不会读游戏内存或模拟输入。

国服客户端的 `LeagueClient\lockfile` 实测为空，因此 LCU 自动发现使用普通可读的 `LeagueClientUx.log`，并要求日志中的 PID 确实拥有对应的 loopback 监听端口。token 不进入命令行、JSON 或日志。

```powershell
.\scripts\run_recommendation.ps1 `
  -Hero "英雄中文名" `
  -LeagueRoot "F:\Program Files (x86)\英雄联盟(26)" `
  -LcuContext auto `
  -CaptureBackend auto
```

## Phase 2 视觉基础

这是一个 Windows x64/C++20 的只读屏幕识别 PoC。Phase 2 已连接 `Replay/Windows Graphics Capture → 16:9 三卡检测 → Windows.Media.Ocr(zh-CN) → mode-aware 文本与图标匹配 → 跨帧共识 → JSON/SQLite/JSONL/debug artifacts`。它不提供推荐、装备、统计、Overlay 或自动操作。

## 安全边界

- 输入仅来自用户显式给出的 PNG/JPEG/目录/JSONL manifest，或用户显式选择的当前可见顶层窗口。
- Live 路径枚举可见窗口，以 `IGraphicsCaptureItemInterop::CreateForWindow` 创建 WGC item；GPU 帧复制到本进程 D3D11 staging texture，再映射为 owning BGRA buffer。
- WGC 原始帧队列容量上限为 3，默认 `drop-oldest`；resize、FrameArrived、设备丢失/转换等不可恢复故障进入公开 `Failed` 状态并停止接收。
- 不读取/写入游戏进程内存，不注入、不 Hook、不逆向网络协议、不联网下载、不模拟输入，也不会启动 League of Legends；当前新增网络访问仅限硬编码 loopback 的 LCU 与 Live Client Data API 白名单接口。
- 当前安全源码扫描和最终 PE 禁用 API/import 命中均为 0。

## Phase 2 实现

检测器支持已实现的 16:9 与 16:10 三卡布局家族；下列 320×180 数值仅是 16:9 合成 plumbing 示例，不是当前 2560×1600 real calibration。归一化 ROI 转像素采用：

```text
left=floor(x*W), top=floor(y*H)
right=ceil((x+width)*W), bottom=ceil((y+height)*H)
```

offer ROI 为 `(0.10,0.10,0.80,0.80)`；LEFT/CENTER/RIGHT 分别为 `(0.15,0.20,0.20,0.55)`、`(0.40,0.20,0.20,0.55)`、`(0.65,0.20,0.20,0.55)`。在 320×180 上，offer=`(32,18,256,144)`，三卡依次为 `(48,36,64,99)`、`(128,36,64,99)`、`(208,36,64,99)`。亮度、边缘密度和三列一致性形成检测分数，必须连续 3 个合格处理帧才进入稳定态。

OCR 后端固定为 `Windows.Media.Ocr` 的 `zh-CN`。每卡保留 aggregate text，并从最多 8 行、每行最多 512 bytes 的 OCR 行生成单行和相邻双行候选。候选集按 `KIWI`/`KIWI_JADE` mode 收窄；匹配依次执行 exact、NFKC/小写/去空白标点后的 normalized、以及有编辑距离/比例/分数/top1-top2 margin 上界的 fuzzy。三张卡必须跨连续 OCR 帧得到相同 ID 共识；fuzzy 结果还要求至少一帧具备 exact/normalized 强证据。

Phase 2 icon manifest 包含 245 个模板记录，由 163 个唯一 PNG 支持，并接入 difference-hash matcher。模板链路接通不等于真实识别准确率验收；Windows OCR 不提供置信度，输出中的 lexical/final confidence 仍是非校准排序启发式，不是准确率概率。

知识库 `data/knowledge/augments.zh-CN.json` 有 655 条记录、655 个唯一 technical `id`、641 个唯一 `numeric_id`；重复 numeric ID 仅 sentinel `-1`。CLI 支持 `KIWI` 和 `KIWI_JADE`。

每次运行会输出逐行 JSON，并在 workspace 的 `phase1-*` session 目录写入 `session.sqlite3` 和 `events.jsonl`。SQLite schema 包含 `schema_version`、`sessions`、`augment_offers`、`augment_choices`、`recognition_results`、`artifacts`；接受 offer 时还写 RAW、LEFT、CENTER、RIGHT PNG 和 sidecar JSON。正常错误路径有 SQLite transaction、JSONL 截断补偿和 artifact 清理，但 SQLite/JSONL/artifacts 之间的断电级 crash atomicity 仍为 **OPEN**。

## 源码树构建与运行

前置条件：64-bit Windows、Visual Studio 2022/MSVC x64、带 C++/WinRT 的 Windows SDK、CMake、CPython 3.11。脚本不会下载依赖。

```powershell
.\scripts\build.ps1
.\scripts\test.ps1
.\scripts\run.ps1 --help
.\scripts\run_replay.ps1 -ReplayPath .\sample.png
.\scripts\run.ps1 --window-title "League of Legends (TM) Client" --max-seconds 10
```

源码树默认构建目录为 `outputs/tmp/build`，知识库为 `data/knowledge/augments.zh-CN.json`，workspace 为 `outputs/runtime`。Replay 支持单图、图片目录和 JSONL manifest；Live 可用 `--list-windows`、`--hwnd` 或唯一的 `--window-title` 子串。

## 独立发布包

运行时 icon manifest 按以下顺序解析，不依赖编译期源码绝对路径：

1. exe 目录相对 `../data/knowledge/augment_icons/manifest.json`；
2. 当前工作目录的 `data/knowledge/augment_icons/manifest.json`。

从指定 fresh Release build 确定性重建发布包：

```powershell
.\scripts\package_phase2.ps1 `
  -BuildDirectory .\outputs\tmp\phase2-release `
  -DestinationDirectory .\result
```

脚本只接受 `result` 或 workspace 内 `outputs/tmp` 作为目标，生成 `SHA256SUMS.txt`，并拒绝 PE 中仍含源码绝对 manifest 路径的 exe。发布内容包括 catalog、manifest 及其引用的 163 icons、config、运行脚本、dataset/benchmark/import 工具和文档；不会包含真实 dataset、runtime、构建树、VC runtime 或游戏文件。

```powershell
.\result\scripts\run.ps1 --help
.\result\scripts\run_replay.ps1 -ReplayPath C:\path\frame.png
.\result\scripts\run.ps1 --window-title "League of Legends (TM) Client" --max-seconds 10
```

复制整个 `result` 后，脚本自动使用 `result/bin/lol_augment_assistant.exe`、`result/data/knowledge/augments.zh-CN.json` 和 `result/runtime`，可从任意 cwd 启动，不需要源码树，也不会隐式下载。

## 已验证与未验收边界

- 最终 Release 构建 `outputs/tmp/build_phase2_final_verify` 的 CTest 为 **21/21 passed**；该构建 exe 与 `result/bin/lol_augment_assistant.exe` 的 SHA-256 同为 `b0726f47230e1f98ee4124091fec0de66e7bfa9d9bfffc71f6ffa1fda1a26075`，版本为 `0.2.0 (Phase2 portable)`。
- Dataset validator：9 个 real bundle 均结构有效；其中 6 个 2560x1600 original-WGC、3 个 1280x800 preview-derived。5 个 original-WGC 被弹窗遮挡并人工 skip，3 个 preview-derived 只作校准，最终只有 clear n5 的 **1 个独立 offer / 3 张卡**可进入 original-WGC 指标。
- 同版本最终 producer 以 headless、无 preview 模式 replay 4 个完整标注样本，`status=ok`、operational failure=0；clear n5 被识别，3 个 preview-derived 结果不进入 original-WGC 指标。
- 最终 benchmark 的 screen 1/1、OCR 3/3（NFKC+casefold、去 Unicode 空白/标点后的 normalized exact）、final card 3/3、three-card 1/1 均只来自 1 个 2560x1600 offer，绝不能表述为稳定准确率。Icon 为 0/3，三槽均 UNKNOWN；wall latency avg/p95 均为 934.8124 ms。
- 多分辨率、已知 UI scale、HDR/SDR、hover/highlight、跨 patch、长时运行、误报/漏报与 crash atomicity 仍未验收。Phase 3 暂缓，先扩充经授权的 original-WGC holdout。

## Phase 2 capture boundary 与 benchmark 隔离

`provenance=real` 只表示画面来自真实场景，不再等价于“可进入 original-WGC 指标”。Phase 2 metadata 支持以下可选 typed 字段；它们保持 schema v1 和既有 metadata 的读取兼容：

```json
{
  "source": {"kind": "windows_graphics_capture", "reference": "..."},
  "derived_from_preview": false,
  "capture_boundary": "original_wgc",
  "benchmark_use": "original_wgc_metrics"
}
```

- `capture_boundary` 为 `original_wgc | preview_derived | other_real_capture | synthetic | unknown`。
- `benchmark_use` 为 `original_wgc_metrics | real_scenario_calibration | excluded`。
- 只有 `provenance=real`、`source.kind=windows_graphics_capture`、非 preview-derived、`capture_boundary=original_wgc` 且 `benchmark_use=original_wgc_metrics` 的完整有效标注样本才可进入 original-WGC benchmark。
- `derived_from_preview=true`、`source.kind=manual_capture`、`source.kind=debug_preview`、`capture_boundary=preview_derived` 中任一信号都会强制得到 effective `capture_boundary=preview_derived`、`benchmark_use=real_scenario_calibration` 和 `benchmark_eligible=false`。这类样本仍保留在 `real` 分桶作为真实场景校准素材，不改成 synthetic，也不删除。
- validator 对缺少 typed 字段的 legacy metadata 按现有 `source`/preview 信号保守推断并输出 warning。独立 benchmark 输入若完全没有来源边界字段，为保持旧 bundle/JSONL 可读与既有调用兼容，会带明确 warning 沿用旧的 original-WGC 假设；一旦出现任一边界信号，就按上述严格规则过滤，显式 `provenance=real` 不能覆盖 preview 排除。

Validator 报告分别给出 `capture_boundary_counts`、`benchmark_use_counts`、`preview_derived_sample_count`、`skipped_excluded_sample_count` 和 `original_wgc_benchmark_eligible_sample_count`；benchmark 的总指标、分层、延迟和失败样本均只使用同一 original-WGC eligible 集合。

Phase 2 可移植发布审计见 `outputs/phase2_portable_package_report.md`；发布包内文档见 `result/Exp/README.md`。

## Phase 2 最终交付：10 项状态

1. **只读安全边界：完成。** 不读写进程内存、不注入/Hook、不联网、不模拟输入、不启动游戏。
2. **Headless 与前台安全：完成。** collect 默认不启用 preview；preview 只能显式 `--preview`，窗口使用 `WS_EX_NOACTIVATE`/`SW_SHOWNOACTIVATE`。产品不调用前台/焦点切换或输入注入 API；F8 仅由 `GetAsyncKeyState(VK_F8)` 观察。
3. **Live 目标校验：完成。** 只接受当前可见、标题大小写完全等于 `League of Legends (TM) Client` 的顶层窗口；launcher、子串、大小写不符、歧义及二次枚举失效均 fail closed。
4. **样本采集：完成。** detector suspect 或人工 F8 可在 accepted gate 之外写 RAW/三卡/metadata，带间隔+dHash 去重、临时目录提交和失败回滚。
5. **Dataset 契约与工具：完成。** schema、annotator、validator 明确区分 original-WGC、preview-derived、synthetic、skip 与 benchmark eligibility。
6. **Detector/ROI：完成本阶段定向修复。** clear n5 在 2560x1600 为 visible；5 个弹窗遮挡样本保持 UNKNOWN；仅对当前实测分辨率成立。
7. **OCR：链路完成，泛化未验收。** 最终产品 staged OCR 已导出，clear n5 为 3/3 normalized exact；但只有 1 个独立 offer，不能作为产品最优参数或稳定准确率。
8. **Icon 与融合：链路完成，准确率未验收。** 245 templates/163 PNG 可移植加载；n5 生产 matcher 仍为 0/3 accepted，OCR-only 活性修复不等于 icon 准确。
9. **Replay → benchmark：完成。** producer 严格解析 JSONL、按 sample_id join，并从全部指标排除 preview-derived/其他非 original-WGC 数据。
10. **Portable release：完成。** final build/replay/result exe 同版本，exe SHA-256=`b0726f47230e1f98ee4124091fec0de66e7bfa9d9bfffc71f6ffa1fda1a26075`；发布包携带 catalog、manifest/icons、dataset/benchmark 工具。交付时按 `result/SHA256SUMS.txt` 逐项验证；清单不记录自身哈希，最终 stage2 另有独立 handoff 清单。

## 精确运行与复核命令

前置条件：PowerShell、64-bit Windows；源码构建另需 VS2022/MSVC x64、Windows SDK/C++/WinRT、CMake、CPython 3.11。所有命令均从仓库根目录执行。不要复用任何历史 HWND；HWND 是瞬时值。推荐始终使用精确标题选择器。

```powershell
# build / test
.\scripts\build.ps1 -Configuration Release -BuildDirectory .\outputs\tmp\build_phase2_final_verify
.\scripts\test.ps1 -Configuration Release -BuildDirectory .\outputs\tmp\build_phase2_final_verify

# portable help / version
.\result\scripts\run.ps1 --help
.\result\scripts\run.ps1 --version

# headless live run：精确标题，不启用 preview
.\result\scripts\run.ps1 --window-title "League of Legends (TM) Client" --max-seconds 30

# headless collect：自动 suspect；如需人工 F8，移除 --no-hotkey
$dataset = (Resolve-Path .\data\dataset\augment_offers).Path
.\result\scripts\run.ps1 --window-title "League of Legends (TM) Client" --collect-samples --dataset-root $dataset --no-hotkey --max-seconds 600

# 单帧 replay：无 preview
$frame = (Resolve-Path .\data\dataset\augment_offers\real\sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6\RAW.png).Path
.\result\scripts\run_replay.ps1 -ReplayPath $frame -Mode KIWI -Workspace .\result\runtime\n5-replay -MaxSeconds 30 -Once

# validate；stdout 是 JSON
py -3.11 -B .\scripts\phase2\validate_dataset.py --dataset-root .\data\dataset\augment_offers --knowledge .\data\knowledge\augments.zh-CN.json

# dataset -> replay -> benchmark，输出写到 dataset 外
py -3.11 -B .\scripts\phase2\run_dataset_replay.py --exe .\result\bin\lol_augment_assistant.exe --dataset-root .\data\dataset\augment_offers --knowledge .\data\knowledge\augments.zh-CN.json --output-dir .\outputs\tmp\phase2-replay-benchmark --timeout-seconds 30

# 对 producer 的 split JSONL 再独立 benchmark
py -3.11 -B .\scripts\benchmark_phase2.py --dataset .\outputs\tmp\phase2-replay-benchmark\benchmark_input --json-output .\outputs\tmp\phase2-replay-benchmark\benchmark_report.json --markdown-output .\outputs\tmp\phase2-replay-benchmark\benchmark_report.md
```

最终真实性与失败样本见 `outputs/phase2_final_report.md`。Phase 3 当前明确暂缓；下一轮只做 authorized original-WGC 数据扩充、分层 holdout 与未关闭边界验证。

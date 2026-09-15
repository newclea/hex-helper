# Wave3-D Capture/Vision 独立代码审查

审查日期：2026-08-25（Asia/Shanghai）  
审查范围：`src/capture/**`、`src/detector/**`、`src/vision/**`、`src/knowledge/**`、`src/replay/**`，以及对应 `include/**`、`tests/**`、`data/knowledge/**`。仅为确认产品 ROI 配置与构建目标，点查了少量调用/构建行；未深入审计 `app/storage/output`。未联网、未使用 IDA、未逆向 LoL、未修改业务代码、未使用 Git。

## 结论

**Phase1：阻断。** 当前代码已经具备可运行的 capture→ROI→OCR→matcher→replay plumbing，且新鲜 MSVC/x64 构建与现有定向测试全部通过；但仍有 5 个 P1：整卡 OCR 的文本边界错误、fuzzy 歧义 margin 被虚高、单帧 lexical score 被当作最终置信度、WGC resize/device-lost 可静默停流、OCR `noexcept` 边界可终止进程。若 Phase1 的验收只是“合成 plumbing 能跑”，它已通过；若验收包含“可对真实画面可靠识别并持续运行”，则不能放行。

**Phase2 首要顺序：**

1. 先修 P1-01/P1-02/P1-03，建立 title 子 ROI/行选择、多帧 OCR 共识和真实校准；这是准确率底座。
2. 再修 P1-04/P1-05 和 P2-01，补齐 WGC 设备恢复、resize 失败状态、OCR 异常边界及有时间约束的稳定确认；这是持续运行底座。
3. 上 icon 模板并处理 CHERRY 同名项（P2-05），随后为 replay/catalog 增加资源预算和 fail-closed 完整性检查（P2-02/03/04）。

发现计数：**P0=0，P1=5，P2=5，P3=2**。

## Findings

### P0

无。

### P1-01 — OCR 对整张卡片调用 `Result.Text()`，没有 title 子 ROI/行选择

位置：`src/vision/recognition_pipeline.cpp:33-42`、`src/vision/ocr.cpp:170-175`、`src/vision/text_matcher.cpp:276-295`。

证据：pipeline 将 `stable_detection.rois->cards[index]` 整卡裁剪直接送 OCR；OCR 返回整个结果的 `result.Text()`；matcher 随后把整段文本与单个强化符文标题做 exact/normalized/编辑距离匹配。代码没有标题区域、OCR `Lines()`/bounding box 选择、首行选择或标题+描述拆分。只要 Windows OCR 同时读出描述，输入长度差就会越过 edit-ratio/edit-distance 硬界并返回 Unknown。

现有测试没有覆盖该边界：`recognition_pipeline_test` 的 fake OCR 无视 crop 并直接返回纯标题；`ocr_smoke_test` 只识别纯白图。合成卡片也没有真实标题/描述排版。

影响：真实卡片容易系统性 false-negative；这不是“缺样本才不知道”的问题，而是当前接口把多行整卡文本送入单标题匹配器的确定性结构错误。

建议：为每张卡定义独立 title ROI，或使用 OCR 行 bounding box 只生成受限标题候选；保留整卡 OCR 仅作诊断。新增“标题+描述同时被识别”“标题分两行”“描述先于标题返回”等测试。

### P1-02 — fuzzy 的 top2 只在硬编辑距离内计算，歧义 margin 会被虚高

位置：`src/vision/text_matcher.cpp:281-321`。

证据：候选先经 `distance <= bound` 过滤后才进入 `scored`，top2 再从 `scored[1]` 取得。一个接近 top1、但刚好位于 hard bound 外的真实 top2 会被当作不存在，`top2_score` 变成 0，margin 被放大。

审计探针用默认配置复现：输入 `abcdefghij`，top1 距离 2（score 0.8），top2 距离 3（真实 score 0.7）。实现返回：

```text
FUZZY matched=1 id=top1 top1=0.8 top2=0 margin=0.8 reason=fuzzy_match
```

真实 top1-top2 margin 应为 0.1，小于配置的 0.12，本应拒绝。

影响：违反“bounded fuzzy + ambiguity rejection”的核心契约，可把有近邻歧义的 OCR 文本错误映射到一个 ID。

建议：距离阈值用于 top1 可接受性，但 margin 必须基于全候选（至少基于能影响 margin 的候选）计算；按唯一 augment ID 聚合后再算 top1/top2。

### P1-03 — 单帧 lexical match 被当作最终置信度；无 OCR/icon 置信度时 exact 直接为 1.0

位置：`src/vision/text_matcher.cpp:235-274`、`src/vision/recognition_pipeline.cpp:56-76`、`include/lol_assistant/vision/ocr.h:40-42`。

证据：exact/normalized 的 `top1_score` 固定为 1.0；Windows.Media.Ocr 明确没有置信度；icon 在本阶段始终 unavailable。pipeline 因而把 lexical score 原样写入 `final_confidence`。审计探针复现真实后端同样的“无 OCR confidence + 无 icon”条件：

```text
NO_CONFIDENCE exact_final=1 icon_available=0 state=4
```

`ConsecutiveFrameConfirmer` 只确认页面可见性，不保存或比较三张卡的识别结果；因此这是稳定页面后的单帧 OCR 决策，不是多帧标题共识。OCR 若恰好误读成目录里的另一个合法标题，会以 1.0 被标记为 Recognized。

影响：`final_confidence` 不是校准概率，不能安全充当自动接受阈值；当前命名和数值会掩盖错误 exact/normalized 结果。

建议：把 edit score 明确命名为 lexical similarity；没有后端置信度时不要合成“1.0 最终置信度”。至少要求跨帧相同 ID 共识或独立 icon corroboration，并用真实标注集校准最终分数。

### P1-04 — WGC resize/device-lost 异常可能保持 Running 但永久停流

位置：`src/capture/windows_graphics_capture_source.cpp:405-415`、`:500-516`、`:611-614`。

证据：resize 分支先将 `accepting_frames_` 设为 false，再执行可能抛出 HRESULT 的 `frame_pool_.Recreate(...)`。该异常被外层 per-frame catch 吞掉，只记录 last error/增加 dropped；没有恢复 `accepting_frames_`、没有 `Failed` 状态、没有重建 device/pool。后续 callback 在 `accepting_frames_ == false` 时直接返回，worker 队列为空，外部可看到 Running/Paused 但再无帧。普通 `Map`/copy 的 device-removed/reset 错误也只按单帧 drop 处理，D3D device 从不重建或 fail-fast。

影响：窗口快速 resize、显卡 reset/device removed 等实战故障可变成无界静默停流，调用方没有公开错误通道恢复。

建议：将 resize 设计成事务式状态迁移；失败时恢复旧 pool 或进入 Failed 并提供可重启错误。识别 `DXGI_ERROR_DEVICE_REMOVED/RESET/HUNG`，统一 teardown 后重建 device/session，或明确失败让上层重启。

### P1-05 — `Recognize() noexcept` 的 WinRT 调用位于 try 外，HRESULT 可触发 `std::terminate`

位置：`include/lol_assistant/vision/ocr.h:61-63`、`src/vision/ocr.cpp:102-143`。

证据：`WindowsMediaOcrTitleRecognizer::Recognize` 声明为 `noexcept`，但 `factory.MaxImageDimension()` 位于 line 143 才开始的 try/catch 之前。C++/WinRT 的 `MaxImageDimension()` 通过 `check_hresult` 抛 `hresult_error`；一旦该调用失败，不能转成 `RecognitionFailed`，而会穿越 `noexcept` 直接终止进程。

影响：OCR runtime/activation factory 异常可以把单卡识别故障升级为进程崩溃。

建议：把 activation、`MaxImageDimension`、bitmap 构造、engine 创建和 async `.get()` 全部纳入同一异常边界；或者移除 `noexcept` 并在调用层统一转换。加入可抛 fake/static adapter 的异常测试。

### P2-01 — “连续帧”只约束已转换 frame_id，不约束源帧丢失或时间间隔

位置：`src/capture/windows_graphics_capture_source.cpp:405-415`、`:558-562`；`src/detector/augment_screen_detector.cpp:215-247`。

证据：WGC 的 frame ID 在 CPU 转换成功后才递增；callback queue drop、转换失败和 resize stale frame 都没有占用 ID。confirmer 只检查 `current.frame_id == previous + 1`，也没有最大时间间隔或 source/session identity。因此发生任意数量的源帧 drop 后，交付给 detector 的三个 frame 仍可编号连续；长暂停后的下一帧也可继续同一确认窗口。

影响：多帧稳定确认弱于名字/测试表达的语义，不能证明三个相邻采集时刻均可见。

建议：在 callback 接收时分配 source sequence，向 CPU frame 传播；confirmer 同时约束 source ID、source sequence 和最大 capture timestamp gap。对 drop、pause/minimize、restart 明确 reset。

### P2-02 — Replay eager decode 没有帧数/单帧/总解码字节预算

位置：`src/replay/image_replay_source.cpp:668-719`、`:738-753`；`src/replay/wic_image_codec.cpp:164-185`。

证据：manifest/目录条目数无上限，所有图片在构造期解码并永久保存在 `frames_`；同一路径可重复列出；WIC 只受单个 buffer 的 `UINT_MAX` 上限约束，没有合理像素上限或 aggregate decoded bytes 上限。播放时 `common::Frame frame = frames_[cursor_]` 又完整复制一次像素 buffer。

影响：小 manifest + 高压缩大图或大量重复项可在播放前耗尽内存/commit，eager fail-fast 变成资源耗尽入口。

建议：OpenOptions 增加 `max_frames`、`max_pixels_per_frame`、`max_decoded_bytes`；在 decode 前读取尺寸并累计 checked budget；考虑 immutable shared frame buffer 或按需 decode/cache。

### P2-03 — Manifest 未知字段递归解析无深度限制

位置：`src/replay/image_replay_source.cpp:430-485`。

证据：`JsonLineReader::SkipValue()` 对未知 object/array 递归调用自身，没有 depth 参数；knowledge JSON parser 已有 64 层上限，manifest parser 没有。深层损坏 JSONL 可先耗尽线程栈，绕过预期的 `ReplayError` 错误返回。

影响：损坏/恶意 manifest 可能导致进程 stack overflow，而不是 eager validation 的可控拒绝。

建议：传递并限制 nesting depth，同时限制每行长度、manifest 总字节和 entry 数。

### P2-04 — Runtime catalog loader 不验证 655/已知模式/生成校验元数据

位置：`src/knowledge/augment_catalog.cpp:349-373`、`:417-469`。

证据：loader 只要求 metadata 非空且 records 非空；任意非空 mode 字符串均被接受；顶层 `sources`/`validation` 被忽略。部署文件若被截成一个语法正确记录、模式被拼错、或验证计数与实际记录不一致，仍返回 Loaded。655 和 44/220/188 只由测试/生成器检查，不是运行时 fail-closed 契约。

影响：损坏或错版本 catalog 可静默缩小 candidate 集、造成 mode 过滤漏项，并仍携带看似合法的 `catalog_version`。

建议：schema v1 loader 明确验证 record count、locale、允许 mode 集、metadata counts，并校验生成 bundle/hash；至少把 expected counts 放入受版本控制的 schema contract，而非只放测试。

### P2-05 — CHERRY 当前存在同 mode 同中文名的两个 ID，而 icon 信号不可用

位置：`data/knowledge/augments.zh-CN.json:2826-2835`、`:5810-5819`；`src/vision/text_matcher.cpp:240-245`；`src/vision/recognition_pipeline.cpp:67-75`。

证据：`DustToDiamonds` 与 `TrashToTreasure` 均属于 CHERRY，display_name 都是“变废为宝”。mode 过滤后仍有一个 normalized ambiguity group。matcher 正确地 fail closed 为 `ambiguous_exact_match`，但 pipeline 没有 icon matcher 注入，`icon_match` 永远保持 Unavailable，所以这两个当前记录无法靠现有链路区分。

影响：这是确定性 coverage hole，不是概率性准确率问题；遇到该标题时必然 Unknown/拒绝。

建议：Phase2 首批 icon 模板必须覆盖此冲突；也可引入受版本控制的 disambiguation metadata。不要按记录顺序选一个。

### P3-01 — Manifest SHA 错误使用 entry index 伪装源行号

位置：`src/replay/image_replay_source.cpp:594-631`、`:694-703`。

证据：ReadManifest 会跳过空白行，但 `NormalizeSha256` 接收的是 `index + 1`，ManifestEntry 不保存原始 `line_number`。有空白行时，`sha256` 格式错误报告的行号不正确。

影响：只影响损坏 manifest 的定位与复核，不改变接受/拒绝结果。

建议：把 source line 存入 ManifestEntry，后续 dimension/hash/path 错误统一带真实行号。

### P3-02 — WGC 没有传播源时间戳，`capture_started` 实际是 CPU 转换开始

位置：`src/capture/windows_graphics_capture_source.cpp:488-492`、`:583-586`。

证据：`conversion_started` 被写入 `timestamps.capture_started`，WGC frame 的 `SystemRelativeTime()` 没有写入 `source_timestamp`。因此结构中的 capture latency 只是 GPU→CPU 转换耗时，不包含 frame 在 pool/queue 中等待的年龄。

影响：诊断指标会低估端到端 capture age，也无法为 P2-01 提供可靠时间连续性。

建议：传播 WGC source timestamp，并把 conversion timing 与 capture timing 分成不同指标。

## 已验证的确定性边界

- **WGC ownership/stride：** callback 的 dropped frame 显式 Close，worker 用 guard 关闭已取 frame；GPU mapped RowPitch 按行拷入 owning、tight BGRA8（`stride=width*4`）buffer。队列容量被 clamp 到 1..3，overflow drop-oldest，latest CPU frame 替换也计 dropped。
- **ROI/16:9：** normalized ROI 验证 finite/positive/contained/non-overlap；floor(left/top)+ceil(right/bottom) 后再次检查 frame bounds；crop 尊重输入 padded stride 并生成 owning tight crop。1366×768、1920×1080、2560×1440 通过，1024×768 被拒绝。
- **SoftwareBitmap：** 只接受 tight owning crop，DataWriter/SoftwareBitmap 拷贝位于异常边界内；没有借用原 frame 内存。
- **exact/normalized：** 多 ID 同名 exact/normalized 会拒绝，不按 catalog 顺序选；mode 过滤确实缩小 candidate 集。缺陷集中在 fuzzy top2 计算，而不是 exact 的 fail-closed 分支。
- **icon unavailable：** 无模板时 state=Unavailable、id/confidence 均空；pipeline 没有把 unavailable 当 0 分的负证据。语义真实，但目前没有任何 disambiguation 能力。
- **catalog 当前文件：** 655 records、655 个唯一 technical ID；numeric ID 有 641 个唯一值，唯一重复值是 sentinel `-1`（15 records），且 metadata 明示 `duplicate_numeric_ids:[-1]`。模式计数为 CHERRY=44、KIWI=220、KIWI_JADE=188。normalized 同名组：ALL=122、CHERRY=1、KIWI=0、KIWI_JADE=0。
- **Replay 基础 fail-fast：** 当前实现会在构造期拒绝 unsupported/corrupt image、dimension mismatch、SHA-256 mismatch；manifest 顺序和 directory natural sort 测试通过。
- **Windows apartment：** 本机 zh-CN OCR backend 可用；未初始化/MTA smoke 成功，显式 STA 下 `Probe()` 与 blank `Recognize()` 也成功。`RPC_E_CHANGED_MODE` 路径在本机没有复现功能失败；P1-05 是独立的异常边界问题。

## 测试与探针证据

所有新生成物均位于 `outputs/tmp/audit_vision/**`。使用 Visual Studio 2022、MSVC 19.44、x64、Windows SDK 10.0.26100.0，项目 `/W4 /WX` 新鲜构建成功。

```text
detector_test:             exit 0, checks=36 failures=0
text_matcher_test:         exit 0, checks=17 failures=0
recognition_pipeline_test: exit 0, checks=35 failures=0
augment_catalog_test:      exit 0, checks=18 failures=0
replay_tests:              exit 0, 7 cases passed
ocr_smoke_test:            exit 0, blank synthetic OCR only
capture_minimal_test:      exit 0, 4 cases / 90 assertions
  WGC smoke: received=29 converted=28 dropped=27 queued=0/3
  resize observed: 698x469 -> 938x529; close terminal state observed
vision_audit_probe:        exit 0
  fuzzy omission reproduced
  exact/no-confidence final=1 reproduced
  catalog counts/ambiguities recomputed
  actual STA OCR probe/recognize passed
```

`augment_import_determinism` 未运行：该测试依赖预先提取的静态源，并固定写入 `outputs/tmp/vision_worker/**`，超出本次允许的临时根；已审查其实现，并对 checked-in catalog 独立重算记录、ID、mode 与 title 冲突数据。

## 实战未验证项（不等同于代码 finding）

### 合成 plumbing 已验证

- 基础 WGC callback→queue→worker→CPU frame、一次 resize、window close、幂等 Start/Stop。
- 16:9 ROI 数学、padded stride crop、合成三列 detector、三帧 visible gate。
- fake OCR pipeline gate、exact/normalized/fuzzy 基本路径、unknown/duplicate title 拒绝。
- blank Windows OCR backend smoke、SoftwareBitmap 基础转换。
- 小型 PNG/JPEG replay、corrupt extension/content、manifest order/dimension/hash。

### 准确率/耐久性仍未验证

- 没有任何真实 LoL frame fixture；测试树中没有 PNG/JPEG/JSONL 实战样本。
- detector 没有真实正/负样本 ROC、误报率、漏报率；合成棋盘仅证明算法 plumbing。
- 没有 title+description 实卡 OCR、简体中文标题准确率、不同字体/缩放/抗锯齿/亮度/遮挡测试。
- 没有 1366×768/1080p/1440p 实卡 ROI 对齐；没有 window border/client-area、DPI、letterbox、最小化/恢复、HDR/SRGB、21:9 行为验证。当前算法有意只接受近似 16:9。
- 没有 rapid resize/close callback race、D3D device removed/reset/hung、GPU/driver reset 注入和长时间 soak。
- 没有跨帧标题共识、真实 unknown/new-title corpus、fuzzy 阈值校准或最终 confidence calibration。
- 没有 icon templates，因此 icon accuracy、同名消歧和 unavailable→available 迁移均未验证。
- Replay 没有大图/大量帧/深层 manifest/内存预算/长循环 soak；eager decode 的峰值内存没有量化。

## Phase gate 关闭条件

Phase1 至少应满足：P1-01..05 全部关闭；P2-01 加入 source sequence/time reset；新增真实 title+description fixture，证明整卡文字不会污染 title 匹配；WGC device-lost/resize-failure 测试能恢复或明确 Failed；真实 OCR exact 结果不得在无独立信号时伪装成 calibrated 1.0。

Phase2 再以标注集给出 detector precision/recall、每 resolution 的 OCR top-1/unknown/ambiguity 指标、置信度可靠性曲线，并交付 icon 模板覆盖 CHERRY 同名冲突。只有这些数据闭环后，才能把“算法可运行”升级为“识别准确率可承诺”。

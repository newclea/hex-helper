# Wave5 最终回归审计报告

审计日期：2026-08-25（Asia/Shanghai）  
工作根：`F:\Realworld\lol`  
方法：只读 Wave3 两份报告及 `include/src/tests`；未重新构建；直接运行 `outputs/tmp/wave4_main_verify/bin` 中指定的 7 个既有测试；额外执行一次限定到 `include/src/tests` 的精确 `rg`。未联网、未逆向、未启动 LoL、未修改业务代码或测试。

## 最终结论

- Wave3 的 10 项确定性 P1 修复：**10 CLOSED / 0 PARTIAL / 0 OPEN**。
- 独立开放风险：**SQLite / JSONL / artifact 跨介质 crash atomicity = OPEN**。现有进程内回滚不能证明进程终止、掉电或 SQLite `COMMIT` 窗口下的原子一致性。
- 新 P1：**在本次严格限定的源码与测试边界内未发现新 P1**。这不是对未审计范围的无条件背书。
- 真实准确率：**UNKNOWN**。仓库与本轮测试没有真实 LoL 截图；`ocr_smoke_test` 明确只使用空白合成图，因此不能证明真实游戏字体、标题/描述排版、缩放、亮度、遮挡或分辨率下的识别准确率。

## 10 项逐条裁决

### 1. OCR description 污染 — CLOSED

源码证据：

- `OcrTextResult::line_candidates` 在 `include/lol_assistant/vision/ocr.h:39-49` 提供有界行候选。
- `WindowsMediaOcrTitleRecognizer::RecognizeCore` 在 `src/vision/ocr.cpp:202-220` 从 `result.Lines()` 提取有界候选，同时仅把 `result.Text()` 保留为原始诊断文本。
- `BuildMatchInputs` / `MatchOcrCandidates` 在 `src/vision/recognition_pipeline.cpp:28-122` 分别尝试单行与相邻两行；不同候选若命中不同 ID，则以 `conflicting_ocr_candidate_ids` fail closed，而不是从描述中选错一个 ID。

测试证据：`tests/vision/recognition_pipeline_test.cpp:135-200` 的 `TestLineCandidatesSelectTitleAndCombineAdjacentLines` 覆盖“描述+标题+描述”和拆成两行的标题，`TestConflictingLineIdsFailClosed` 覆盖冲突拒绝。`recognition_pipeline_test.exe`：`checks=95 failures=0`。

### 2. fuzzy top2 margin — CLOSED

源码证据：`BoundedTextMatcher::Match` 在 `src/vision/text_matcher.cpp:279-338` 先对全部 normalized candidates 计分，再按 augment ID 聚合、排序并计算 top1/top2；hard bound 只约束全局 top1 的可接受性，不再提前滤掉可影响 margin 的 top2。

测试证据：`tests/vision/text_matcher_test.cpp:72-92` 的 `TestFuzzyMarginUsesAllUniqueIds` 精确复现旧反例：top1=0.80、top2=0.70、margin=0.10，并验证重复 ID 先聚合。`text_matcher_test.exe`：`checks=28 failures=0`。

### 3. exact confidence 虚高 — CLOSED

源码证据：

- `NoConfidenceHeuristic` 在 `src/vision/recognition_pipeline.cpp:125-137` 将无后端置信度的 exact/normalized/fuzzy 明确映射为保守排序启发值 0.90/0.86/不高于 0.82。
- `AugmentRecognitionPipeline::Recognize` 在 `src/vision/recognition_pipeline.cpp:206-216` 明确声明这些值不是 calibrated probability；无 OCR confidence 的 exact 不再输出 final confidence 1.0。
- `AugmentFrameProcessor::Process` 在 `src/app/augment_frame_processor.cpp:391-419` 要求两个连续 OCR 结果保持相同 ID，并要求每张卡出现 exact/normalized 强证据；单帧 lexical exact 不再直接成为 accepted offer。

测试证据：`tests/vision/recognition_pipeline_test.cpp:135-176,203-237` 的 `TestLineCandidatesSelectTitleAndCombineAdjacentLines` 与 `TestNoConfidenceFallbacksAreExplicitHeuristics` 验证 exact=0.90、normalized=0.86、fuzzy ceiling=0.82；`tests/integration/augment_frame_processor_test.cpp:430-454,577-595` 验证两帧 OCR 共识及 fuzzy-only 不可自动接受。相关测试均退出 0。

剩余未知：这些启发值没有真实标注集校准，不能解释为真实概率；该未知不重新打开“exact 固定 1.0”的确定性缺陷。

### 4. OCR noexcept — CLOSED

源码证据：`WindowsMediaOcrTitleRecognizer::Recognize` 在 `src/vision/ocr.cpp:102-120` 是完整 `noexcept` wrapper，捕获 `winrt::hresult_error` 和所有其他异常；包括 `MaxImageDimension()` 在内的 WinRT 调用位于可抛的 `RecognizeCore`（`src/vision/ocr.cpp:122-220`）内，因此异常会被转换为 `RecognitionFailed`，不会越过 `noexcept`。

测试证据：`tests/vision/ocr_smoke_test.cpp:12-31` 的 `ThrowingWindowsOcr` 从 `RecognizeCore` 注入 `E_FAIL`，验证返回 `recognition_failed:0x80004005`。`ocr_smoke_test.exe` 退出 0。

### 5. WGC resize / device-lost — CLOSED

源码证据：

- `CaptureStateMachine::BeginResize` / `CompleteResize` / `Fail` 位于 `include/lol_assistant/capture/capture_state_machine.h:132-161,242+`，失败终态为公开的 `CaptureState::Failed`。
- `WindowsGraphicsCaptureSource::ConvertFrame` 在 `src/capture/windows_graphics_capture_source.cpp:533-568` 捕获 `frame_pool_.Recreate` 的 HRESULT/标准/未知异常并调用 `FailCapture`，不会停留在 Running/Paused 静默停流。
- conversion worker 在 `src/capture/windows_graphics_capture_source.cpp:409-431` 将 HRESULT conversion/device failure 转为 Failed；`GetDeviceRemovedReason` 在 `src/capture/windows_graphics_capture_source.cpp:601-604` 显式检查设备状态。
- `IsUnrecoverableGraphicsError` 在 `include/lol_assistant/capture/capture_state_machine.h:55-68` 覆盖 REMOVED/HUNG/RESET 等关键 DXGI 错误。

测试证据：`tests/capture/capture_minimal_test.cpp:240-278` 的 `TestResizeFailureStateTransition` 和 `:280-330` 的 `TestDeviceLostFailureStateTransition`；实跑还完成真实可见测试窗口 WGC smoke。`capture_minimal_test.exe`：6 cases、137 assertions、0 failures。

说明：修复策略是明确 fail-fast 并允许上层 Stop/Start，而不是内部自动重建设备；它关闭了原“保持 Running 但永久停流”的确定性故障。

### 6. 单图 / `--once` 稳定门 — CLOSED

源码证据：

- `kStaticReplayPassCount=5` 位于 `src/app/cli.h:29`。
- `RunReplay` 在 `src/app/main.cpp:595-629` 对单图生成 5 个有连续 frame ID 的静态分析 pass，使 detector 的三帧稳定门和两帧 OCR 共识有机会完成；`--once` 通过 `ShouldStopAfterRecognitionAttempt` 在识别尝试完成后停止，而不是第一张 source frame 后停止。
- `RecognitionProgress`、`RecordRecognitionObservation`、`ShouldStopAfterRecognitionAttempt`、`ResolveRecognitionSessionStatus` 位于 `src/app/cli.h:31-35,85-96` 与 `src/app/cli.cpp:321-356`；EOF 无稳定观察时明确返回 `completed_no_stable_observation`，不再伪装成普通 `completed`。

测试证据：`tests/integration/cli_test.cpp:158-217` 的 `TestRecognitionStopPolicy` 与 `TestReplayCompletionStatusPolicy` 覆盖 5-pass、稳定前不停止、OCR consensus 未完成不停止、accepted/unknown/no-stable/timeout 状态。`cli_test.exe` 退出 0。

### 7. 页面内容变化和失败重试 — CLOSED

源码证据：

- `ComputeTitleContentSignature` / `IsSignificantContentChange` 声明于 `src/app/augment_frame_processor.h:98-103`，处理逻辑位于 `src/app/augment_frame_processor.cpp:294-326`；同屏内容变化需 2 帧确认后 `ResetContentAttempt`，不再只依赖页面 invisible。
- `RegisterRetryableFailure` 在 `src/app/augment_frame_processor.cpp:436-448` 实施有界指数 frame backoff；`content_attempt_exhausted_` 在 3 次失败后停止洪泛，内容变化后可重新武装。
- 已形成的 `persistable_consensus_` 在 `src/app/augment_frame_processor.cpp:339-345` 可直接重试持久化，无需重新 OCR。

测试证据：`tests/integration/augment_frame_processor_test.cpp:465-533` 的 `TestSameScreenContentChangeRearmsAfterFailure`、`TestBackendFailureRecoversWithBoundedBackoff`、`TestRetryableRecognitionFailuresStopAtThreeScans`，以及 `:535-559` 的持久化失败重试。`augment_frame_processor_test.exe` 退出 0，`FRAME_PROCESSOR_OCR_CALLS=12`。

### 8. round 递增 — CLOSED

源码证据：

- `PrepareStableOffer` 在 `src/state/phase1_game_state_reducer.cpp:183-242` 只以 `value_or(1U)` 建立首轮或替换当前轮 offer，不因新 fingerprint 自增 round。
- `AdvanceRound` / `ScreenDismissed` 在 `src/state/phase1_game_state_reducer.cpp:313-335` 是唯一明确推进路径，并在 round 4 fail closed。
- processor 仅在两帧 invisible 后调用 `ScreenDismissed`，见 `src/app/augment_frame_processor.cpp:256-274`。

测试证据：`tests/state/phase1_game_state_reducer_test.cpp:100-158` 的 `TestOfferCorrectionDoesNotAdvanceRound` 与 `TestExplicitDismissalAdvancesAndCapsRounds`；processor 集成测试还验证 `screen_dismissed_round_advanced`。`state_worker_tests.exe`：7/7 passed。

### 9. persist-before-commit — CLOSED

源码证据：

- `PrepareStableOffer` 在 `src/state/phase1_game_state_reducer.cpp:183-242` 只创建 proposed snapshot，不改 live state、revision 或 seen signature。
- `AugmentFrameProcessor::PersistConsensus` 在 `src/app/augment_frame_processor.cpp:482-522` 严格按 prepare → `PersistOffer/AcceptOffer` → `CommitPreparedOffer` 排序；持久化失败时不提交 reducer。
- `CommitPreparedOffer` 在 `src/state/phase1_game_state_reducer.cpp:245-273` 验证 base revision，并用 no-throw swap 同时提交 snapshot 与 seen signatures。

测试证据：`tests/state/phase1_game_state_reducer_test.cpp:160-210` 的 `TestPrepareCommitAndDiscardAreAtomic` 验证 discard/retry/stale commit；`tests/integration/augment_frame_processor_test.cpp:535-559` 验证首轮持久化失败后同 round、同 selected 可直接重试且不重新 OCR。`state_worker_tests.exe` 与 `augment_frame_processor_test.exe` 均退出 0。

边界：这里关闭的是进程内 reducer-before-persistence 顺序错误，不代表跨 SQLite/JSONL/artifact 的 crash atomicity 已关闭；后者见独立 OPEN 项。

### 10. selected 单次消费 — CLOSED

源码证据：`AugmentFrameProcessor::PersistConsensus` 在 `src/app/augment_frame_processor.cpp:476-483` 将当前 selected 与 prepared offer 一起准备；失败时保留事件供重试，只有持久化成功且 reducer commit 成功后才在 `:530-535` 执行 `selected_slot_.reset()`。因此 selected 被成功 offer 消费一次，而不是每轮复用。

测试证据：`tests/integration/augment_frame_processor_test.cpp:535-574` 的 `TestPersistenceRollbackRetryAndSelectedConsumption` 验证失败重试仍携带 `beta`，首个成功提交后第二轮不再携带 selection，且最终只持久化一次 selected。`augment_frame_processor_test.exe` 退出 0。

## 独立 OPEN：跨 SQLite / JSONL / artifact crash atomicity

结论：**OPEN（明确不因 10 项回归全绿而关闭）**。

当前顺序仍存在进程崩溃窗口：

- Create：`Phase1SessionRuntime::Create` 在 `src/app/session_runtime.cpp:488-503` 的 SQLite transaction callback 内先写并 flush JSONL；真正 SQLite `COMMIT` 在 `src/storage/session_store.cpp:714-718` 的 callback 返回之后。
- Accept：artifact 在 `src/app/session_runtime.cpp:701-713` 先落盘；accepted JSONL 在 `:794-798` 写并 flush；SQLite 同样随后才由 `SessionStore::RunInTransaction` commit。
- Close：`src/app/session_runtime.cpp:882-893` 在 transaction callback 内先写并 flush JSONL end，再到 SQLite commit。

`RemoveArtifacts`、JSONL truncate/reopen 和 SQLite rollback 只能补偿函数正常返回的失败，不能覆盖 artifact/JSONL 已持久化后进程被终止或掉电的窗口。本轮指定测试没有 crash injection、restart reconciliation、outbox/commit marker 或原子 rename 验证，因此必须保持 OPEN。

## 可复现测试记录

所有测试均从 `F:\Realworld\lol` 直接运行现有二进制；未重新构建。

| 可执行文件 | 结果 |
|---|---|
| `outputs/tmp/wave4_main_verify/bin/capture_minimal_test.exe` | exit 0；6 cases，137 assertions，0 failures |
| `outputs/tmp/wave4_main_verify/bin/text_matcher_test.exe` | exit 0；28 checks，0 failures |
| `outputs/tmp/wave4_main_verify/bin/recognition_pipeline_test.exe` | exit 0；95 checks，0 failures |
| `outputs/tmp/wave4_main_verify/bin/ocr_smoke_test.exe` | exit 0；异常边界通过；空白合成图 OCR smoke 通过 |
| `outputs/tmp/wave4_main_verify/bin/state_worker_tests.exe` | exit 0；7/7 passed |
| `outputs/tmp/wave4_main_verify/bin/augment_frame_processor_test.exe` | exit 0；integration passed；OCR calls=12 |
| `outputs/tmp/wave4_main_verify/bin/cli_test.exe` | exit 0；passed |

汇总：**7/7 executables passed，0 failed**。

## 一次源码安全边界精确 `rg`

仅执行一次，范围固定为 `include src tests`，文件类型固定为 `*.h/*.hpp/*.cpp`，模式只覆盖旧 P1 边界：`Result.Text()`、title line/ROI、top2、confidence、`MaxImageDimension`、WGC `Recreate`/DXGI device errors、single-frame/`--once`、内容签名/retry、prepare/commit/advance、selected 与 `AcceptOffer`。

结果与逐项源码核对一致：

- `Result.Text()` 仍只作为 `raw_text` 诊断输入，实际新增有界 `line_candidates`；冲突候选 fail closed。
- `MaxImageDimension()` 位于受 `Recognize` wrapper 捕获的 `RecognizeCore`。
- `frame_pool_.Recreate` 和 DXGI device-lost 有明确 Failed 转移及测试。
- app/state 命中内容签名、retry、prepare/commit、显式 `AdvanceRound`、selected 成功后 reset 等修复符号。
- 未发现旧 `stable_page_processed_` 门闩残留。

## 自检三问

1. 测试可复现？**是。** 固定了工作根、二进制路径、7 个测试名及退出结果，且未重新构建。
2. 结论均有符号和测试？**是。** 10 项均引用当前源码符号/行与对应测试案例；crash atomicity OPEN 也引用当前提交顺序。
3. 未知是否保持未知？**是。** crash atomicity 保持 OPEN；没有真实 LoL 截图，真实识别准确率保持 UNKNOWN；“无新 P1”仅限本次明确范围。

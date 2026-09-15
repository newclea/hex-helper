# Wave3-C App/Data 代码审查报告

审查日期：2026-08-25  
审查角色：Wave3-C App/Data 独立审查  
结论：**阻断 Phase1**

## 1. 结论摘要

本次审查发现：

- P0：0
- P1：6
- P2：8
- P3：4

阻断原因不是单测失败，而是当前产品编排存在可确定触发的静默丢数、错轮次和跨介质分叉：

1. CLI 宣称支持单张 PNG/JPEG，但单图在稳定确认前即 EOF，仍以 `completed`、退出码 0 结束。
2. `stable_page_processed_` 只按“页面是否消失”重置，同屏换卡、reroll 和瞬时 OCR 失败都不会再次 OCR。
3. reducer 把每个全新 offer 直接当成下一轮，未等待选择确认或显式 round 事件；reroll/识别修正会推进 round。
4. reducer 和 selected 状态先推进，`SessionRuntime` 后持久化；持久化失败没有内存回滚或可重试协议。
5. SQLite、JSONL、artifact 的“事务”只有进程内补偿，不具备崩溃原子性。
6. `--selected` 是 session 级常量，却被每个 accepted offer 重复写成 confirmed choice。

在 P1-01～P1-06 修复并补齐反例测试前，不建议进入 Phase1。

## 2. 范围与方法

正式审计范围：

- `src/app/**`
- `src/state/**`
- `src/storage/**`
- `src/output/**`
- 对应 public/private headers 与 tests

仅为确认调用契约，读取了 `common` 数据结构头和 capture/replay/detector 的接口签名；未审计 capture、detector、vision、knowledge、replay 的深层算法。

逐函数核查内容包括：构造/析构与 move、异常路径、锁与关闭状态、SQLite 事务、JSONL 截断补偿、artifact 创建/删除、UTF-8、路径规范化、offer 去重、round/selected 映射、main 的 Stop/Close、sleep/EOF/preview、日志与退出码。

## 3. Findings

### [P1-01] 单图 replay 和 `--once` 无法达到稳定帧门槛，却报告成功完成

位置：

- `src/app/main.cpp:556-593`
- `src/app/main.cpp:744-780`
- `src/app/augment_frame_processor.h:37-39`
- `tests/integration/augment_frame_processor_test.cpp:272-285`

不变量：一个被 CLI 明确接受的输入种类，至少应有机会进入其核心处理阶段；若配置使其必然无法处理，应拒绝配置或明确返回未处理状态。

复现：

```text
lol_augment_assistant.exe \
  --replay outputs/tmp/audit_app/continuous_visible_replay/001.png \
  --workspace outputs/tmp/audit_app/single_image_repro \
  --max-seconds 2
```

当前源码实测：

```text
raw_detector.visible=true
stable_detector.visible=false
reason=awaiting_stability:1/3
ocr_executed=false
session_end.status=completed
available_frames=1
exhausted=true
EXIT_CODE=0
```

只读查询该 session：`augment_offers=0`、`recognition_results=0`，JSONL 只有 `start,diagnostic,end`。

原因：single-file replay 只产出一个 source frame；`RunReplay` 遇到 EOF 立即结束。`--once` 也在第一个 source frame 后退出。对应集成测试明确证明默认 processor 要到第三个连续可见帧才调用 OCR。

影响：单张截图这一公开入口对识别功能实际不可用；自动化看到退出码 0 和 `completed` 会误判处理成功。

建议：为 single-image replay 提供显式单帧确认模式；`--once` 要么采用该模式，要么在解析期拒绝与多帧稳定器不兼容的组合。将“输入耗尽”与“完成一次有效处理”拆成不同状态/退出码，例如 `completed_no_stable_observation`。

### [P1-02] 页面门闩不看内容变化；同屏换卡和瞬时失败会永久跳过 OCR

位置：

- `src/app/augment_frame_processor.cpp:205-210`
- `src/app/augment_frame_processor.cpp:223-230`
- `src/app/augment_frame_processor.cpp:231-267`
- `tests/integration/augment_frame_processor_test.cpp:294-325`

不变量：去重应针对“同一 offer 内容”，不能只针对“检测器仍然可见”；失败尝试也不应无条件消费整页。

复现：在 `outputs/tmp/audit_app/continuous_visible_replay` 放入 6 张连续可见的 16:9 图：前三张为内容 A，后三张为内容 B，期间无 invisible frame。当前源码实测：

```text
frame 0: awaiting_stability:1/3
frame 1: awaiting_stability:2/3
frame 2: ocr_executed=true, reason=recognition_unknown
frame 3: ocr_executed=false, duplicate=true,
         reason=stable_page_already_processed
frames_processed=6, exhausted=true, EXIT_CODE=0
```

`stable_page_processed_` 在 OCR 前即被设为 true。无论结果是 backend unavailable、OCR failed、unknown、low confidence、reducer reject 还是 persistence error，后续所有仍可见帧都在 OCR 前返回。只有累计两个 raw invisible frame 才重置。

影响：

- 同一页面内 reroll/换卡不会被发现。
- 页面切换的短暂 invisible 间隔若被 capture 丢帧，下一轮也会被当成旧页。
- 瞬时 OCR/backend 故障没有有界重试机会。
- 代码中的 offer fingerprint 在此门闩之后，无法参与同屏内容去重。

建议：门闩键应包含稳定 card ROI 的廉价内容签名或页面版本；内容变化时重新进入稳定确认。失败结果采用有界退避/最大尝试数，而不是永久消费页面。补充“持续 visible 但内容变化”“首次 backend unavailable 后恢复”“invisible 间隔未采到”的测试。

### [P1-03] 每个全新 offer 都直接推进 round，reroll/识别修正会被当成下一轮

位置：

- `src/state/phase1_game_state_reducer.cpp:171-198`
- `src/state/phase1_game_state_reducer.cpp:201-235`
- `tests/state/phase1_game_state_reducer_test.cpp:97-120`

不变量：round 只能由明确的轮次完成事件推进；“看到一个不同的 offer”不等价于“上一轮已完成”。

推理复现：

```text
ApplyStableOffer(A)  -> offer_round = 1
// 不调用 RecordSelection
ApplyStableOffer(B)  -> offer_round = 2
```

第二次调用只要求签名全新，不要求 round 1 已选择、页面完成或收到显式 round transition。现有测试把 `round2`、`round3`、`round4` 简化为连续 unique offer，正好固化了这个错误假设。

影响：reroll、OCR 纠错、slot 顺序修正或同轮重新展示都可能提前推进 round；达到 4 后，后续真实轮次被 `OfferRoundLimitReached` 丢弃。SQLite 的 `UNIQUE(session_id, offer_round)` 会把错误轮次永久固化。

建议：把 `offer_round` 与 `offer_revision/attempt` 分离；`ApplyStableOffer` 只更新当前 round 的 offer，新增显式 `ConfirmSelection/AdvanceRound` 事件后才推进。测试必须覆盖同轮 A→B reroll、未选择、选择后下一轮以及 round 4 上限。

### [P1-04] reducer/selected 先提交，持久化失败后没有内存回滚，无法安全重试

位置：

- `src/app/augment_frame_processor.cpp:259-282`
- `src/state/phase1_game_state_reducer.cpp:186-198`
- `src/state/phase1_game_state_reducer.cpp:223-235`

不变量：内存状态、SQLite、JSONL 和 artifact 对一次 accepted offer 应表现为同一个逻辑提交；持久化失败后必须保持原状态或提供确定的重试状态。

推理复现：

1. `ApplyStableOffer` 先写入 seen signature、推进 round、替换 current offer。
2. 配置 `--selected` 时，`RecordSelection` 再写入 confirmed selection。
3. 随后 `AcceptOffer` 可能因 DB busy/commit、JSONL、artifact、序列化或 Closed 返回失败。
4. processor 直接返回，没有恢复 reducer，也没有撤销 `stable_page_processed_`。
5. 即使页面之后 re-arm，同一 offer 会被 reducer 的全局 seen signature 判定为 duplicate；不同 offer 又会从已错误推进的 round 继续。

影响：数据库没有该 offer，但 stdout 后续状态和 reducer revision/round 已包含它；一次瞬时存储失败会永久改变本 session 的业务状态。

建议：reducer 提供“prepare candidate snapshot（不变更）→持久化→commit reducer”的两阶段 API，或提供不可失败的 checkpoint/restore。selected 也必须和 offer 在同一逻辑提交中完成。增加 DB lock、JSONL write/flush、artifact write 三类 fault injection 后的重试测试。

### [P1-05] SQLite、JSONL、artifact 不是崩溃原子事务，存在确定的跨介质分叉窗口

位置：

- `src/app/session_runtime.cpp:488-503`
- `src/app/session_runtime.cpp:701-713`
- `src/app/session_runtime.cpp:766-818`
- `src/app/session_runtime.cpp:882-915`
- `src/storage/session_store.cpp:690-719`

不变量：`start`、`accepted_offer`、`end` 在 SQLite/JSONL/artifact 之间不能出现互相矛盾的提交事实。

推理：

- Create：JSONL 的 `start`/`diagnostic` 在 SQLite `COMMIT` 前写入并 flush。
- Accept：5 个 artifact 先落盘；随后 SQLite transaction callback 写入并 flush JSONL；callback 返回后 `RunInTransaction` 才执行 SQLite `COMMIT`。
- Close：JSONL `end` 先写入并 flush，SQLite 再 commit。

进程若在任一“外部文件已持久化、SQLite 尚未 commit”的窗口崩溃，会分别留下：

- 有 JSONL start、无 session row；
- 有 artifact/accepted JSONL、无 offer/recognition rows；
- JSONL 已 closed、SQLite `ended_at_utc` 仍为空。

当前 `resize_file` 和删除 artifact 只覆盖函数正常返回时的补偿，不覆盖掉电、进程终止和 crash。

影响：重放、审计和训练数据无法判断哪个介质是事实源；“测试全绿”无法证明 crash consistency。

建议：明确 SQLite 为唯一提交事实源；在 SQLite 中写 outbox/commit marker，artifact 先写 `.tmp`，commit 后原子 rename；JSONL 由已提交 outbox 派生。启动时执行 reconciliation：补齐已提交输出、删除未提交临时文件、校验 lifecycle sequence。

### [P1-06] `--selected` 是 session 级常量，却给每个 accepted offer 重复写 confirmed choice

位置：

- `src/app/main.cpp:298-312`
- `src/app/main.cpp:425-430`
- `src/app/augment_frame_processor.h:35-37`
- `src/app/augment_frame_processor.cpp:269-282`
- `tests/integration/augment_frame_processor_test.cpp:269-270`
- `tests/integration/augment_frame_processor_test.cpp:217-225`

不变量：confirmed choice 必须对应一次具体 offer/round 的实际选择事件，不能从 session 启动参数推导所有轮次。

推理复现：processor 构造时保存一个 `selected_slot_`；每次 offer 成功识别都从当前 cards 的同一位置取 ID，并立即 `RecordSelection`。该值从不消费、不更新，也不等待真实选择发生。现有集成测试设置 center，接受两个 offer，并明确期望数据库有两条 choice；它只检查数量，没有检查这种 session 级复用是否符合产品语义。

影响：多轮运行会静默伪造“每轮都选了同一位置”；reroll 被误当 round 时还会给未选择的 offer 写 confirmed choice。

建议：把 selection 变成带 round/offer fingerprint 的一次性事件；没有 selection 事件就写 unconfirmed/null 或不写 choice。若 `--selected` 仅用于单样本测试，则解析期限制其只能与单 offer 模式组合，并在 help/JSON 中明确语义。

### [P2-01] JSONL 恢复失败会留下“IsOpen=true、writer=null”的中毒状态，Close 可空指针解引用

位置：

- `src/app/session_runtime.cpp:346-362`
- `src/app/session_runtime.cpp:800-811`
- `src/app/session_runtime.cpp:857-915`
- `src/app/session_runtime.cpp:925-927`

推理复现：`RestoreJsonl` 先 `jsonl_writer.reset()`，再 `resize_file`。若 resize 因共享冲突、I/O 错误或空间/文件系统故障失败，函数返回时 writer 保持 null、`closed` 仍为 false。

随后：

- `IsOpen()` 仍返回 true；
- `AcceptOffer()` 因 writer null 返回 `Closed`；
- `Close()` 没有 writer null 检查，在 transaction callback 中执行 `impl_->jsonl_writer->Write`，可空指针解引用。

建议：引入 `Open/Failed/Closed` 显式状态；恢复失败立即进入 Failed，所有 API 返回稳定错误。`Close` 必须容忍部分初始化/部分关闭对象。恢复过程不要在可恢复替代对象准备好前丢失唯一 writer 所有权。

### [P2-02] artifact 的成功与回滚都没有验证最终文件集合

位置：

- `src/output/debug_artifact_writer.cpp:156-193`
- `src/output/debug_artifact_writer.cpp:307-321`
- `src/app/session_runtime.cpp:295-303`
- `src/app/session_runtime.cpp:800-810`

问题一：`WriteSidecar` 只检查 `write()` 后的 stream 状态，没有显式 `flush()`/`close()` 并检查结果。延迟到析构时暴露的磁盘错误会被吞掉，函数仍返回完整 artifact set。

问题二：transaction rollback 的 `RemoveArtifacts` 对 5 次 remove 全部忽略 error。Windows sharing mode、AV 扫描或权限变化导致删除失败时，DB/JSONL 已回滚，但孤儿文件仍保留，且返回结果清空了 artifact paths，调用方无法补偿。

建议：sidecar 显式 flush/close 并检查；artifact writer 采用临时名和原子 rename；回滚返回每个路径的清理状态，失败时写 recovery marker，并由启动 reconciliation 清理。增加磁盘写失败与 delete-sharing-denied fault injection。

### [P2-03] async worker 没有异常边界，后台异常会直接 `std::terminate`

位置：

- `src/storage/async_storage_writer.cpp:47-73`
- `src/storage/async_storage_writer.cpp:81-105`
- `src/storage/async_storage_writer.cpp:146-149`

`Run()` 直接调用 `Process()`，线程入口也没有 catch。虽然多数存储 API 返回 `StorageStatus`，但字符串分配、filesystem/path UTF-8 转换、JSON 序列化和第三方库仍可抛异常。异常逃出 `std::thread` 入口会终止整个进程，而不是增加 `failed`、记录 `LastError` 或让 `Shutdown` 返回错误。

建议：每条 command 外层捕获 `std::exception`/unknown，转换为失败统计；线程最外层再设 fatal worker status、停止 accepting 并唤醒 Shutdown。加入一个 throwing/fault-injected command path 测试，验证进程不终止且会 drain/关闭。

### [P2-04] reducer 的状态更新顺序不满足异常安全；一次分配异常可留下无效 snapshot

位置：

- `src/state/phase1_game_state_reducer.cpp:194-198`

`ApplyStableOffer` 依次：插入 seen signature、推进 `offer_round`、复制 `current_offer`、增加 revision。复制 observation 内的 string/optional 时可抛异常。首轮若在 `current_offer = observation` 抛出，可能留下 `offer_round=1` 但无 current offer；signature 已标记 seen，revision 未增加，`Snapshot().IsValid()` 为 false，并且同 offer 无法重试。

建议：先在临时 snapshot 和临时 set node 上完成所有可抛操作，最后以不抛的 swap/commit 一次替换；添加分配失败或 throwing allocator 测试。

### [P2-05] `Phase1SessionRuntime` move assignment 静默丢弃 Close 失败

位置：

- `src/app/session_runtime.h:67-69`
- `src/app/session_runtime.cpp:370-382`

move assignment 先 `(void)Close()`，无论 Close 是否因 transaction、JSONL flush 或 SQLite close 失败，都立即用新 `impl_` 覆盖旧对象。该 API 又标记为 `noexcept`，调用方没有任何渠道知道旧 session 未正确结束。

建议：删除 move assignment，或只允许目标对象已关闭/为空时移动；需要替换 session 时提供显式 fallible `CloseAndReplace`，Close 失败必须保留旧 impl 供重试/诊断。

### [P2-06] replay preview 在 EOF 立即关闭，单图/快速目录下几乎不可见

位置：

- `src/app/main.cpp:487-505`
- `src/app/main.cpp:507-545`
- `src/app/main.cpp:568-593`

`ProcessFrame` 每帧只 pump 一次；replay EOF 后立即 `ClosePipeline`，其中第一步就是关闭 preview。单图 `--preview --max-seconds 30` 实测总进程仅 **455 ms**，没有 EOF 后的消息循环或观察窗口。独立 output smoke test 持续 2 秒，但未覆盖 main 的 replay 生命周期。

建议：replay EOF 后若 preview 已请求，进入有界/用户可关闭的 preview loop，或增加明确的 `--preview-hold-ms`；目录 replay 可按显示帧率节流 preview，而核心无 preview 路径继续无 sleep。

### [P2-07] main 的 JSON string emitter 不验证/修复 UTF-8，错误路径可输出非法 JSON

位置：

- `src/app/main.cpp:53-79`
- `src/app/main.cpp:81-123`
- `src/app/main.cpp:208-230`
- `src/app/main.cpp:594-601`
- `src/app/main.cpp:724-737`
- `src/app/main.cpp:781-783`

`AppendJsonString(std::string_view)` 只转义 JSON 控制字符，其他字节原样写入。`ErrorJson` 接受 `std::exception::what()`、capture backend error 等任意窄字符串；这些值不保证是 UTF-8。与 storage/structured writer 的严格或 sanitize 行为不一致。

建议：main 复用统一的 UTF-8 JSON helper；产品输出采用“严格拒绝”或 U+FFFD sanitize 的单一策略，并对 malformed backend error 加测试。

### [P2-08] alternating reason 可让 frame_result 按 20 Hz 持续洪泛

位置：

- `src/app/main.cpp:442-474`
- `src/app/cli.cpp:15`

日志门控条件是 `reason_changed || ocr_executed || accepted`。检测状态若在两种 reason 间抖动，每帧都满足 reason_changed；active rate 为 20 Hz，允许 `--max-seconds=86400`，理论上单 session 可输出约 1,728,000 条完整 frame JSON。没有速率限制、周期汇总或 dropped-log counter。

建议：对非 accepted 诊断采用 token bucket/最小间隔，并周期输出聚合计数；accepted/error/lifecycle 继续立即输出。增加 20 Hz alternating reason 的容量测试。

### [P3-01] `*_json` 字段只校验 UTF-8，不校验 JSON 语法

位置：

- `src/storage/session_store.cpp:452-477`
- `src/storage/session_store.cpp:503-537`
- `src/storage/session_store.cpp:577-634`
- `src/storage/session_store.cpp:188-277`

`metadata_json`、`offer_json`、`raw_json` 只经过 `IsNonEmptyUtf8`；例如 `"{"` 是合法 UTF-8，会被存入数据库。schema 也没有 `json_valid` 约束。`AsyncStorageWriter` 暴露这些 record 类型，因此并非只会收到内部 serializer 的输出。

建议：在 API 边界解析/验证 JSON；若部署的 SQLite JSON1 能力可保证，则 schema 同时增加 `CHECK(json_valid(...))`，否则在 C++ 层验证并补测试。

### [P3-02] `IsOpen`/状态查询与 Close 并发时存在数据竞争

位置：

- `src/app/session_runtime.cpp:857-927`
- `src/storage/session_store.cpp:722-740`
- `src/storage/jsonl_writer.cpp:228-254`

Close 在 mutex 下写 `closed`、database pointer 或 stream 状态；对应 `IsOpen()` 不加锁也不使用 atomic。若这些公开查询与 Close 并发，属于 C++ data race/未定义行为。类内部使用 mutex 容易让调用方误以为并发关闭是受支持的。

建议：查询使用同一 mutex/atomic lifecycle state，或在 public contract 明确全部 lifecycle API 只能由单一 control thread 调用并在 debug build 断言线程归属。

### [P3-03] 高优先级入队删除“第一个较低优先级”，不是队列中的最低优先级

位置：

- `src/storage/async_storage_writer.cpp:173-188`

当队列顺序为 `[Normal, Low]`，新 Critical command 会删除 Normal，因为 `find_if` 命中第一个 `< Critical`，即使后面还有 Low。该行为弱化 priority 语义；若 command 有 session/offer 依赖，错误淘汰还可能导致后续 FK 失败。

建议：选择最低 priority 的候选，并在同级内定义 oldest/newest 策略；对具有关联依赖的 command 使用不可拆分 batch 或禁止按单条记录淘汰。

### [P3-04] 路径边界检查与实际 open/write 之间存在 reparse-point TOCTOU

位置：

- `src/storage/storage_internal.h:71-119`
- `src/storage/session_store.cpp:414-428`
- `src/storage/jsonl_writer.cpp:170-188`
- `src/output/debug_artifact_writer.cpp:260-305`

代码先 canonical/relative 验证，再通过路径名 open/write。攻击者或并发进程若能在检查与使用之间把父目录替换成 junction/symlink/reparse point，最终 I/O 目标可能不再位于已验证 root。随机 session ID 降低了 runtime 场景可利用性，但通用 storage/output API 仍有窗口。

建议：在 Windows 上通过 handle 打开父目录/文件，拒绝不允许的 reparse point，并用 `GetFinalPathNameByHandle` 对实际 handle 做 root 验证；至少在写入前后复验并记录异常。

## 4. 已运行验证

隔离构建目录：`outputs/tmp/audit_app/build_msvc`。

构建：

```text
cmake -S . -B outputs/tmp/audit_app/build_msvc \
  -G "Visual Studio 17 2022" -A x64
cmake --build outputs/tmp/audit_app/build_msvc --config Release --target \
  lol_augment_assistant state_worker_tests storage_worker_tests output_tests \
  cli_test session_runtime_test augment_frame_processor_test
```

结果：当前源码 MSVC Release 构建成功。

定向测试：

```text
ctest --test-dir outputs/tmp/audit_app/build_msvc -C Release \
  -R "^(state_worker_tests|storage_worker_tests|output_tests|cli_test|session_runtime_test|augment_frame_processor_test|json_loads_validation)$" \
  --output-on-failure
```

结果：**7/7 passed，0 failed，5.20 sec**。

反例诊断：

- 单图 EOF：1 个 frame，`awaiting_stability:1/3`，OCR 0 次，DB offer 0 行，但 `completed`/exit 0。
- 连续 visible 内容切换：6 个 frame，只在第 3 帧 OCR；第 4 帧内容已切换仍返回 `stable_page_already_processed`，后两帧也不 OCR。
- replay preview EOF：请求 max 30 秒，进程 455 ms 即关闭 preview 并退出。

这些结果与 7/7 测试通过不矛盾：现有测试没有覆盖 main 单图入口、持续 visible 内容变化、持久化失败后的 reducer 状态、跨文件 crash window 或 main 的 preview EOF。

## 5. 已核对且本轮未发现缺陷的项目

- Session Create 对 absolute path、`..` component 和严格 descendant 做了检查；随机 session ID 是安全 ASCII。
- storage UTF-8 validator 拒绝非法起始字节、截断序列、overlong、surrogate 和超出 U+10FFFF 的 code point。
- JSONL 对 metadata、field key/string 和非有限 double 做校验，控制字符转义正确，无 BOM，正常 Close/Flush 可观测。
- Structured JSON 对控制字符转义并 sanitize malformed UTF-8；finite float 检查存在。
- SessionRuntime 正常返回路径下，same-round fingerprint duplicate 不写新 row/JSONL/artifact；不同 offer 同 round 被拒绝。
- recognition 按 LEFT/CENTER/RIGHT semantic slot 重排，检查 slot 唯一、ID 映射一致和三 ID distinct。
- SQLite schema 启用 foreign keys、WAL、round uniqueness、choice-to-offer trigger；正常 transaction failure 会 rollback SQLite。
- 正常 AcceptOffer transaction 失败路径会尝试删除 artifact、截断并 reopen JSONL、撤销 in-memory runtime fingerprint。
- main 正常 live 路径在退出前调用 `source.Stop()`；异常路径在 source 已启动时也先 Stop，再 Close session。
- ClosePipeline 会关闭 preview，并显式调用 SessionRuntime Close；正常 Close 幂等，Close 失败映射为非零退出码。
- live 有 frame 时按 recommended Hz sleep，deadline 对 sleep 做上界；无 frame 时 2 ms sleep 并 pump preview，不存在显式 busy spin。
- replay 正常路径无无意义 sleep，EOF 判定与 FrameCount/CurrentIndex 对齐。

## 6. 测试覆盖缺口

建议至少新增：

1. 可见单图 replay 进入 OCR 的 end-to-end test，并校验退出状态。
2. `--once` 与稳定门槛的组合测试。
3. 持续 visible 时 A→B 内容变化，B 必须被处理。
4. backend unavailable/unknown 后恢复的有界重试。
5. 同轮 reroll 不推进 round；选择确认后才推进。
6. round 1 未选择、round 2 选择时 selected 映射不压缩、不伪造。
7. DB busy/commit failure、JSONL write/flush/resize failure、artifact write/delete failure的 fault injection。
8. crash/restart reconciliation：分别在 artifact write、JSONL flush、SQLite commit 前后终止。
9. main replay preview 在 EOF 后的生命周期测试。
10. alternating reason 的日志容量测试与退出码矩阵。
11. malformed UTF-8 backend error 的 stdout/stderr JSON parse test。
12. async worker exception、并发 Shutdown/IsOpen 和 priority eviction 顺序测试。

## 7. 残余风险与边界

- 本轮不审计 capture/detector/vision/knowledge/replay 深层算法，因此不对其识别准确率、队列实现和图像解码安全性背书。
- 未做真实断电/进程 crash、磁盘满、杀毒软件 delete-sharing-denied 和 reparse race；对应风险来自明确提交顺序和错误忽略路径。
- 未运行真实游戏窗口的 live capture；只核对 main 对 capture 接口的 Start/State/Stop 调用契约。
- 当前没有持久化 session reopen 功能；若未来增加，内存 fingerprint、accepted rounds 和 `next_offer_id` 都必须从 SQLite 恢复，不能沿用当前初始值。
- artifact 没有 sha256，数据库记录也不验证文件存在；若 artifact 将作为审计证据，仍需完整性与启动校验策略。

## 8. Phase1 Gate

**结论：BLOCK。**

解除阻断的最低条件：

1. 修复 P1-01～P1-06。
2. 增加单图 EOF、同屏内容切换、reroll/round、selected per-offer、持久化失败重试五类回归测试。
3. 明确 SQLite/JSONL/artifact 的事实源与 crash recovery 协议，并用至少三个 commit-window fault injection 验证。
4. 定向测试继续全绿，同时反例诊断不再复现。

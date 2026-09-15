# Phase 3 动态验收交接

## 当前状态

本轮按用户要求收束在静态完成边界。实时识别到推荐引擎的实现、常驻 IPC、四轮状态机、人工选择确认、JSONL 记录、异常 fail-closed、测试和延迟基准均已完成；真实 LoL 一局四轮动态验收由用户明确延期，禁止把静态 GameState 契约测试或旧截图 Replay 写成动态验收通过。

当前锁定数据：

- snapshot：`16.16-20260823-255fffa635da`
- patch：`16.16`
- data date：`2026-08-23`
- `SINGLE_PRIOR_GAMES=5000`
- `COMBO_PRIOR_GAMES=2000`
- 原有 `Scrape/src/recommendation_engine.py` 未修改
- recommendation engine SHA-256：`292937AEB4C6491B927BDE7E965355EEDCA187518181BC2670359E4DBA68591A`

## 已确认结论

1. `Scrape/src/recommendation_worker.py` 只加载一次现有 `RecommendationEngine` 和视觉目录，通过 UTF-8 JSONL stdin/stdout 长驻服务请求。
2. `scripts/phase3/realtime_recommendation.py` 启动现有 `lol_augment_assistant.exe`，只消费其 accepted `GameState`；没有截图替代、没有进程内存、没有输入注入。
3. offer 以 `stage + 三个 technical id` 的 SHA-256 去重，每个新 offer 最多推荐一次。
4. 玩家通过 `Ctrl+Alt+1/2/3` 确认左/中/右最终选择；实现只轮询 `GetAsyncKeyState`，不注册/消费热键，不发送输入，不调用前台激活 API。
5. stage 必须等于 `len(owned_augments)+1`；确认后 technical id 加入 owned，下一轮传给现有推荐引擎用于 combo。
6. UNKNOWN、视觉 ID 不在 snapshot、stage invalid、重复/重叠、上一轮未确认、worker/engine/protocol 异常全部 fail closed，`recommended=null` 或根本不调用 worker。
7. Scrape unittest 32/32 通过；根 CTest 23/23 通过；Phase 3 契约 9/9 通过，并通过 `ResourceWarning` 作为错误的检查。
8. 1000 次常驻请求：IPC 平均 0.632 ms、P95 1.032 ms；engine 平均 0.116 ms、P95 0.193 ms；冷启动 728.437 ms，仅发生一次。
9. 生产 worker 四轮状态契约已通过，但输入是明确标记的契约对象，不是真实 LoL 画面。
10. 已有真实 WGC 预览派生帧（尤里卡 / 不祥契约 / 超负荷）在裁成 1280×720 后 detector=visible、confidence=0.983684，但现有 OCR 为 UNKNOWN；闭环正确地没有给推荐。这是正确失败路径，不是视觉成功样本。

## 下一步唯一目标

完成一局真实 LoL 海克斯大乱斗四轮动态验收，不扩展 LLM、联网、装备、Overlay、TTS 或新推荐算法。

### 启动条件

- 游戏使用现有视觉链路已支持的 16:9 分辨率，优先 1920×1080 或 2560×1440。
- 从第一轮三选一前启动；如果中途启动，当前状态机会因为 stage 顺序不完整而 fail closed。
- 用户提供本局英雄，继续手动传入，不做英雄识别。

### 运行

```powershell
Set-Location -LiteralPath 'F:\Realworld\lol'
.\scripts\run_recommendation.ps1 -Hero '实际英雄名' -VisionDebug
```

若窗口标题匹配有问题，先用现有程序取得 HWND，再执行：

```powershell
.\scripts\run_recommendation.ps1 -Hero '实际英雄名' -Hwnd 123456 -VisionDebug
```

程序不会抢占 LoL 前台。游戏内选完卡后，立即按一次：左 `Ctrl+Alt+1`、中 `Ctrl+Alt+2`、右 `Ctrl+Alt+3`。

### 每轮验证点

1. 三选一画面出现后，视觉程序输出包含三张 `RECOGNIZED` technical ID 的 GameState。
2. 只出现一条 `status=recommended` 的推荐事件。
3. `stage` 依次为 1/2/3/4。
4. `choices` 三项与画面人工核对一致。
5. `ranking` 有三项，debug 保留 single / combo / inferred / sample size / confidence / stage validity。
6. 推荐后玩家在游戏中自行选择，再按确认组合键。
7. `choice_confirmation.status=choice_confirmed`，下一轮 `owned` 比上一轮多一项。
8. 第四轮确认后出现 `recommendation_session_complete`，`stages_completed=4`。
9. 每轮 `bridge_latency_ms < 1000`；另记录从画面稳定到推荐事件的端到端时间。
10. 任意 UNKNOWN 或冲突必须没有非空 recommended，并保留对应 Debug Artifact。

### 证据与输出

- 推荐会话：`outputs/runtime/recommendation_sessions/*.jsonl`
- 视觉会话：`outputs/runtime/phase1-*/events.jsonl` 与对应 SQLite/debug artifacts
- 静态基准：`outputs/phase3_realtime/static_verification.json`
- 静态 Exp：`result/Exp/PHASE3_REALTIME_RECOMMENDATION.md`

动态验收后，把四轮逐轮画面人工标签、识别 IDs、推荐 JSON、确认事件、owned 变化和延迟汇总到新的 `outputs/phase3_realtime/live_four_round_verification.json`。只有四轮均来自同一真实会话时，才能报告“真实闭环通过”。

## `next/files` 内容

`stage4/next/files` 提供继续动态验收所需的规范化快照：

- 新增 worker、桥接器、PowerShell 入口和两组测试；
- 未修改的推荐引擎及当前 Scrape snapshot 数据；
- 视觉海克斯目录、图标模板 manifest/templates；
- Release 视觉程序；
- 静态验证 JSON 与 Exp；
- 本轮使用的旧真实 WGC 样本及人工 annotation。

标准完整副本位于 `stage4/outputs` 和 `stage4/scripts`。首次复制因 PowerShell `New-Item -LiteralPath` 参数不兼容，在 `stage4` 根部留下了一份不完整、无害的 outputs 重复副本；清理被环境递归删除安全策略阻止。继续工作时忽略根部重复项，只使用上述两个标准目录与 `next/files`。

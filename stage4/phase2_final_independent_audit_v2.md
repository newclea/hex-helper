# Phase 2 修复后最终独立签收审计 v2

审计日期：2026-08-26（Asia/Shanghai）  
审计对象：当前 final build、`result`、`outputs/tmp/phase2_final_replay_benchmark2` 与 top-level final reports  
审计方式：fresh-context、只读取证；未启动游戏、未启动 preview，未修改代码、数据、result 或 stage。仅本报告为本审计新增文件。

## 最终签收

**PASS**

- **P0：0**
- **P1：0**
- 上次唯一 P1（OCR stage 为 `UNAVAILABLE`）已关闭：当前 producer 从同一 replay session 的 SQLite `recognition_results.raw_json` 导出逐槽 OCR/icon 证据，最终 benchmark 为 OCR **3/3**、icon **0/3（三槽均为 `UNKNOWN`）**、final augment ID **3/3**。
- 本 PASS 只适用于本报告列出的精确 artifact hash 与真实性边界。**有效正例仅 n=1 个 original-WGC offer；绝不可把 3/3 或 100% 泛化为总体准确率、稳定性或跨环境能力。**

## Gate 明细

| Gate | 结论 | 独立证据 |
|---|---|---|
| current final build 与 `result` 同 hash | PASS | `outputs/tmp/build_phase2_final_verify/bin/lol_augment_assistant.exe` 与 `result/bin/lol_augment_assistant.exe` 均为 868,864 bytes，SHA-256 均为 `B0726F47230E1F98EE4124091FEC0DE66E7BFA9D9BFFFC71F6FFA1FDA1A26075`。 |
| `result` hash manifest | PASS | 独立解析并逐项重算 `result/SHA256SUMS.txt`：181 entries 对应 181 个非 manifest 文件；malformed=0、missing=0、mismatch=0、unsafe/escape=0、duplicate=0、unlisted=0。EXE、两份 README 与两支修复后 Python 脚本条目均和实物一致。 |
| CTest 21/21 | PASS | 当前 final build 的 `LastTest.log` 是 2026-08-26 01:22–01:23 完整 run：21 个 `x/21 Testing:`、21 个 `Test Passed.`、0 个失败标记；首项 `common_contracts_test`，末项 `phase2_benchmark_contract`。 |
| Producer/Benchmark tests 18/18 | PASS | 本审计以 `python -B -m unittest tests.phase2_benchmark.test_benchmark_phase2 tests.phase2_replay_benchmark.test_dataset_replay_benchmark -v` 独立重跑；`Ran 18 tests in 9.113s`、exit 0、`OK`。测试使用系统临时目录，`-B` 禁止 bytecode 落盘。覆盖 SQLite staged evidence、icon `UNKNOWN`、malformed `raw_json`、DB path escape、DB/stdout 不一致、annotation 不回填、preview 排除及 normalized exact。 |
| producer 绑定 EXE hash | PASS | `producer_report.json` 同时记录 executable 绝对路径及 `executable_sha256=B0726F...A26075`；该值与当前 final build/result 实物独立重算值一致。 |
| OCR 来源为同会话 SQLite raw_json | PASS（证据边界见下） | n5 报告 `prediction_source=session_start.database:recognition_results.raw_json`、session=`phase1-e0353ee6505a9ef230ff5c0b670bcb19`、row count=3、DB SHA-256=`81366C72F330D70CCFB7AA4F228880B6FD39D25D4D89DAA9D9D86E89BCCDC8A4`。Producer 源码以 SQLite `mode=ro` 打开 `session_start.database`，要求 DB 位于该样本临时 workspace，按同一 `session_id` 查询 `recognition_results`，拒绝其他 session、行数/slot、DB columns/raw_json、DB/stdout final 的不一致；OCR 只取 `raw_json.raw_text`。`_produce_prediction` 不接收 annotation；annotation 只在随后生成 benchmark truth stream 时读取。 |
| 非 annotation/final 回填 | PASS | n5 stdout legacy protocol 的 OCR 仍是三槽 `UNAVAILABLE`，但导出 cards 的 OCR 是三条 SQLite raw text；代码没有从 final/display name/augment ID 合成 OCR。独立通过的 `test_unknown_is_not_filled_from_annotation` 与 malformed/inconsistency tests 进一步封闭回填路径。 |
| Normalize 规则、非 fuzzy | PASS | Benchmark 仅执行 NFKC → casefold → 删除 Unicode whitespace 与 Unicode `P*` punctuation，再做相等比较；`fuzzy_matching=false`。`不 动 如 山`→`不动如山`、`我 们 的 治 疗`→`我们的治疗`、`星 界 躯 体`→`星界躯体`，三槽均 normalized exact。 |
| OCR / icon / final | PASS | 唯一 benchmark-eligible n5：OCR numerator/denominator=`3/3`；icon=`0/3`，LEFT/CENTER/RIGHT 均 `UNKNOWN`、`icon_id=null`，没有伪造 icon 命中；final augment ID=`3/3`；three-card all-correct=`1/1`；false match=`0/3`。 |
| preview 排除 | PASS | Dataset validation：9 个 real bundle，其中 original-WGC=6、preview-derived=3；3 个 preview 样本均 `derived_from_preview=true`、`capture_boundary=preview_derived`、`benchmark_use=real_scenario_calibration`、`benchmark_eligible=false`。5 个受遮挡 original-WGC 为 skipped；benchmark input 三个 JSONL 各仅 1 行，只含 clear n5，scope 明确 `preview_derived_included=false`、`synthetic_included=false`。Producer command 未含 `--preview`，报告 `headless=true`。 |
| 安全 API | PASS（限定表述） | `src/include/scripts` 扫描未发现 `SetForegroundWindow`、`SetFocus`、`AttachThreadInput`、`SendInput`、`keybd_event`、`mouse_event`、`OpenProcess`、`Read/WriteProcessMemory`、`CreateRemoteThread`、`VirtualAllocEx`、`SetWindowsHookEx` 等产品调用；final PE imports 同样无这些符号。PE 存在 `GetAsyncKeyState`，源码只读 F8 collection hotkey；存在 `ShowWindow`，preview 实现固定使用 `WS_EX_NOACTIVATE` 与 `SW_SHOWNOACTIVATE`。 |
| launcher 拒绝 | PASS | Selector 只接受唯一、可见、大小写精确的 `League of Legends (TM) Client`；标题 `League of Legends` 与 launcher HWND 均显式拒绝，substring、wrong case、ambiguous、消失 HWND 也拒绝。当前 final build 的 `cli_test.exe` 本审计再次直接运行，exit 0、`cli_test passed`；同一测试也在 21/21 CTest 中通过。 |
| top-level final reports | PASS | `outputs/phase2_final_replay_producer.json` 与工作目录 producer report 同 hash；top-level benchmark JSON/Markdown 与工作目录对应文件分别同 hash。当前 `phase2_final_report.md` 的 OCR/icon/final、n=1、preview 与安全边界与机器报告一致。 |

## SQLite 证据边界

Producer 的逐槽导出、同 session 校验、DB hash、源码数据流和 18/18 contract tests 足以确认本轮 machine-readable OCR 的生产路径是 SQLite `recognition_results.raw_json`，不是 annotation 或 final 回填。该 producer 使用 `TemporaryDirectory`，运行结束后原始 session SQLite 已清理；因此本审计不能再次按 DB hash 打开历史数据库逐字节复查。此项不阻断当前签收，但不可把现有证据扩张为“原始 SQLite 已归档并可离线重放验证”。

## 可声明

- 当前 final build、producer 执行所记录的 EXE 与 `result` EXE 是同一 SHA-256：`B0726F47230E1F98EE4124091FEC0DE66E7BFA9D9BFFFC71F6FFA1FDA1A26075`。
- 当前 final build 的完整 CTest 为 21/21；修复后的 Producer/Benchmark contract tests 为 18/18。
- 在唯一纳入指标的 original-WGC n5 offer 上，产品 staged OCR 为 3/3 normalized exact，icon matcher 为 0/3 且三槽均 fail-closed `UNKNOWN`，最终 augment ID 为 3/3。
- OCR machine evidence 的实现路径来自同 replay session 的 SQLite `recognition_results.raw_json`；producer 不从 annotation、display name、final augment ID 或 truth 回填 OCR。
- Preview-derived 与 synthetic 未进入 original-WGC benchmark 分母；本轮 producer 是 headless，未请求 preview。
- 在所列 API 范围内，产品无焦点操纵、输入注入或跨进程内存读写；launcher title/HWND 被拒绝。
- 当前 `result/SHA256SUMS.txt` 对 package 内容逐项闭合，无缺失、错 hash 或未列文件。

## 不可声明

- **不可从 n=1 推广任何总体识别准确率、稳定准确率、性能分布或生产可用率。** 三张卡来自同一 offer，高度相关，不是 3 个独立样本。
- 不可声明跨分辨率、UI scale、DPI、HDR/SDR、不同 patch、hover/highlight 或长期 WGC 稳定性已验证；当前有效正例只有 2560×1600、UI scale unknown。
- 不可声明 icon component 已识别成功；其真实结果是 0/3，三槽均 `UNKNOWN`。
- 不可声明 OCR raw text 与 annotation 字面完全相同；本轮通过的是已声明规则下的 normalized exact，未使用 fuzzy。
- 不可声明“零 input API”；存在只读 F8 的 `GetAsyncKeyState`。也不可声明产品没有 preview 能力；只能声明本轮未启动 preview，且 preview 窗口不激活。
- 不可声明原始临时 SQLite 已归档可复查；当前保留的是 producer 导出、DB hash 与数据流验证。

## 精确哈希（SHA-256）

| Artifact | SHA-256 |
|---|---|
| final build EXE | `B0726F47230E1F98EE4124091FEC0DE66E7BFA9D9BFFFC71F6FFA1FDA1A26075` |
| `result` EXE | `B0726F47230E1F98EE4124091FEC0DE66E7BFA9D9BFFFC71F6FFA1FDA1A26075` |
| `result/SHA256SUMS.txt` | `EA3450176FBAB703CCFEA0270C8776D7E0661D1A71549414B8397B0E3F5D7A4D` |
| final CTest `LastTest.log` | `21A56EF064FCBC56A44284986C6D6E59E85C14D717B035D6395E6D532E85F6B5` |
| replay producer report（工作目录与 top-level） | `617A7CFB7E3465171E44CBD97BB02B79645242A3A6971619985D188A5C6A4896` |
| replay producer results JSONL | `A04FFBDC83AF71CD3E9A6BA704AE03CAF532E65C73CC8B96C72035C96F4AA378` |
| final benchmark JSON（工作目录与 top-level） | `211C097FA47F041D83B709AC80B1C4C0D92D1B0CEA2D546E82AB69AEB202E133` |
| final benchmark Markdown（工作目录与 top-level） | `2EC87603A71EDD14FE65F03D63E9E231FCF7B0B7963A33A60145DF7F6E18CFFB` |
| final dataset validation JSON | `C710D36233BF0D527DDE409D73C69755471D4AA818510A296DBA9733422BC51B` |
| OCR stage export fix report | `1D6391786B6DA339E9DED9E17AEFB32E65AD98CCF68F20A3DAFE04F2F1389A21` |
| top-level final report | `EFDB202314AC95021F75B556A558665C3FBC59F77D2D3C53ACFB46533DF2AD23` |
| producer source `run_dataset_replay.py` | `9A339018893B48BA132085C42EB77DECAB313F28A4888A45A237539B92428A2B` |
| benchmark source `benchmark_phase2.py` | `AD3F8AF16A15D60D02D92A9F753CE30D379D5999B4BE23087E6B3E67554FFD32` |
| n5 transient session SQLite（producer 记录值） | `81366C72F330D70CCFB7AA4F228880B6FD39D25D4D89DAA9D9D86E89BCCDC8A4` |

## 签收决定

**最终签收 PASS；P0=0，P1=0。** 上次 OCR-stage P1 已由同会话 SQLite raw_json 导出链关闭，且没有发现新的阻断项。该决定严格限定于上述 hash 快照和 **n=1 original-WGC offer**；不得作任何泛化准确率或稳定性宣传。

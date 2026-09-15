# Phase 2 最终独立签收审计

审计日期：2026-08-26（Asia/Shanghai）  
审计方式：fresh-context、只读取证；未启动游戏、未启用 preview，未修改代码、数据、`result` 或 `stage1`。

## 最终 Gate

**FAIL**

- **P0：0**
- **P1：1** — n5 的最终融合 augment ID 为 3/3，但机器可读 benchmark/producer 证据把三个 OCR component 全部记录为 `UNAVAILABLE`、`ocr_text=null`，并计算 `ocr_accuracy=0/3`。因此不能签收要求中的“**OCR component 3/3**”。
- 其余受审 gate（同一 EXE hash、CTEST 21/21、真实 n5 最终 ID 3/3、benchmark 分母和真实性边界、icon accepted 0/3、启动器拒绝、包哈希完整性、源码绝对 icon 路径禁入）通过或在下述限定范围内通过。

## Gate 明细

| Gate | 结论 | 独立证据 |
|---|---|---|
| final build 与 `result` 同一 EXE | PASS | `outputs/tmp/build_phase2_final_verify/bin/lol_augment_assistant.exe` 与 `result/bin/lol_augment_assistant.exe` 均为 868,864 bytes，mtime 均为 2026-08-26 00:47:00，SHA-256 均为 `B0726F47230E1F98EE4124091FEC0DE66E7BFA9D9BFFFC71F6FFA1FDA1A26075`；`result/SHA256SUMS.txt` 的 EXE 条目一致。 |
| CTEST 21/21 | PASS | 独立解析 `LastTest.log`：21 个 `x/21 Testing:` 条目、21 个 `Test Passed.`、0 个 `Test Failed.`；首项 `common_contracts_test`，末项 `phase2_benchmark_contract`。日志中所有二进制命令均来自 `outputs/tmp/build_phase2_final_verify/bin`。未重跑会改写测试目录的 CTest。 |
| final real replay | PASS | session `phase1-88c4fa4f949115b1b104fe4e5d6b722c` 的 `accepted_offer` 给出 n5 三个最终 ID：LEFT=`ARAM_Impassable`、CENTER=`Equilibrium`、RIGHT=`ARAM_CelestialBody`，与 annotation 逐项一致，最终 ID 3/3。backend=`windows_media_ocr:zh-CN`，source=`replay`。 |
| n5 真实性 | PASS（样本量限定） | `phase2_final_dataset_validation.json`：9/9 real，6 个 original-WGC，其中 5 个因 `modal_disconnect_or_afk_occlusion` 跳过；唯一 original-WGC benchmark-eligible 样本是 `sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6`。其 metadata 为 `capture_boundary=original_wgc`、`source.kind=windows_graphics_capture`、`derived_from_preview=false`。 |
| benchmark 分母/边界 | PASS（n=1） | benchmark 输入仅含 n5：sample denominator=1、card denominator=3；screen detection=1/1、final card ID=3/3、three-card all-correct=1/1、false match=0/3、UNKNOWN=0/3。3 个 preview-derived 样本被 validator 明确排除于 original-WGC 指标；无 synthetic 入分母。平均/P95 911.1959 ms 的分母均为 1。 |
| icon accepted 0/3 | PASS | n5 producer 三槽 icon 均为 `state=UNAVAILABLE, augment_id=null`；benchmark `icon_accuracy=0`, numerator=0, denominator=3。没有把 icon 误报为成功。 |
| OCR component 3/3 | **FAIL / P1** | 虽然 `protocol.ocr_executed=true` 且最终融合 ID 3/3，但三槽 OCR 均为 `state=UNAVAILABLE, text=null`；benchmark `ocr_accuracy=0`, numerator=0, denominator=3，并列出三个 `ocr_mismatch`。现有证据无法把最终 ID 3/3等同为 OCR component 3/3。 |
| 源码安全 API / PE imports | PASS（限定表述） | `src` 扫描未发现 focus 操纵、输入注入或跨进程内存 API：`SetForegroundWindow`、`SetFocus`、`AttachThreadInput`、`SendInput`、`keybd_event`、`mouse_event`、`OpenProcess`、`ReadProcessMemory`、`WriteProcessMemory` 等均无产品调用；PE import 同样无这些符号。唯一相关 import 是 `GetAsyncKeyState`，源码两处都只读 `VK_F8`，用于显式 sample collection hotkey。故可声明“无焦点操纵/输入注入/进程内存访问”，不可字面声明“零 input API”。 |
| PE 源码绝对 icon 路径 | PASS | build/result PE 的 UTF-8/UTF-16 strings 仅发现相对运行时路径 `data/knowledge/augment_icons/manifest.json`，未发现 `F:\Realworld\lol\data\knowledge\augment_icons\...` 等源码绝对 icon 路径。PE 仍含编译器/源码调试路径（例如 Windows Kits 和一个 `src/capture/*.cpp` 路径），所以不可扩张为“PE 不含任何绝对路径”。 |
| package hash 完整性 | PASS | 独立逐项验证 `result/SHA256SUMS.txt`：181 entries 对应 181 个非 manifest 文件；hash mismatch=0、missing=0、unlisted=0、absolute/escape path=0、duplicate path=0。 |
| package 路径可移植性 | PASS（有前置条件） | `runtime_paths_test` 在同一 21/21 run 中通过；final package replay session `phase1-01151595b390af8be2c8c82a6183beb1` 再现同一三 ID 和同一 RAW/LEFT/CENTER/RIGHT 内容 hash。EXE 使用相对 icon manifest。包不含 DLL，PE 动态依赖 `MSVCP140.dll`、`VCRUNTIME140.dll`、`VCRUNTIME140_1.dll`；因此只可声明“在文档所列 Windows/MSVC runtime 前置条件下可复制、跨 cwd 运行”，不可声明 clean-machine/self-contained。 |
| CLI target selector / 启动器拒绝 | PASS | `SelectWindowByTitle` 只接受唯一、可见、大小写精确的 `League of Legends (TM) Client`；精确 launcher 标题 `League of Legends` 被拒。`SelectWindowByHandle` 在选择后重新校验可见性和精确标题，并拒绝 launcher HWND。`cli_test` 在 final CTEST 中通过，测试覆盖 title、substring、大小写、launcher HWND、关闭窗口和歧义窗口。 |

## 可声明

- 本审计快照中的 final build EXE 与交付 EXE 是同一 SHA-256：`B0726F47230E1F98EE4124091FEC0DE66E7BFA9D9BFFFC71F6FFA1FDA1A26075`。
- final build 的既有 CTEST 证据为 21/21 通过。
- 唯一纳入 original-WGC benchmark 的真实 n5 样本，最终融合 augment ID 为 3/3；icon component 接受为 0/3。
- benchmark 的有效真实性范围是 **1 个 original-WGC 三卡样本 / 3 张卡**；preview-derived 与 synthetic 未进入该分母。
- 产品没有焦点操纵、输入注入、跨进程内存读写；selector 拒绝 launcher。
- `result` hash manifest 完整，资源路径在已声明运行时前置条件下可移植。

## 不可声明

- 不可声明“Phase 2 全部 gate PASS”或“最终签收 PASS”。
- 不可声明“OCR component 3/3”或“OCR accuracy 100%”；机器可读结果是 0/3。
- 不可把最终融合 ID 3/3 攻写为 OCR 文本 3/3，也不可把 `ocr_executed=true` 当作 OCR 命中证据。
- 不可从 n=1 推广总体识别准确率、跨分辨率/UI scale 稳健性或性能分布。
- 不可声明“零 input API”；存在仅限 F8 的 `GetAsyncKeyState`。
- 不可声明 package 是不依赖 VC runtime 的 clean-machine/self-contained 包。
- producer report 记录了 final build 的绝对 executable 路径，但没有内嵌 executable SHA-256；本报告能证明审计快照中该路径与 `result` hash 相同，不能把历史执行提升为带不可变 hash attestation 的证据。

## 精确哈希（SHA-256）

| Artifact | SHA-256 |
|---|---|
| final build EXE | `B0726F47230E1F98EE4124091FEC0DE66E7BFA9D9BFFFC71F6FFA1FDA1A26075` |
| `result` EXE | `B0726F47230E1F98EE4124091FEC0DE66E7BFA9D9BFFFC71F6FFA1FDA1A26075` |
| final CTEST `LastTest.log` | `2B3FDD080FCCA2BAF9F1CB2CC177504A1FD53474C43C2A0717AB5472840EDCC0` |
| final real replay `events.jsonl` | `3DC987CA9A055718C792D0BAD7740275C6D67F27461B381E2EADD3884F8127CD` |
| final real replay `session.sqlite3` | `1FE872F0F4722E9548DDAC97BE43E349AA9D6DC47B8E049067A95058B9AF642F` |
| replay producer report | `F938DB1E0C56B1B84DF7B66BC55557ADEFE13CCCCDE63720ECC61C4DB294C731` |
| replay producer results JSONL | `4B3A3F086E9F088454133CCFC6238729C3C2A5C97C31AAF9D20A1D3AA79A1EEC` |
| final benchmark JSON | `21CFF706FA4ECFBDF2A976C6ABB80196FD7CAAD591F132EF6317C09E2D83FE87` |
| final dataset validation JSON | `C710D36233BF0D527DDE409D73C69755471D4AA818510A296DBA9733422BC51B` |
| `result/SHA256SUMS.txt` | `0D5800144A8BA31C6D8D7C7E4511A0247E12E0D22454429A19856488CD161316` |

## 签收决定

**拒绝最终 PASS 签收。** 解除条件仅有一个：用同一 final EXE hash 产出可机读、逐槽可追溯的 OCR component 结果，并使 n5 OCR component 达到 3/3；不能只复用最终融合 ID。修复后还应把 executable SHA-256 写入 producer report，以封闭历史执行的 hash attestation 缺口。

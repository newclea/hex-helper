# Phase 1 最终交付核验

- 核验日期：2026-08-25（Asia/Shanghai）
- 工作区：`F:\Realworld\lol`
- 产品版本：`lol_augment_assistant 0.1.0 (Phase1 PoC)`
- 最终 EXE 源：`outputs/tmp/wave4_main_verify/bin/lol_augment_assistant.exe`

## 1. 统一构建与测试

统一 Release 构建的 `Testing/Temporary/LastTest.log` 记录 16 个测试全部执行完成，末项为 `16/16 Test: json_loads_validation`，失败数为 0。测试中的 detector 图片和 fake OCR 属于单元/合成证据；OCR smoke 是空白/受控输入；这些结果不等同于真实 LoL 验收。

```text
CTest: 16/16 passed, 0 failed
augment import: records=655, mode counts CHERRY/KIWI/KIWI_JADE=44/220/188
JSON validation: Python json.loads parsed all generated UTF-8 JSON documents
```

## 2. 最终产品烟测汇总

| 场景 | 结果 | 数据边界 |
|---|---|---|
| 单图 Replay | 5 passes，`completed_unknown` | 合成/非真实海克斯输入；证明单图重复 pass 和状态机可结束，不证明识别准确率 |
| Live | `received=150`、`converted=149`、`frames_processed=8` | 非 LoL 可见窗口短时烟测；证明 WGC→staging→CPU→pipeline 链路，不证明真实 LoL 或长时稳定 |
| 安全 | 源码禁用 API 0 命中；最终产品 PE 禁用 import 0 命中 | 只读屏幕/replay 边界；无内存读写、注入、Hook、网络下载或输入模拟路径 |

Replay 与 Live 的 stdout 均按“一行一个 JSON object”解释；session 运行产物为 SQLite、JSONL，接受 offer 时额外生成 RAW/三卡 PNG 与 sidecar JSON。单图 unknown 没有被包装成识别成功。

## 3. 发布物核验项

```text
result/
├─ bin/lol_augment_assistant.exe
├─ data/knowledge/augments.zh-CN.json
├─ config/default.json
├─ scripts/run.ps1
├─ scripts/run_replay.ps1
└─ Exp/README.md
```

```powershell
Get-Content -Raw .\config\default.json | ConvertFrom-Json | Out-Null
Get-Content -Raw .\result\config\default.json | ConvertFrom-Json | Out-Null
Get-Content -Raw .\result\data\knowledge\augments.zh-CN.json | ConvertFrom-Json | Out-Null
Get-FileHash -Algorithm SHA256 .\outputs\tmp\wave4_main_verify\bin\lol_augment_assistant.exe
Get-FileHash -Algorithm SHA256 .\result\bin\lol_augment_assistant.exe
.\result\scripts\run.ps1 --help
```

最终 EXE 源文件在组包前的 SHA-256 为 `9C045F1EC9E3F3E695AFB3DF1556737613972695DCB35A71F468E2518B70661E`，大小 731648 bytes。组包后要求发布 EXE 与该源文件 SHA-256 完全一致。发布脚本必须从自身目录定位 `result/bin`、`result/data`，默认写 `result/runtime`，且不含隐式下载。

## 4. 事实边界与 OPEN 项

- 本阶段没有运行真实 LoL，没有真实海克斯截图；真实 detector/OCR/ID 准确率和稳定性未验收。
- detector 固定近似 16:9，不支持或未校准多分辨率、UI scale、HDR、hover。
- icon matcher 当前为 `unavailable/template_unavailable`；没有图标识别成功证据。
- SQLite transaction、JSONL rollback 和 artifact 清理覆盖正常失败；跨 SQLite/JSONL/artifacts 的断电级 crash atomicity 仍为 **OPEN**。
- Phase 2 必须先做真实截图采集、版本化标注、benchmark、图标模板以及多分辨率/UI scale/HDR/hover 校准。

结论：Phase 1 可作为离线、只读、边界明确的 PoC 发布；不得将 16/16、单图 synthetic smoke 或非 LoL Live smoke 表述成真实 LoL 识别验收通过。

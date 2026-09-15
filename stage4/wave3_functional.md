# Wave3-A 独立黑盒功能验收报告

## 结论

**PASS**

- 验收日期：2026-08-25（Asia/Shanghai）
- 工作目录：`F:\Realworld\lol`
- 验收方式：仅调用公开脚本、产品 CLI、产品输出文件及 SQLite/JSONL 只读接口；未读取或修改产品源码实现。
- Build/Test/Runtime/Failure 四类证据均闭环；未发现 P0-P3 阻断缺陷。
- `recognition_unknown` 未判失败：最终 Replay 的第三帧已由产品自报 `ocr_executed=true`，但输入没有真实文字，因此本报告不评价、也不宣称任何 LoL 识别准确率。

## 1. Build 证据

执行命令：

```powershell
& '.\scripts\test.ps1' -Configuration Release -BuildDirectory 'outputs/tmp/audit_functional/build' -Clean
```

结果：

- 脚本退出码：`0`
- build root：`F:\Realworld\lol\outputs\tmp\audit_functional\build`
- configure exit code：`0`
- build exit code：`0`
- Release 产品：`F:\Realworld\lol\outputs\tmp\audit_functional\build\bin\lol_augment_assistant.exe`
- CMake：`D:\Env\MinGW\bin\cmake.exe`
- Visual Studio：`D:\Downloads\VisualStudio\Enterprise`，VS 17 2022 x64
- Windows SDK：`10.0.26100.0`

## 2. Test / CTest 证据

CTest 由上述 `scripts/test.ps1` 在同一 clean Release build 中执行。

- CTest 退出码：`0`
- 测试总数：`16`
- 通过：`16`
- 失败：`0`
- 汇总：`100% tests passed, 0 tests failed out of 16`
- 总耗时：`9.55 sec`

本次实际测试项：

1. `common_contracts_test`
2. `capture_minimal_test`
3. `detector_test`
4. `text_matcher_test`
5. `recognition_pipeline_test`
6. `ocr_smoke_test`
7. `augment_catalog_test`
8. `state_worker_tests`
9. `storage_worker_tests`
10. `replay_tests`
11. `output_tests`
12. `cli_test`
13. `session_runtime_test`
14. `augment_frame_processor_test`
15. `augment_import_determinism`
16. `json_loads_validation`

## 3. Runtime 证据

### 3.1 help / version / list

```powershell
& '.\outputs\tmp\audit_functional\build\bin\lol_augment_assistant.exe' --help
& '.\outputs\tmp\audit_functional\build\bin\lol_augment_assistant.exe' --version
& '.\outputs\tmp\audit_functional\build\bin\lol_augment_assistant.exe' --list-windows
```

结果：

- `--help`：退出码 `0`；展示三种 source（replay/hwnd/window-title）及 knowledge/workspace/preview/max-seconds/once/selected 等公开参数。
- `--version`：退出码 `0`；输出 `lol_augment_assistant 0.1.0 (Phase1 PoC)`。
- `--list-windows`：退出码 `0`；输出 1 行 JSON，`type=window_list`，枚举 `14` 个窗口。
- 对 `--list-windows` stdout 使用 Python 3.11 逐行 `json.loads`：第 1 行通过，总计 1 行，解析器退出码 `0`。

用于产品 JSON stdout 的实际解析逻辑：

```python
for lineno, raw in enumerate(sys.stdin, 1):
    line = raw.rstrip("\r\n")
    if not line:
        continue
    obj = json.loads(line)
```

`--help` 和 `--version` 按公开约定为人类可读文本，不作为 JSON 解析对象；`--list-windows`、Replay、Live 的每个非空 stdout 行均执行了上述 `json.loads`。

### 3.2 三帧 Replay 与真实 OCR 调用

首先从 `outputs/tmp` 选择并实际解码以下三个预存 PNG：

- `outputs/tmp/main_verify_build/artifacts/replay_cases_30824/ordered/frame1.png` → `2x2`，SHA-256 `21D23AFDCDDE6C6EDF3B772E5FCC9CD9607DC77722560C2F054A1D393829DEAB`
- `outputs/tmp/main_verify_build/artifacts/replay_cases_30824/ordered/frame2.png` → `2x2`，SHA-256 `2F9CFFEA61FDBB18DA7E9B27F0DD45FA2CB4C79219B0B6A0317D8F15CF88B041`
- `outputs/tmp/main_verify_build/artifacts/replay_cases_30824/ordered/frame10.png` → `2x2`，SHA-256 `E3C05A2F3993BCE4E3BAE8A25A9173228227DCBAA312903FD9EFFA59B4D0F476`

直接三帧资格运行确实处理了 3 帧并退出 `0`，但产品将 `2x2` 判为 `unsupported_aspect_ratio`，故 `ocr_executed=false`。该结果没有被冒充为 OCR 成功；对应诊断 session 为：

`F:\Realworld\lol\outputs\runtime\phase1-c4ed15f537f7e6625686ae1ae02803d7`

为仅验证产品真实 OCR 路径，将上述三个 PNG 分别作为底图标准化为 `1920x1080`，并只在产品黑盒输出的三个 card ROI 内加入无文字高对比边缘。最终实体 PNG 位于：

`F:\Realworld\lol\outputs\tmp\audit_functional\replay_ocr_final`

- `001.png`：PNG `1920x1080`，SHA-256 `45564937851080EA987F10054BA143CA5FE9815D36F135E01689450517AA5694`
- `002.png`：PNG `1920x1080`，SHA-256 `49AE1ABA886841A50354D21E8FC14C75AE6AE6D280FD7326D6A3610C1830D5CD`
- `003.png`：PNG `1920x1080`，SHA-256 `160A461095316E268EA591316E5F853701D249D537F0E0B886BDC233A4FB3136`

最终命令：

```powershell
& 'F:\Realworld\lol\outputs\tmp\audit_functional\build\bin\lol_augment_assistant.exe' --replay 'F:\Realworld\lol\outputs\tmp\audit_functional\replay_ocr_final' --workspace 'F:\Realworld\lol\outputs\runtime'
```

结果：

- 产品退出码：`0`
- stderr：空
- stdout：5 个非空行，Python `json.loads` 逐行全部通过，解析器退出码 `0`
- `session_start`：1 行
- frame 0：`raw_detector.visible=true`，`awaiting_stability:1/3`，`ocr_executed=false`
- frame 1：`raw_detector.visible=true`，`awaiting_stability:2/3`，`ocr_executed=false`
- frame 2：`stable_detector.visible=true`，`reason=stable_visible`，`ocr_executed=true`，最终 `reason=recognition_unknown`
- `session_end`：`status=completed`，`frames_processed=3`，`available_frames=3`，`exhausted=true`，`close_ok=true`
- metadata backend：`windows_media_ocr:zh-CN`
- 最终 Replay session：`F:\Realworld\lol\outputs\runtime\phase1-ab94416acecfedd4a85c634077da467b`

该证据仅证明真实产品 OCR 后端被执行；因为三帧无真实文字，`recognition_unknown` 符合验收边界。

### 3.3 安全可见窗口 Live 0.5 秒

`--list-windows` 返回两个标题为“设置”的窗口。Live 使用明确 HWND `0xB0B14`，不做键盘、鼠标或进程内操作：

```powershell
& 'F:\Realworld\lol\outputs\tmp\audit_functional\build\bin\lol_augment_assistant.exe' --hwnd '0xB0B14' --max-seconds '0.5' --workspace 'F:\Realworld\lol\outputs\runtime'
```

结果：

- 产品退出码：`0`
- stderr：空
- stdout：3 个非空行，Python `json.loads` 逐行全部通过，解析器退出码 `0`
- 捕获：`received=1`，`converted=1`，`dropped=0`
- 帧：`2560x1528`，`valid=true`
- 结束：`status=timeout`（达到 0.5 秒上限），`frames_processed=1`，`close_ok=true`
- 该非 LoL 设置窗口被检测器判为 `unsupported_aspect_ratio`，因此 `ocr_executed=false`；这不用于 LoL 识别结论。
- Live session：`F:\Realworld\lol\outputs\runtime\phase1-eb562c21ac5197b5b06875f2465a2be7`

## 4. Session / SQLite / JSONL / 路径证据

对最终 Replay 和 Live 数据库均以 Python `sqlite3` 的 `mode=ro` 打开，并执行：

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
SELECT COUNT(*) FROM sessions;
SELECT COUNT(*) FROM augment_offers;
SELECT COUNT(*) FROM recognition_results;
SELECT COUNT(*) FROM artifacts;
```

同时对 `sessions.metadata_json`、offer/recognition JSON（若有）及 `events.jsonl` 每个非空行执行 `json.loads`；对 session 根目录、递归文件和 artifact 相对路径执行 resolved-path workspace 包含性检查。

### Replay session `phase1-ab94416acecfedd4a85c634077da467b`

- workspace：`F:\Realworld\lol\outputs\runtime`
- `PRAGMA integrity_check`：`ok`
- `PRAGMA foreign_key_check`：0 行违规
- FK 定义：offers→sessions 1 条，recognition→offers/sessions 2 条，artifacts→sessions 1 条
- 行数：`sessions=1`，`augment_offers=0`，`recognition_results=0`，`artifacts=0`
- `sessions.metadata_json`：`json.loads` 通过
- `events.jsonl`：3 行，逐行 `json.loads` 通过；顺序 `start → diagnostic → end`
- 生成文件：`events.jsonl`、`session.sqlite3`，二者均位于该 workspace 内

### Live session `phase1-eb562c21ac5197b5b06875f2465a2be7`

- workspace：`F:\Realworld\lol\outputs\runtime`
- `PRAGMA integrity_check`：`ok`
- `PRAGMA foreign_key_check`：0 行违规
- FK 定义：offers→sessions 1 条，recognition→offers/sessions 2 条，artifacts→sessions 1 条
- 行数：`sessions=1`，`augment_offers=0`，`recognition_results=0`，`artifacts=0`
- `sessions.metadata_json`：`json.loads` 通过
- `events.jsonl`：3 行，逐行 `json.loads` 通过；顺序 `start → diagnostic → end`
- 生成文件：`events.jsonl`、`session.sqlite3`、`session.sqlite3-shm`、`session.sqlite3-wal`，全部位于该 workspace 内

零 offer/recognition/artifact 与本次两种输入未产生已接受 offer 一致；特别是 Replay 已执行 OCR 但结果为 unknown。按验收约束，这不是失败，也不构成准确率证据。

本轮共生成 5 个产品 session（含 3 个输入资格/稳定性黑盒探测 session）。逐一复核后：5/5 根路径及递归文件均在各自传入的 workspace 内，5/5 `integrity_check=ok`，5/5 FK 零违规：

- `outputs/runtime/phase1-c4ed15f537f7e6625686ae1ae02803d7`
- `outputs/runtime/phase1-eb562c21ac5197b5b06875f2465a2be7`
- `outputs/runtime/phase1-ab94416acecfedd4a85c634077da467b`
- `outputs/tmp/audit_functional/probe_runtime/phase1-b0d8702a95533f67f524eb723ee78248`
- `outputs/tmp/audit_functional/probe_edges_runtime/phase1-e32e9ef0d749f62d5dbb09c967ad8c75`

## 5. Failure 证据

三类失败均在参数/输入校验阶段得到非零退出和结构化 stderr；未修改缺陷或产品文件。

### 5.1 坏 knowledge

```powershell
& 'F:\Realworld\lol\outputs\tmp\audit_functional\build\bin\lol_augment_assistant.exe' --replay 'F:\Realworld\lol\outputs\tmp\audit_functional\replay_3frames' --knowledge 'F:\Realworld\lol\outputs\tmp\audit_functional\bad_knowledge.json' --workspace 'F:\Realworld\lol\outputs\tmp\audit_functional\failure_bad_knowledge'
```

- 产品退出码：`3`
- stdout：空
- stderr type：`pipeline_error`
- 诊断：`Unable to load augment catalog: expected object key at byte 2`

### 5.2 坏 Replay

```powershell
& 'F:\Realworld\lol\outputs\tmp\audit_functional\build\bin\lol_augment_assistant.exe' --replay 'F:\Realworld\lol\outputs\tmp\main_verify_build\artifacts\replay_cases_30824\corrupt.png' --workspace 'F:\Realworld\lol\outputs\tmp\audit_functional\failure_bad_replay'
```

- 产品退出码：`3`
- stdout：空
- stderr type：`pipeline_error`
- 诊断包含：`Rejected replay input`、`IWICImagingFactory::CreateDecoderFromFilename failed`、HRESULT `0x88982f50`

### 5.3 ambiguous title

```powershell
& 'F:\Realworld\lol\outputs\tmp\audit_functional\build\bin\lol_augment_assistant.exe' --window-title '设置' --max-seconds '0.5' --workspace 'F:\Realworld\lol\outputs\tmp\audit_functional\failure_ambiguous'
```

- 产品退出码：`3`
- stdout：空
- stderr type：`source_error`
- 诊断：`Window title substring is ambiguous`
- candidates：`0xB0B14` 与 `0x3309E4`，标题均为“设置”

## 6. P0-P3 阻断问题

- **P0：无。**
- **P1：无。**
- **P2：无。**
- **P3：无。**

覆盖边界（非缺陷）：本次没有包含带真实 LoL 增幅选择文字的截图；只验到 WGC、Replay、稳定检测、Windows Media OCR 调用、unknown 处理、session 持久化与失败诊断。不得从本报告推导 LoL 识别准确率。

## 7. 写入边界

- 唯一正式产物：`F:\Realworld\lol\outputs\wave3_functional.md`
- 临时产物仅在：`F:\Realworld\lol\outputs\tmp\audit_functional\**`
- 产品运行产物仅在：`F:\Realworld\lol\outputs\runtime\**` 或审计临时 workspace
- 未修改任何源码、配置、既有脚本、result 或 stage 文件；未联网、未使用 IDA/逆向/Hook/进程内存、未执行 Git 操作、未做业务写入。

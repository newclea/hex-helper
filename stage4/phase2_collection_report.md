# Phase 2 SampleCollector 独立库交付报告

## 结论

Phase 2 `SampleCollector` 已作为独立 collection 模块实现并完成合成帧验证。它不依赖 detector visible、stable gate、recognition accepted 或 offer accepted；调用方只要提供有效 owning BGRA8 `Frame` 与像素 ROI，即可因 `detector_suspect` 或 `manual_f8` 发起采集。

本轮没有使用真实游戏帧，没有启动、操纵或连接 LoL，因此不声称 2560x1600 真实窗口实测。验证数据全部为测试程序生成并明确标为 `unknown`/Replay 来源的合成像素。

本轮只新增以下源码/测试/报告，未修改 CMake、app/cli/main、capture、storage、replay、detector 或 vision：

- `include/lol_assistant/collection/sample_collector.h`
- `src/collection/sample_collector.cpp`
- `tests/collection/sample_collector_test.cpp`
- `outputs/phase2_collection_report.md`

独立编译产物与测试数据位于 `outputs/tmp/phase2_collection/**`。

## API 与接线说明

主要入口是：

```cpp
lol_assistant::collection::SampleCollector collector({
    dataset_root,
    {
        std::chrono::seconds{5},  // minimum_interval
        4U,                       // maximum_dhash_distance
        true,                     // manual bypasses interval+dHash gate
        false,                    // manual still rejects exact bytes
    },
});

lol_assistant::collection::SampleCollectionRequest request;
request.sample_kind = SampleKind::Unknown;  // Real/Synthetic/Unknown
request.capture_reason = CaptureReason::DetectorSuspect;  // or ManualF8
request.rois = caller_supplied_pixel_rois;
request.detector = {visible, score, reason};
request.cards = caller_supplied_ocr_diagnostics;

const CollectResult result = collector.Collect(raw_frame, request);
```

Integration Owner 的接线顺序：

1. 在会话/进程级创建一个 `SampleCollector`，`dataset_root` 必须是绝对路径且不得含 `..`。
2. 在 recognition accepted/offer accepted 分支之前，独立判断 `detector_suspect` 或 F8 请求；采集调用不得放进 accepted gate。
3. 为 2560x1600 等 detector 当前拒绝的分辨率提供调用方计算好的像素 ROI。Collector 故意不调用 detector，也不要求 16:9，只校验 offer/card/title/icon ROI 均位于原始帧内且层级包含关系成立。
4. 按真实来源填写 `sample_kind`：`real` 仅接受 WGC/Desktop Duplication 帧，并要求 window title 或 id；Replay/Stub 不能伪装成 `real`。不确定时填 `unknown`，它仍会保存。
5. OCR 未运行或失败时分别填 `NotRun`/`Failed`/`Unknown`，不要构造 matched id 或置信度；Collector 不要求识别成功。
6. 将 `src/collection/sample_collector.cpp` 加入后续正式构建目标，并链接已有 `contracts.cpp`、ROI、WIC 与 icon dHash 支撑。本任务按边界要求没有修改项目 CMake。
7. 处理 `CollectResult`：`Saved` 返回已提交目录；`Duplicate` 返回导致抑制的上一份样本目录；其余状态携带失败原因且不会返回一个看似完成的目录。

`ISampleImageWriter` 是窄依赖注入点：默认实现调用现有 `WicImageCodec::SavePng`，测试使用它注入第三次 PNG 写入失败，验证事务回滚。它不复制 Capture，也不引入输入或网络能力。

## metadata.json 契约

当前固定：

- `schema = "lol_assistant.sample_collection"`
- `version = 1`
- `sample_kind = real | synthetic | unknown`
- `capture_reason = detector_suspect | manual_f8`

每份 metadata 包含：sample id、UTC timestamp、source kind/id、window title/id/process id、width/height/stride、nullable `ui_scale`、offer/card/title/icon 像素 ROI、detector visible/score/reason，以及固定 left/center/right 顺序的 OCR raw/lines/matched id/status/nullable confidence。

另外记录 exact hash/dHash 算法、阈值、最短间隔、manual 策略和五个固定文件名。写入使用现有 JSON UTF-8 清洗、转义、浮点和 UTC 时间支撑。

## 去重策略

去重只针对“上一份成功提交的页面”，符合防止同一页面连续落几十份的目标；失败尝试不推进去重状态。

1. exact gate：直接比较完整 raw BGRA buffer 字节，不依赖 64 位 hash 判等。默认所有请求都拒绝 exact duplicate；`manual_f8` 也一样。只有显式配置 `manual_allows_exact_duplicate=true` 才可覆盖。
2. interval+dHash gate：非 bypass 请求若距离上一份成功样本小于 `minimum_interval`，且 64 位 dHash Hamming distance 小于等于 `maximum_dhash_distance`，则抑制。
3. manual 默认行为：`manual_bypasses_minimum_interval=true` 时，F8 绕过 interval+dHash gate，因此短间隔内有字节变化但视觉相似的帧仍可保存；exact gate 仍生效。
4. 超过最短间隔后，相似但非 exact 的帧允许再次保存。该状态当前是进程内状态，进程重启后不扫描历史数据集。

## 原子性与路径边界

每份样本先写入 dataset root 下唯一的 `.tmp_<sample-id>` 目录。写完后逐项确认恰好存在五个非空普通文件：

- `RAW.png`
- `LEFT_CARD.png`
- `CENTER_CARD.png`
- `RIGHT_CARD.png`
- `metadata.json`

随后重新读取 metadata，要求内容与生成值一致且通过严格 JSON 语法解析，最后在同一 dataset root 内执行目录 rename 提交。已存在的 staging/final 路径一律拒绝覆盖。

发生异常时只回滚本次调用刚创建、仍带 `.tmp_` 名称的 staging 目录；不会删除或覆盖既有/已提交样本。若底层文件系统连回滚删除也失败，结果消息会显式追加 `rollback_failed`，遗留目录仍保持 `.tmp_` 名称，不会伪装成完整样本。

所有生成目录和五个文件在每次 I/O 前都执行 dataset-root descendant 检查；配置拒绝相对 root、含 `..` 的 root 和 dHash 阈值大于 64 的策略。

## 单元测试覆盖

`tests/collection/sample_collector_test.cpp` 覆盖 4 个场景组、50 个断言：

- unknown 在 detector invisible、OCR unknown/not-run 条件下仍保存；
- 五文件名称与数量严格一致，四张 PNG 非空且可由 WIC 回读；
- metadata 内部严格解析和外部字段检查；
- 相同 raw 字节去重；
- dHash 明显变化帧在最短间隔内保存；
- 相同 dHash、不同字节帧在最短间隔内抑制，间隔后保存；
- manual F8 绕过 interval+dHash gate，但默认不绕过 exact gate；
- 第三次 PNG 写入失败后回滚，dataset 目录内零残留；
- writer 收到的所有路径都位于 dataset root；
- relative/`..` root、非法阈值失败关闭；
- Replay 合成帧不能标成 `real`。

## 精确验证命令与结果

工作目录均为 `F:\Realworld\lol`，未使用网络。

### 1. 独立 cl.exe 编译

```powershell
$buildRoot='F:\Realworld\lol\outputs\tmp\phase2_collection'; $objRoot=Join-Path $buildRoot 'obj'; New-Item -ItemType Directory -Force -Path $buildRoot,$objRoot | Out-Null; $msvc='D:\Downloads\VisualStudio\Enterprise\VC\Tools\MSVC\14.44.35207'; $sdk='C:\Program Files (x86)\Windows Kits\10'; $sdkVersion='10.0.26100.0'; $env:INCLUDE="F:\Realworld\lol\include;$($msvc)\include;$($sdk)\Include\$sdkVersion\ucrt;$($sdk)\Include\$sdkVersion\shared;$($sdk)\Include\$sdkVersion\um;$($sdk)\Include\$sdkVersion\winrt"; $env:LIB="$($msvc)\lib\x64;$($sdk)\Lib\$sdkVersion\ucrt\x64;$($sdk)\Lib\$sdkVersion\um\x64"; $cl=Join-Path $msvc 'bin\Hostx64\x64\cl.exe'; $args=@('/nologo','/std:c++20','/W4','/WX','/permissive-','/EHsc','/utf-8','/Zc:__cplusplus','/DNOMINMAX','/DWIN32_LEAN_AND_MEAN','/DUNICODE','/D_UNICODE','/D_WIN32_WINNT=0x0A00','/DWINVER=0x0A00','/DLOL_COLLECTION_TEST_ROOT=L"F:/Realworld/lol/outputs/tmp/phase2_collection/dataset"','tests\collection\sample_collector_test.cpp','src\collection\sample_collector.cpp','src\common\contracts.cpp','src\detector\roi.cpp','src\vision\icon_matcher.cpp','src\replay\wic_image_codec.cpp',('/Fo'+$objRoot+'\'),('/Fd'+$buildRoot+'\sample_collector.pdb'),('/Fe'+$buildRoot+'\sample_collector_test.exe'),'/link','ole32.lib','windowscodecs.lib',('/PDB:'+$buildRoot+'\sample_collector_test.pdb')); & $cl @args; exit $LASTEXITCODE
```

结果：exit code `0`；MSVC 编译列出 6 个 translation units，`/W4 /WX` 下零 warning、零 error，生成 `outputs/tmp/phase2_collection/sample_collector_test.exe`。

### 2. 单元测试

```powershell
& 'F:\Realworld\lol\outputs\tmp\phase2_collection\sample_collector_test.exe'; exit $LASTEXITCODE
```

结果：exit code `0`。

```text
sample collector tests=4 checks=50 failures=0
```

### 3. Python 3.11 外部 JSON 与五文件审计

```powershell
py -3.11 -c "import json,pathlib; root=pathlib.Path(r'F:\Realworld\lol\outputs\tmp\phase2_collection\dataset').resolve(); files=list(root.rglob('metadata.json')); expected={'RAW.png','LEFT_CARD.png','CENTER_CARD.png','RIGHT_CARD.png','metadata.json'}; assert files; [json.loads(p.read_text(encoding='utf-8')) for p in files]; assert all({q.name for q in p.parent.iterdir()}==expected for p in files); assert all(p.resolve().is_relative_to(root) for p in files); print(f'python_json_audit metadata={len(files)} bundles={len(files)} status=PASS root={root}')"
```

结果：exit code `0`。

```text
python_json_audit metadata=10 bundles=10 status=PASS root=F:\Realworld\lol\outputs\tmp\phase2_collection\dataset
```

### 4. 临时目录与越界审计

```powershell
$root=(Resolve-Path -LiteralPath 'F:\Realworld\lol\outputs\tmp\phase2_collection\dataset').Path; $metadata=Get-ChildItem -LiteralPath $root -Filter metadata.json -File -Recurse; $temp=Get-ChildItem -LiteralPath $root -Directory -Recurse -Force | Where-Object Name -Like '.tmp_*'; $bad=@($metadata | Where-Object { -not $_.FullName.StartsWith($root + '\',[System.StringComparison]::OrdinalIgnoreCase) }); "powershell_path_audit metadata=$($metadata.Count) temp_dirs=$($temp.Count) outside_root=$($bad.Count)"; if($temp.Count -ne 0 -or $bad.Count -ne 0){ exit 1 }
```

结果：exit code `0`。

```text
powershell_path_audit metadata=10 temp_dirs=0 outside_root=0
```

## 已知边界

- 没有真实 2560x1600 帧，因此这里只证明 API、文件事务、去重策略和元数据契约在合成 BGRA8 帧上闭环，不证明真实 UI ROI 的准确性。
- dHash 为全帧 9x8 luma difference hash；阈值需由后续真实样本分布校准。
- 去重历史当前不持久化，重启后允许再次保存首份页面。这避免扫描/删除既有数据，也保持独立库无 storage 依赖。

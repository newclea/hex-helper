# Phase2 真实紧急样本入库报告

## 结论

- 已从指定的 135 张连拍中只选取 3 张内容唯一的代表帧，生成 3 个 `provenance=real` 样本；没有批量灌入相邻重复帧。
- 每个样本均包含 `RAW.png`、`LEFT.png`、`CENTER.png`、`RIGHT.png`、`metadata.json`、`annotation.json`。
- 这批数据不是原始 WGC 帧。它们来自 `windows_graphics_capture_debug_preview_gdigrab` 链路，是 debug preview 截图的机械裁剪派生物；`derived_from_preview=true`。
- 9 张卡的真值均由 `scripts/phase2/annotate_dataset.py set` 非交互命令写入。metadata 中没有把 OCR 文本或人工标签伪装为识别结果；OCR 状态为 `not_run`。
- 数据集验证退出码为 0：3 个真实样本、9 张有效人工标注卡、3 个 benchmark-eligible 样本、0 issue、0 重复 sample hash。
- 当前没有 recognition-result 流。benchmark 因而诚实输出 `insufficient_real_data`，所有准确率和延迟指标均为 `null`/`N/A`，没有把 annotation 当 prediction。

## Preview 边界与裁剪依据

坐标均为左上原点的半开 `xywh`。

- 三张来源 PNG 的实际分辨率均为 `1920x1080`，不是来源描述中的约数。
- 在 `y=100..699` 的横向条带上，FFmpeg `cropdetect` 对 A/B 给出非黑边界 `x=0..1279`；C 的 `x=1280..1287` 仅在顶部 `y=0..133` 有非黑像素。逐列扫描确认三张图的稳定游戏画面止于 `x=1279`。
- 在 `x=100..1099` 的纵向条带上，三张图均给出非黑边界 `y=0..719`。`y>=720` 的非黑像素只来自 debug-preview 画布上的鼠标或孤立叠加字形，不构成游戏画面。
- 已知窗口分辨率为 `2560x1600`；preview 的稳定宽度 `1280` 与原窗口恰为 `0.5x`。因此保留左上 16:10 包络 `raw_rect=(0,0,1280,800)`，其中 `active_picture_rect=(0,0,1280,720)`，`y=720..799` 是 16:10 包络内保留的黑色 letterbox。
- 黑色 letterbox 与外部黑色 preview 画布在纯像素上不可区分；`800` 高度不是由黑底猜边，而是由已验证的 `1280` 宽度、已知 `2560x1600` 窗口和精确 `0.5x` 缩放共同约束。
- 左上 debug FPS 文本的亮字 bbox 为 `(11,11,229,13)`。所有 title/icon ROI 均位于卡框内部的 `y>=150`，未使用该 overlay 区域。
- 来源 RGBA 的 alpha 不作为内容真值；派生图由 FFmpeg 显式输出为 `rgb24`，保留可见 RGB 平面。

## 卡框与子 ROI

金色样本的主边框阈值区间为：LEFT `x=254..489`、CENTER `x=527..762`、RIGHT `x=800..1035`、共同 `y=129..479`。向外保留 1 像素抗锯齿边界后，三张卡机械裁为：

| Slot | card rect | title rect | icon rect | 输出分辨率 |
|---|---|---|---|---|
| LEFT | `(253,128,238,353)` | `(283,284,178,23)` | `(298,150,148,120)` | `238x353` |
| CENTER | `(526,128,238,353)` | `(556,284,178,23)` | `(571,150,148,120)` | `238x353` |
| RIGHT | `(799,128,238,353)` | `(829,284,178,23)` | `(844,150,148,120)` | `238x353` |

三帧标题亮字的实测纵向范围均为 `y=289..302`，完整落在 title rect 内。上述 rect 已逐样本写入 metadata 的 `rois.cards[]`；card/title/icon 均使用 `derived_raw_pixels`，raw rect 使用 `debug_preview_pixels`。

## 样本与人工真值

| Sample ID | 来源 | LEFT | CENTER | RIGHT |
|---|---|---|---|---|
| `real-preview-1787670257978-f010` | `outputs/phase2_emergency_real/burst_1787670257978/frame_010.png` | `ARAM_Eureka` / 尤里卡 | `OminousPact` / 不祥契约 | `Overloaded` / 超负荷 |
| `real-preview-1787670257978-f020` | `outputs/phase2_emergency_real/burst_1787670257978/frame_020.png` | `ARAM_Eureka` / 尤里卡 | `OminousPact` / 不祥契约 | `ARAM_EndlessHunt` / 吃过路兵 |
| `real-preview-1787670572085-f0025` | `outputs/phase2_emergency_real/continuous_1787670572085/frame_0025.png` | `ARAM_CelestialBody` / 星界躯体 | `WarlockJuicebox` / 术士果汁盒 | `ARAM_WithHaste` / 急急小子 |

九次 `annotate_dataset.py set ... --valid true --augment-id ... --augment-name ...` 均退出 0；最终三个 annotation 均为 `status=complete`。验证器逐项确认 augment ID 存在于 `data/knowledge/augments.zh-CN.json` 且中文名匹配。

## SHA256 审计

| Sample ID | Source | RAW.png | LEFT.png | CENTER.png | RIGHT.png |
|---|---|---|---|---|---|
| `real-preview-1787670257978-f010` | `393c0f804c371b2b69425d90c50ef7e19fe2ce5d1e1ef4366ee46e606173c4fc` | `4dc2a3ff552db60387b0b1e006ac5e8efdbd21f503844fee34068c997e23b687` | `24ce0e742f49a36d06e8ecf403e257cc6c7b7332685c1bbbfbc0bde111c17ef2` | `541387e69fad132efba929d759082f3388cc4003e434a5f4f8a503475cb4dc23` | `db8fb95fa6dd734704c694043ae8cf0a34edc6b9a38be772835317a8908679a4` |
| `real-preview-1787670257978-f020` | `ce86e44107bcaffb64456a6a1769d39bf2458396c892c18b71e12a7c3aad3a87` | `c970346205561bbe9c9dd8d4fd2c65337eab83e845d4ffa4d2a9505703302368` | `d66c2d087d2ca22570c308b709620c56b5613818b78de741d98ba28fe03abbdf` | `810993d7b39511386caffde625d6349e4c2e8001fd6b00748a1e8deed622eb1d` | `b0b941de38e996a1aaf7e05d9b07af5e19513797ea02b94c7351b54ce7d597c6` |
| `real-preview-1787670572085-f0025` | `28b8a415ddd3a9bfe6b73bc115fa1184a73f2603850814f07bae46254ec32279` | `3c59d985107d78fed995ad4a8ba7b26132f7f7c8f8c466fd3227be21d91b6866` | `eafd8966be95b57aa7f419441e9bac0a2b5e3da6355ae933c809bd20ab392d44` | `ce841a69d0871d2e4512a7658a3718e70173de3b4a4f85c7d2e3f63f0cf87949` | `430bfb6fdd0cee5188412176f7234d5c89144a986fc782a59f3677abff64439f` |

验证结果：3 个 source SHA256 唯一，3 个 RAW SHA256 唯一，12 个 derived PNG SHA256 全部唯一。数据集组合内容哈希也全部唯一：

- `real-preview-1787670257978-f010`: `230bf62a4db80c529b00a19e64181f03290f0a4da43cff415b289c3114a04ba1`
- `real-preview-1787670257978-f020`: `170dcf19361ba634bd12e763d08161ad7b5131eba6c62d0104dad82088d137d9`
- `real-preview-1787670572085-f0025`: `4bf7980c84a184bb93bbb83438ebbbcac5ef5832c5e8f4ea5618e4046602fe8a`

3 张 source 与 12 张 derived PNG 均通过 FFmpeg 完整解码；dataset validator 另行完成 PNG chunk、CRC、IDAT 解压及 Pillow（可用时）校验。

## 验证与 benchmark

执行：

```powershell
python scripts/phase2/validate_dataset.py
python scripts/benchmark_phase2.py --dataset data/dataset/augment_offers --json-output - --markdown-output <temporary-output>
```

结果：

- `validate_dataset.py`: exit `0`，`valid=true`，`real_sample_count=3`，`valid_card_count=9`，`benchmark_eligible_sample_count=3`，`issues=[]`，`duplicate_sample_hashes=[]`。
- validator 产生 3 条非阻断 warning：任务要求的 `LEFT.png/CENTER.png/RIGHT.png` 被当前工具标记为一次迁移兼容命名；这不影响 exit 0 或样本 eligibility。没有额外写入 `*_CARD.png`，以保持每个样本恰好为任务指定的六个文件。
- `benchmark_phase2.py`: exit `0`，`status=insufficient_real_data`，`source_state=no_benchmark_records`，`input_sample_count=0`。`screen_detection_accuracy`、`ocr_accuracy`、`icon_accuracy`、`card_recognition_accuracy`、三卡全对率、unknown/false-match rate、平均/P95 延迟全部为 `null`；Markdown 对应为 `N/A`。
- benchmark 的 `raw_asset_count=22` 是 dataset 目录下全部文件数，不代表 22 个预测样本。当前没有 benchmark schema 的 recognition result，因此人工 annotation 没有进入预测分母。

机器可读输出：

- `outputs/phase2_real_dataset_validation.json`
- `outputs/phase2_real_benchmark.json`
- `outputs/phase2_real_benchmark.md`

## 数据边界

这 3 个样本可用于最早期真实 ROI/OCR 回归抓手，但不能代表原始 `2560x1600` WGC 图像质量、原始 UI scale、真实捕获吞吐或线上识别准确率。已知边界为：debug preview 二次采集、0.5x 几何缩放、preview overlay 存在、`ui_scale=null`、detector score 为 0 且原因是 `unsupported_aspect_ratio`、OCR 未运行、recognition result 不存在。

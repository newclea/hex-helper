# Phase2 独立视觉 / 数据真实性审计

审计工作区：`F:\Realworld\lol`  
审计收口日期：2026-08-26（Asia/Shanghai）  
结论：**不通过真实准确率签收。** 当前可以证明“采集、标注、ROI/OCR/icon 代码路径存在”，但不能计算或声明三卡全对率、UNKNOWN rate、OCR/icon/card accuracy、平均/P95 latency。磁盘上有 9 个 `real` bundle，却只有 1 个清晰 original WGC 三卡样本；它没有产品预测，且已被当前 ROI 参数用作校准依据，不是 held-out evaluation。

## 1. 审计口径与版本边界

- 只把产品实际输出当 prediction；`annotation.json` 只是真值，未被改写或计作预测。
- 3 个 `real-preview-*` 与 6 个 `sample_*` original WGC 分层统计；preview-derived 不进入原始 WGC 准确率。
- 5 个 `status=skipped / modal_disconnect_or_afk_occlusion` 样本只作遮挡证据，不充当 screen-absent negative，也不进入卡片准确率。
- 工作区没有 `.git`，无法以 commit 固定源码、binary、数据和报告。新增 WGC 的 n5 metadata 时间为 00:04:46；当前 `roi.cpp/roi.h` 为 00:16:36、detector 为 00:17:40，说明当前“measured ROI + border gate”是在该采集之后更新。n5 中保存的是采集时旧 ROI/旧 detector 结果，不能冒充当前源码执行结果。
- 现有 binary/契约测试只证明机制。实际跑过的旧 binary 测试为 detector `163/163`、title preprocessor `95/95`、icon matcher `46/46`；detector 自己明确打印“synthetic images ... do not measure LoL ROI or OCR accuracy”。这些 binary 早于上述当前 ROI/detector 源码。
- 审计误触发了一次既有 `augment_frame_processor_test.exe`；它在 `outputs/tmp/fix_processor/runtime/phase1-e85b26e2e2a943a284c029a91566df58` 生成了测试会话。精确清理被执行策略拒绝，因此这是本轮除本报告外唯一已知落盘副作用；它不是产品数据，也未进入任何指标。

## 2. 事实指标表

| 事实项 | 当前值 | 可用于什么 | 不可用于什么 |
|---|---:|---|---|
| dataset bundle | 9 `real` | 盘点 | 不能等同 9 个可评测 real samples |
| original WGC | 6，均为 `2560x1600`、`source.kind=windows_graphics_capture` | 原始采集链真实性 | 不能直接算识别准确率 |
| original WGC clear + complete | 1（n5） | 3 张卡的人工真值、旧 detector 单点失败证据 | 不是独立测试集；无 prediction |
| original WGC skipped | 5（均为 modal/AFK occlusion） | 遮挡/fail-closed 回归素材 | 不能当 5 个 screen-absent negatives |
| preview-derived complete | 3，`RAW=1280x800` | 早期 ROI/OCR 校准素材 | 不能代表 original WGC 图像质量或准确率 |
| 人工有效卡标签 | 12（preview 9 + original n5 3） | ground truth | 不是 12 个预测 |
| 产品 recognition-result | 0 | — | 所有识别率与 UNKNOWN rate 分母均为 0 |
| 完整样本 OCR 执行 | 0/4；三份 preview 与 n5 均为 `not_run` | 证明没有 OCR 结果被伪造 | 不能算 OCR accuracy/latency |
| 当前 validator 只读结果 | `valid=true`，9 real，4 complete/eligible，5 skipped，12 valid cards | schema/文件完整性 | eligibility 不是真实性或 held-out 证明 |
| 持久化 validator 输出 | 仍是旧快照：3 real、3 eligible、9 cards | 历史记录 | 已过期，不能描述当前 9-bundle 数据集 |
| 当前 benchmark loader 只读结果 | `source_state=no_benchmark_records`，58 assets，0 input samples | 证明 benchmark 没读到 prediction | 不能把 58 assets 说成 58 predictions |
| 持久化 benchmark 输出 | 旧快照：22 assets、0 samples，所有指标 `null` | 历史不足数据证据 | 已过期；不是 accuracy report |
| direct clear detector 观察 | n5：采集时 `visible=false`、score `0.931192636`、reason `insufficient_luma`、OCR `not_run` | 旧产品在 1 个清晰正例上的 false negative | 样本太少，不能称 detector accuracy |
| icon manifest | 245 templates；163 unique images；134 unique dHash；54 全局歧义组 | 模板覆盖/歧义审计 | 不能推导 real icon accuracy |

当前 validator 把 3 个 preview-derived + n5 共 4 个样本标成 `benchmark_eligible=true`。按本审计真实性边界，原始 WGC 候选只有 n5；又因当前 measured ROI 明确使用该 clear WGC/同批 preview 做校准，held-out original WGC evaluation 数仍为 **0**。

## 3. 明确不能声明的指标

| 指标 | 当前机器可读值 | 审计判定 | 缺少的最小证据 |
|---|---:|---|---|
| 三卡全对率 | `null`，denominator 0 | 不可声明；不是 0%，也不是 100% | held-out original WGC + 同一产品版本的三卡 predictions |
| UNKNOWN rate | `null`，denominator 0 | 不可声明；`not_run` 不能人工改写成 UNKNOWN | 每卡 final state 的产品输出 |
| card recognition accuracy / false-match rate | `null` | 不可声明 | final augment ID prediction 与真值的独立 join |
| OCR accuracy | `null` | 不可声明；现有 annotation 没有 benchmark 所需 `ocr_text` 真值 | 预先定义的 OCR 真值与产品 OCR raw output |
| icon accuracy | `null` | 不可声明；现有 annotation 没有独立 `icon_id` 真值字段 | 产品 icon result 与独立 icon 真值 |
| screen detection accuracy | `null` | 不可声明；只有 1 个 clear positive，5 个遮挡不是 negatives | held-out positive/negative/occluded 分层集 |
| average / P95 latency | `null` | 不可声明 | 明确定义计时边界的产品 `latency_ms` 流 |

不得用下列替代物填分母：人工 augment 标签、preview probe 的 hard-coded truth、synthetic detector 测试、文件数、detector score、OCR lexical confidence、手填 latency。

## 4. 2560x1600 ROI 与产品路径

### 4.1 采集时旧路径：有真实失败证据

n5 是清晰 original WGC 三卡帧，metadata 记录采集时 detector 选了最大负向纵移：

| ROI | LEFT | CENTER | RIGHT |
|---|---|---|---|
| card | `(384,272,512,880)` | `(1024,272,513,880)` | `(1664,272,512,880)` |
| title | `(414,316,452,212)` | `(1054,316,453,212)` | `(1694,316,452,212)` |
| icon | `(512,553,256,406)` | `(1152,553,257,406)` | `(1792,553,256,406)` |

相对旧 seed 的 `y=320`，card `y=272` 等于 `-48 px`，正好达到 `0.03 * 1600` 的搜索上限。即使 composite score 为 0.931，硬门仍以 `insufficient_luma` 判 invisible，后续 OCR/icon 没有运行。这是确定的旧产品 false negative，而不是未知猜测。

### 4.2 当前源码：几何已重标，但没有同版本执行证据

当前 `ProductDetectorConfig()` 仍写 Phase1 placeholder card triplet；`ComputeThreeCardRois()` 在 detector 边界遇到**完全相同**的 triplet 时，暗中替换成 `kPhase2MeasuredCardRegions`（`src/detector/roi.cpp:13-52,401-404`）。在 `2560x1600`、offset 0 下的当前理论 ROI 为：

| ROI | LEFT | CENTER | RIGHT |
|---|---|---|---|
| offer | `(256,160,2048,1280)` | 同一 offer | 同一 offer |
| card | `(506,284,476,785)` | `(1052,284,476,785)` | `(1598,284,476,785)` |
| title | `(566,631,356,52)` | `(1112,631,356,52)` | `(1658,631,356,52)` |
| icon | `(596,333,296,267)` | `(1142,333,296,267)` | `(1688,333,296,267)` |

title/icon 子 ROI 分别是 card-relative `(0.12605,0.4421,0.74790,0.06494)` 与 `(0.18908,0.0625,0.62185,0.339)`（`include/.../roi.h:48-54`）。它们与 current card geometry 一起明显比 n5 metadata 内的旧大框更贴近卡面；但这只是源码几何推导：没有当前源码 build 对 n5 的 detector/OCR/icon 输出，也没有第二张 held-out clear WGC。

另外，detector 的可见性评分只测 card 全框的 luma/edge/surround/border/三列一致性（`src/detector/augment_screen_detector.cpp:255-380`），不读取 title/icon 子 ROI。因此未来即使 `visible=true`，也不能据此宣称标题与 icon crop 正确。

### 4.3 preview-derived RAW 与当前产品 replay 不同构

debug preview 固定 client 为 `1280x720`，并用 `StretchDIBits` 把整个输入 frame 直接拉伸到 client（`src/output/debug_preview_window.cpp:216-240`），没有 16:10 letterbox 逻辑。`scripts/tmp/phase2_real_vision_probe.cpp:439-440` 也明确称 `y=720..799` 为 `external_black_not_game_pixels`。

因此旧 ingest report 所称“已知 2560x1600 → 精确统一 0.5x，底部 80 为 16:10 letterbox”（`outputs/phase2_real_ingest_report.md:19-20`）不成立：若输入真是 2560x1600，则 preview 的横纵缩放分别是 0.5 与 0.45；若输入其实是 1920x1080，则 `original_window_resolution=2560x1600` 字段有误。两种情形都不能把 `1280x800 RAW` 当原始 WGC 的统一 0.5x 代理。

当前 measured ROI 可精确复现 active `1280x720` 上的人工框：card `(253,128,238,353)`、title `(283,284,178,23)`、icon `(298,150,148,120)`。但产品直接 replay 数据集 `RAW.png` 时看到的是 `1280x800`，理论 LEFT 变为：

- card `(253,142,238,393)`；相对 annotation 为 `y +14 / h +40`；
- title `(283,315,178,27)`；相对 annotation 为 `y +31 / h +4`；
- icon `(298,166,148,134)`；相对 annotation 为 `y +16 / h +14`。

最大纵向搜索只有 24 px，且一个全局 offset 不能同时消除这些不同误差。临时 probe 先手工裁成 `1280x720` 再检测，绕过了产品对 dataset RAW 的真实输入形状；它的结果不能当产品 replay benchmark。

## 5. OCR 实际产品路径

真实产品链为：stable detector → card 的 `primary_ocr_rect`（即 title ROI）→ `ProductTitlePreprocessingOcr` → Windows.Media.Ocr → text matcher → icon fusion → 两帧 consensus（`src/vision/recognition_pipeline.cpp:147-218`、`src/app/augment_frame_processor.cpp:458-536`）。

当前产品只调用一次 `TitlePreprocessor::DefaultParameters()`：grayscale、无 contrast stretch、无 threshold、scale 1，即 `gray_1x`，backend 标记 `strategy=single_default`（`src/app/augment_frame_processor.cpp:46-84`；`include/.../title_preprocessor.h:18-22`）。库能枚举 18 个变体，但产品没有枚举或择优。Windows.Media.Ocr 不提供 confidence，代码保留 `ocr_confidence=null` 是准确披露，不应合成分数。

`scripts/tmp/phase2_real_vision_probe.cpp` 不是产品路径：它硬编码 3 个 preview truth、人工 title/icon ROI，先裁 active 1280x720，再在同 9 张卡上遍历 18 个 OCR 变体并计算 correct/all-correct/unknown/latency。若用这些结果选参数后仍在同 3 样本报告准确率，就是直接 tuning/evaluation leakage。当前 `outputs/tmp/phase2_real_vision_probe` 没有可用结果；不得补造。

## 6. 245 模板、icon 融合与 UNKNOWN

manifest 自身完整：245 templates、163 unique PNG、134 unique dHash、54 个全局 dHash 歧义组。按模式：

| Mode | templates | 歧义组 | 位于歧义组的 IDs | 最大组 |
|---|---:|---:|---:|---:|
| KIWI | 220 | 49 | 145 | 10 |
| KIWI_JADE | 188 | 37 | 112 | 7 |

当前 12 张人工真值卡中，7/12 的正确 ID 在 KIWI 下与其他 ID 共享**完全相同** dHash；9 个 distinct IDs 中 6 个有歧义：`ARAM_Eureka`、`Overloaded`、`ARAM_EndlessHunt`、`WarlockJuicebox`、`ARAM_Impassable`、`Equilibrium`。仅 n5 就有 2/3（`ARAM_Impassable` 为 4-way，`Equilibrium` 为 2-way）。

matcher 对 top1/top2 同距返回 `hash_top1_ambiguous`，不选择 ID；OCR/icon 冲突返回 `ocr_icon_conflict:*` 并清空 ID。这两个 fail-closed 决策本身正确（`src/vision/icon_matcher.cpp:203-217,221-256`；`src/app/augment_frame_processor.cpp:578-585`）。

但产品 consensus 存在 **P0 liveness bug**：

1. exact/normalized OCR + icon unknown 会得到 `ocr_only_icon_unknown`，并附加到 card reason；
2. `IsStrongLexicalEvidence()` 只要看到 `+ocr_`，就要求 `+ocr_icon_agree` 才保持 strong（`src/app/augment_frame_processor.cpp:140-149`）；
3. 两帧后要求三卡全部 strong，否则返回 `fuzzy_requires_strong_consensus`，不持久化（同文件 `:524-532`）。

结果是：**正确的 exact OCR 会因为 icon 的模板固有歧义/unknown 被降级并持续阻塞 offer。** 当前测试覆盖 hash tie、OCR-only-unavailable、fuzzy consensus，却没有覆盖“exact OCR + icon unknown/ambiguous 应否允许持久化”。真实 crop 噪声还可能打破模板 tie，产生错误唯一 icon，再与正确 OCR 冲突并强制 UNKNOWN；没有 real icon benchmark 能量化该风险。

## 7. Provenance、schema 与 benchmark 泄漏

### 正确披露

- 6 个新 bundle 的 `provenance=real`、`source.kind=windows_graphics_capture`、raw `2560x1600`、`coordinate_space=raw_frame_pixels` 与采集产物一致；n0-n4 的 `skipped/occluded` 与 n5 `complete` 分层清楚。
- 3 个 preview bundle 的自由字段明确写了 `capture_chain=windows_graphics_capture_debug_preview_gdigrab`、`derived_from_preview=true`、`data_boundary=derived_from_debug_preview_not_original_wgc_frame`、`ui_scale=null`；OCR 为 `not_run`。这部分文字披露是诚实的。
- `source.kind=manual_capture` 能描述其直接的 gdigrab/manual ingest，但不能表达“真实场景的 preview 二次采集”这一 benchmark 边界。

### 披露失效的位置

- dataset schema 允许 `real + manual_capture`，metadata 又是 `additionalProperties=true`；它没有 typed `preview_capture`、`derived_from_preview` 约束或 benchmark-use 字段（`data/dataset/augment_offers/schema.json:24-48,50-112`）。
- validator 不检查这些自由字段；eligibility 只由 `valid && annotated && provenance == real` 决定（`scripts/phase2/validate_dataset.py:1026-1032`）。所以 3 个 preview 被错误标成 benchmark eligible。
- benchmark schema 更严格地只保留 `provenance/resolution/ui_scale/ocr_preprocessing_variant`，完全没有 `source.kind`、preview boundary、capture chain 或 held-out split；计算时又只按 `provenance == real` 过滤（`scripts/benchmark_phase2.py:254-302,893-901`）。一旦有人写 adapter，preview 样本会自动泄漏进 real metrics。
- benchmark input 把 annotation 与 recognition_result 放在同一个 bundle，却没有 executable/model hash、run ID、原始 frame hash、prediction 生成时间、只读 prediction artifact 或独立 join 证明。把人工标签复制到 prediction 可通过 schema；schema 也只要求 `latency_ms >= 0`，没有定义 detector-only、OCR-only、per-card 或 end-to-end 的计时边界。
- canonical annotation 只有 augment ID/name/valid；benchmark 还要求 `screen_present`、每卡 `ocr_text`、`icon_id` 和 metadata 的非空 `ui_scale`/OCR variant。当前 `ui_scale=null`，这些字段不能靠 adapter 猜测或人工补造。
- benchmark 默认只需 1 个三卡 real sample 即可 `status=ok`；即便 producer 存在，这也不足以支持泛化准确率声明。

## 8. P0 / P1 缺陷清单

| ID | 级别 | 缺陷与真实影响 | 关闭条件 |
|---|---|---|---|
| VDA-01 | **P0** | preview-derived 被 `provenance=real` + validator 判 eligible；真实场景像素与 raw-WGC benchmark provenance 被混为一类 | schema 增加 typed capture boundary/split；validator 与 benchmark 明确排除 preview-derived |
| VDA-02 | **P0** | 0 recognition records，canonical dataset 与 benchmark schema 之间没有产品 prediction producer；三卡、UNKNOWN、accuracy、latency 全部不可计算 | 用固定产品 hash 批量 replay held-out direct WGC，独立输出 predictions/timing，再按 sample ID join |
| VDA-03 | **P0** | exact/normalized OCR 遇 icon unknown/ambiguous 会失去 strong evidence，三卡 consensus 永不持久化；KIWI 145/220 IDs 处于 exact-dHash 歧义组 | 改证据策略并增加 exact-OCR + ambiguous-icon 产品级测试；冲突仍保持 UNKNOWN |
| VDA-04 | **P0** | 唯一 clear original WGC 在采集时 detector false-negative、OCR not_run；当前 ROI/detector 修订发生在采集之后，且没有同版本 build/real replay 证据 | 当前源码 clean build；在 n5 与新增 held-out clear/negative/occluded WGC 上保存完整 detector/OCR/icon/final outputs |
| VDA-05 | **P1** | `1280x800 RAW` 含 80 px external black；产品按 16:10 计算 ROI，临时 probe 却手裁 1280x720，路径不同构 | preview 仅留 calibration bucket；若要 replay，显式记录并在产品路径实现 active-picture transform |
| VDA-06 | **P1** | ingest report 的统一 0.5x/letterbox 解释与 preview 绘制代码矛盾 | 修正报告/metadata；保存原始 WGC frame dimensions 与变换矩阵 |
| VDA-07 | **P1** | 当前 measured layout 由同一批 preview/n5 校准；若拿 4 个 complete 样本评测会数据泄漏，held-out direct WGC 为 0 | 冻结参数后采集独立测试集，按 capture session/time 分组切分 |
| VDA-08 | **P1** | 产品 OCR 固定 `gray_1x`，18-variant probe 绕过产品 ROI 且在同 3 样本调参；无 held-out 证据证明默认最佳 | 只在 train/calibration split 选 variant，在 held-out WGC 固定一次评测 |
| VDA-09 | **P1** | 245 ID 仅 134 dHash；real crop 噪声可导致 unknown、错误唯一匹配或 OCR conflict，且没有 icon accuracy 数据 | 更有判别力的视觉特征/多模板策略；held-out real icon benchmark 与 conflict matrix |
| VDA-10 | **P1** | `ProductDetectorConfig` 仍显示旧 card ROI，核心函数仅对精确 placeholder triplet 做隐式替换；配置、metadata 与实际几何难以追踪 | 显式版本化 calibration，随每个 prediction 输出 calibration/version/hash |
| VDA-11 | **P1** | persisted validator/benchmark outputs 已落后于 9-sample 数据集；工作区又无 git/version manifest | 每次评测原子写入 dataset hash、source hash、binary hash、run timestamp 与 immutable report |
| VDA-12 | **P1** | detector visible 只验证 card 外观，不验证 title/icon 子 ROI；不能以 detector pass 代替 OCR/icon crop QA | 单独保存/校验 child ROI IoU/coverage，并加入真实 crop 回归 |

## 9. 最终签收判定

当前只能声明：有 6 张 original WGC（1 clear complete、5 skipped occluded）、3 张明确披露的 preview-derived complete、12 张人工真值卡、245 个 icon templates；旧产品在唯一 clear original WGC 上 detector false-negative；当前 benchmark 分母为 0。

当前不能声明任何真实识别准确率、三卡全对率、UNKNOWN rate 或 latency。要解除阻断，必须先隔离 preview/calibration 数据、修复 exact-OCR + ambiguous-icon 的强证据门、用当前同一 binary 生成不可人工混入的 prediction 流，并在新采集的 held-out original WGC 上评测。

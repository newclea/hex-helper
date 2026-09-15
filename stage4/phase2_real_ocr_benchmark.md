# Phase2 n5 真实 OCR 参数 Benchmark

## 结论

- 产品最优参数结论：**UNKNOWN**。本报告只有 1 个真实独立 offer，不能把 3 张卡或 7 次计时重复伪装成多个独立 offer。
- n5 上观测排名第一：`fixed128_1x`；3 卡首轮 3/3，7 个测量轮次中全三卡正确 7/7。
- 可供下一步产品试用的精确候选：contrast_stretch=`false`，threshold_mode=`fixed`，fixed_threshold=`128`，scale=`1x`，缩放=`nearest-neighbor`；这只是 n5 最佳观测，不是已泛化的 产品定案。

## 18 变体事实表

OCR 单元格格式为 `Windows.Media.Ocr 原文 → 产品词库 ID(映射类型)`。3/3 使用每个配置的首个测量轮；稳定性列是 7 个轮次的三卡全对数。

| Rank | Variant | Left | Center | Right | 3/3 | 全对轮次 | E2E avg ms | E2E p95 ms |
|---:|---|---|---|---|---:|---:|---:|---:|
|1|`fixed128_1x`|不 动 如 山 → ARAM_Impassable(normalized)|》 我 们 的 治 疗 》 → Equilibrium(normalized)|星 界 躯 体 → ARAM_CelestialBody(normalized)|3/3|7/7|22.859|25.280|
|2|`contrast_fixed128_1x`|不 动 如 山 》 → ARAM_Impassable(normalized)|我 们 的 治 疗 → Equilibrium(normalized)|星 界 躯 体 → ARAM_CelestialBody(normalized)|3/3|7/7|23.902|29.955|
|3|`gray_1x`|不 动 如 山 → ARAM_Impassable(normalized)|》 我 们 的 治 疗 》 → Equilibrium(normalized)|星 界 躯 体 → ARAM_CelestialBody(normalized)|3/3|7/7|24.148|26.143|
|4|`gray_2x`|不 动 如 山 → ARAM_Impassable(normalized)|我 们 的 治 疗 → Equilibrium(normalized)|星 界 躯 体 ； → ARAM_CelestialBody(normalized)|3/3|7/7|24.619|26.634|
|5|`contrast_gray_1x`|不 动 如 山 → ARAM_Impassable(normalized)|》 我 们 的 治 疗 》 → Equilibrium(normalized)|星 界 躯 体 → ARAM_CelestialBody(normalized)|3/3|7/7|24.624|29.820|
|6|`contrast_otsu_2x`|不 动 如 山 → ARAM_Impassable(normalized)|我 们 的 治 疗 → Equilibrium(normalized)|「 星 界 躯 体 」 → ARAM_CelestialBody(normalized)|3/3|7/7|25.135|27.303|
|7|`contrast_otsu_1x`|不 动 如 山 》 → ARAM_Impassable(normalized)|我 们 的 治 疗 → Equilibrium(normalized)|星 界 躯 体 → ARAM_CelestialBody(normalized)|3/3|7/7|25.221|32.561|
|8|`gray_3x`|不 动 如 山 ； → ARAM_Impassable(normalized)|我 们 的 治 疗 → Equilibrium(normalized)|星 界 躯 体 → ARAM_CelestialBody(normalized)|3/3|7/7|25.382|26.262|
|9|`otsu_1x`|不 动 如 山 》 → ARAM_Impassable(normalized)|我 们 的 治 疗 → Equilibrium(normalized)|星 界 躯 体 → ARAM_CelestialBody(normalized)|3/3|7/7|25.413|31.859|
|10|`contrast_otsu_3x`|不 动 如 山 → ARAM_Impassable(normalized)|我 们 的 治 疗 → Equilibrium(normalized)|星 界 躯 体 → ARAM_CelestialBody(normalized)|3/3|7/7|26.589|32.904|
|11|`contrast_fixed128_3x`|不 动 如 山 → ARAM_Impassable(normalized)|我 们 的 治 疗 → Equilibrium(normalized)|星 界 躯 体 → ARAM_CelestialBody(normalized)|3/3|7/7|26.651|29.133|
|12|`contrast_gray_3x`|沐 动 如 山 ； → ARAM_Impassable(fuzzy)|我 们 的 治 疗 → Equilibrium(normalized)|星 界 躯 体 → ARAM_CelestialBody(normalized)|3/3|7/7|26.970|33.537|
|13|`otsu_3x`|不 动 如 山 → ARAM_Impassable(normalized)|我 们 的 治 疗 → Equilibrium(normalized)|星 界 躯 体 → ARAM_CelestialBody(normalized)|3/3|7/7|27.000|29.841|
|14|`fixed128_3x`|， 不 动 如 山 → ARAM_Impassable(normalized)|我 们 的 治 疗 → Equilibrium(normalized)|星 界 躯 体 → ARAM_CelestialBody(normalized)|3/3|7/7|27.636|31.436|
|15|`contrast_gray_2x`|不 动 如 山 ， → ARAM_Impassable(normalized)|我 们 的 治 疗 → Equilibrium(normalized)|星 界 躯 体 ； → ARAM_CelestialBody(normalized)|3/3|7/7|27.834|35.616|
|16|`otsu_2x`|不 动 如 山 → ARAM_Impassable(normalized)|我 们 的 治 疗 → Equilibrium(normalized)|「 星 界 躯 体 」 → ARAM_CelestialBody(normalized)|3/3|7/7|29.695|39.458|
|17|`fixed128_2x`|， 不 动 如 山 《 → ARAM_Impassable(normalized)|俄 们 的 治 疗 」 → Equilibrium(fuzzy)|丨 星 界 躯 体 」 → ARAM_CelestialBody(fuzzy)|3/3|7/7|32.122|39.506|
|18|`contrast_fixed128_2x`|不 动 如 → UNKNOWN(unknown)|「 我 们 的 治 疗 」 → Equilibrium(normalized)|丨 星 界 躯 体 」 → ARAM_CelestialBody(fuzzy)|2/3|0/7|27.197|30.610|

## Raw 对照（不计入 18 变体排名）

| Left | Center | Right | 3/3 | 全对轮次 | E2E avg ms | E2E p95 ms |
|---|---|---|---:|---:|---:|---:|
|不 动 如 山 → ARAM_Impassable(normalized)|》 我 们 的 治 疗 》 → Equilibrium(normalized)|星 界 躯 体 → ARAM_CelestialBody(normalized)|3/3|7/7|24.798|28.544|

## 人工标题 ROI（2560x1600 原始像素）

| Slot | 人工标签 | Glyph bbox | OCR ROI |
|---|---|---|---|
|left|不动如山 → `ARAM_Impassable`|`683,645,122,28`|`675,638,138,42`|
|center|我们的治疗 → `Equilibrium`|`1212,644,156,29`|`1204,638,172,42`|
|right|星界躯体 → `ARAM_CelestialBody`|`1774,644,124,29`|`1766,638,140,42`|

人工标签只参与评分，未作为 OCR 预测输入。ROI 是 n5 可见字形的人工 测量框，并留 8 px 水平、6–7 px 垂直暗背景边距。

## 方法与边界

- 样本：仅 `sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6`，1 帧、1 offer、3 卡；同一 offer 内卡片高度相关。
- 后端：`windows_media_ocr:zh-CN`（probe=`available`），该 API 不提供 confidence，因此 JSON 中保持 `null`。
- 词库：`data/knowledge/augments.zh-CN.json`，mode=`KIWI`，220 个候选；直接编译产品 `BoundedTextMatcher`，按 exact → normalized → 有界 fuzzy 映射。
- 预处理：直接编译产品 `TitlePreprocessor`，完整枚举 2×3×3=18 项；处理顺序为灰度、可选 min/max contrast、可选 fixed-128/Otsu、nearest-neighbor 1x/2x/3x。
- 时延：每卡 1 次 warm-up 后测 7 次，每配置 21 个调用；p95 为 nearest-rank。E2E 包含预处理、gray→BGRA、Windows.Media.Ocr 和词库匹配。重复只用于稳定性/时延，不增加独立准确率样本量。
- 逐卡 line candidates、normalized text、match score、实际 Otsu threshold 与 contrast black/white point 见配套 JSON。

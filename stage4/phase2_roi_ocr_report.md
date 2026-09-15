# Phase2 ROI 校准与标题 OCR 预处理报告

## 结论

- detector/ROI 现在支持 16:9 与 16:10 三卡布局比例家族。`2560x1600` 的定向断言证明它不会再仅因 `aspect_ratio` 被硬拒绝。
- 这不证明真实 LoL 的 offer/card/title/icon ROI 准确，也不证明 detector 在真实 16:10 负样本上的误报率。实现没有以合成图得出任何 OCR 最佳参数或准确率结论。
- 归一化布局只生成初始种子；detector 会在受限的二维局部偏移网格中逐候选测量，并以三列卡片几何、亮度、边缘和卡片/周边亮度对比共同门控。
- `RoiCalibration` 可输出确定性紧凑 JSON，字段为 `resolution`、nullable `ui_scale`、`offer`、`card_rect`、`title_rect`、`icon_rect`。矩形均为绝对像素，卡片数组顺序固定为 Left/Center/Right。
- 现有 OCR 调用无需修改 recognition pipeline：`ThreeCardRois::cards` 继续保存整卡坐标和 debug 语义，同时每个 card 携带 `primary_ocr_rect`；`CropBgraOwning` 对该 ROI 裁 title，`CropRawBgraOwning` 显式保留整卡裁剪。
- `TitlePreprocessor` 不依赖 OpenCV，提供可枚举、参数完整记录的灰度/对比度/阈值/缩放变体。默认仅为中性基线 `gray_1x`，不代表 benchmark 优胜项。

## ROI 与兼容适配

### 比例入口

- `IsSupportedThreeCardAspectRatio` 接受容差内的 16:9 或 16:10。
- Phase1 的 `IsSupported16By9` 名称保留以兼容现有调用，但语义扩展为受支持三卡比例家族，使未允许修改的下游 guard 不再拒绝 16:10。
- 新增 `IsExact16By9`，供确实需要严格 16:9 判定的调用者使用。
- 4:3、超宽屏和纵向异常比例仍返回 `unsupported_aspect_ratio`。

### 校准与子 ROI

- `NormalizedThreeCardLayout` 保留 Phase1 的 `offer_region`/`card_regions` 初始化方式，尾部新增相对 card 的 title/icon 种子，因此原有两字段 aggregate 初始化仍可编译。
- `ThreeCardRois` 保留 `offer_region`/`cards`，新增 `title_rects`、`icon_rects`、`resolution` 和 nullable `ui_scale`。
- `RoiCalibration::IsValid` 检查分辨率、UI scale 有限正值、所有 ROI 非空且位于帧内、card 位于 offer 内、title/icon 位于对应 card 内、三卡排序及非重叠。
- 归一化到像素仍使用左/上 `floor`、右/下 `ceil`；子 ROI 在取整后再次检查父子包含关系。
- `SerializeRoiCalibration` 对非法校准返回 `nullopt`，合法校准输出稳定字段顺序的 JSON；`ui_scale` 未知时输出 `null`。

### 局部搜索与门控

- `RoiSearchConfig` 控制水平/垂直最大偏移比例和步长比例；默认启用，候选生成和同分决策顺序固定。
- 搜索是全局三卡布局的局部 x/y 平移，不把默认归一化坐标当作最终校准结果。
- 配置限制每轴最多 16 个步长，阻止极小步长造成候选数量失控。
- 每个候选独立计算三列 `mean_luma`、`edge_density`、`surround_luma_contrast` 和几何/跨列一致性；任一硬门控失败都不会标记 visible。
- 合成 16:10 空白帧和全帧棋盘纹理均被拒绝；这只验证门控管线不会因比例放宽而无条件放行，不是实际 FPR 测量。

## TitlePreprocessor 变体

处理顺序固定为：BGRA 整数灰度化 -> 可选 min/max 线性对比度拉伸 -> 可选阈值 -> 可选最近邻放大。

枚举网格共 18 项：

- contrast：off/on；
- threshold：none、fixed-128、Otsu；
- scale：1x、2x、3x。

每个 `TitlePreprocessVariant` 记录：

- 稳定 variant id；
- 请求参数：contrast、threshold mode、fixed threshold、scale；
- 实际参数：contrast black/white point、resolved threshold；
- owning grayscale 输出及尺寸/stride。

Otsu 使用 256-bin histogram 和固定的最低阈值 tie-break；灰度、对比度、阈值和缩放均为整数或固定顺序算法。相同输入重复枚举得到逐字节相同结果。

## 验证证据

验证日期：2026-08-25，Windows x64，MSVC 19.44，C++20，`/W4 /WX /permissive-`。

1. 新建独立 build tree：`outputs/tmp/phase2_roi_ocr_build`
   - CMake configure：exit 0
   - Release 全量 build：exit 0
2. 全量 CTest：20/20 passed，0 failed
   - 包含 detector、title preprocessor、recognition pipeline、session runtime、augment frame processor 及其余回归测试。
3. detector 定向测试：163 checks，0 failures
   - 覆盖 1920x1080、2560x1440、2560x1600；
   - 覆盖 4:3、超宽、纵向异常比例；
   - 覆盖 floor/ceil、父子 ROI、偏移越界、nullable UI scale、JSON 确定性；
   - 覆盖 title-only primary crop 与 raw-card crop；
   - 覆盖局部偏移搜索和 16:10 空白/任意纹理门控。
4. TitlePreprocessor 定向测试：95 checks，0 failures
   - 除 CMake 回归外，还直接用 MSVC 编译 `title_preprocessor_test.cpp + title_preprocessor.cpp + roi.cpp + contracts.cpp`，exit 0；
   - 覆盖 18 项枚举完整性、参数记录、灰度、contrast、fixed/Otsu、2x/3x、组合、非法输入及重复运行逐字节确定性。

## 已知边界

- 没有启动、操纵或读取 LoL 进程，也没有输入自动化、注入、Hook、进程内存或逆向行为。
- 本阶段没有真实标注 corpus，因此不能选择“最佳”预处理参数，不能给 ROI 精度、OCR accuracy、FPR/FNR 或 confidence calibration 结论。
- 局部搜索当前只做整体布局 x/y 平移；UI scale 字段可记录已知值，但尚未基于真实标注执行独立缩放搜索。
- 工作目录没有 `.git` 元数据，无法用 `git diff` 做变更归属审计。执行期间 app/icon/CMake 出现并发外部修改；本任务未编辑这些禁止目录，也未覆盖或回滚外部改动。

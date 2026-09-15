# Phase2 真实画面 Detector + ROI 修复报告

## 结论

- 已修复原始 WGC clear 帧 `sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6/RAW.png` 的核心失败：detector 直调从旧记录 `visible=false / confidence=0.931193 / insufficient_luma` 变为 `visible=true / confidence=0.993798 / visible`。
- 没有降低 `minimum_card_luma=0.16` 或 `minimum_edge_density=0.025`。修复先纠正 card 几何，使 clear 帧三列实测 luma 自然升至 `0.207859 / 0.220733 / 0.215466`；再加入四边亮框覆盖与亮框/内部对比硬门控。
- 五张弹窗遮挡原始 WGC 均为 `visible=false / insufficient_bright_card_border`。这是明确的 detector-level UNKNOWN 预期：弹窗破坏至少一张卡的一侧亮框，不进入稳定确认。
- 三张 preview-derived 辅助正例在裁出 metadata 指定的 `1280x720` active picture 后均 `visible=true`，搜索偏移均为 `(0,0)`。
- 10 张既有正常游戏负例全部 `visible=false`，未出现新增误报。
- 本轮只验证本任务明确给出的 2560x1600 原始 WGC 和 1280x720 preview active picture，不声称其他分辨率或 UI scale 稳定。

## 精确 ROI

矩形使用左上原点、半开 `xywh`。2560x1600 clear WGC 的最终 detector ROI 为：

| Slot | card | title | icon |
|---|---:|---:|---:|
| LEFT | `(506,284,476,785)` | `(566,631,356,52)` | `(596,333,296,267)` |
| CENTER | `(1052,284,476,785)` | `(1112,631,356,52)` | `(1142,333,296,267)` |
| RIGHT | `(1598,284,476,785)` | `(1658,631,356,52)` | `(1688,333,296,267)` |

粗 offer 包络为 `(256,160,2048,1280)`。card 的 frame-normalized 值为：

```text
LEFT   = (0.19765625, 0.17777777777777778, 0.1859375, 0.49027777777777776)
CENTER = (0.4109375,  0.17777777777777778, 0.1859375, 0.49027777777777776)
RIGHT  = (0.62421875, 0.17777777777777778, 0.1859375, 0.49027777777777776)
```

card 内部子 ROI 为：

```text
title = (0.12605042016806722, 0.4421, 0.7478991596638656, 0.06494)
icon  = (0.18907563025210084, 0.0625, 0.6218487394957983, 0.339)
```

这些值同时复现三份已有 preview annotation 的 1280x720 card/title/icon：`(253,128,238,353)/(283,284,178,23)/(298,150,148,120)`，各列 x 间隔固定为 273 px。

### IoU

IoU 的参照是上述实测校准矩形，不是 OCR 结果或人工 augment 标签。

| ROI | 旧 ROI IoU（L/C/R） | 最终 detector IoU（L/C/R） |
|---|---|---|
| card | `0.590943 / 0.827707 / 0.640663` | `1.0 / 1.0 / 1.0` |
| title | `0 / 0 / 0` | `1.0 / 1.0 / 1.0` |
| icon | `0.046225 / 0.070516 / 0.051879` | `1.0 / 1.0 / 1.0` |

旧 title ROI 位于卡片上部，和真实标题带完全不相交；旧 icon ROI 过低且过窄。这也是旧 card crop 含大量非卡片背景、最终触发 `insufficient_luma` 的几何根因。

## Detector 证据与门控

真实尺寸路径保留并组合以下证据：

- 三列固定几何、等尺寸、等间隔；
- 每卡 luma 下限 `0.16`，未下调；
- 每卡 edge density 下限 `0.025`，未下调；
- 在 top/bottom/left/right 四个 frame band 分别测量 `luma >= 110` 的覆盖率，以最弱一侧作为每卡 `bright_border_density`，每张卡必须 `>=0.55`；
- 测量四边 band 平均 luma 与卡片内部平均 luma 的差，每张卡 `border_luma_contrast >=0.28`；
- 三列一致性由布局、luma、edge、三卡亮框存在和三卡边框对比共同加权；单卡 hover 允许更亮，但不能缺边。

clear WGC 的关键证据：

```text
bright_border_density = 0.640681 / 0.655663 / 0.639411
border_luma_contrast  = 0.448048 / 0.428803 / 0.430991
edge_density          = 0.058905 / 0.077088 / 0.059929
three_column          = 0.975192
```

弹窗帧的最弱侧亮框覆盖只有约 `0.192..0.286`，明显低于 clear 帧的 `0.639..0.656`，因此不是靠 confidence 或宽松阈值放行。真实/preview 尺寸上，旧 `surround_luma_contrast` 继续作为诊断和弱分数，但不再是硬门：它采样的是实时地图像素，会被地图和 tooltip 改变。小尺寸合成 plumbing 继续走旧 surround gate，避免把真实亮框模型伪装成低分辨率准确性结论。

## 样本结果

### 6 张原始 WGC

| sample | 状态 | confidence | reason | 预期 |
|---|---:|---:|---|---|
| `...n0_0309c4711657f988` | false | `0.801684` | `insufficient_bright_card_border` | 弹窗遮挡，UNKNOWN |
| `...n1_77c5f72fbff68bea` | false | `0.790935` | `insufficient_bright_card_border` | 弹窗遮挡，UNKNOWN |
| `...n2_7d20cfaed875701d` | false | `0.797717` | `insufficient_bright_card_border` | 弹窗遮挡，UNKNOWN |
| `...n3_47d9c66595f20b78` | false | `0.797792` | `insufficient_bright_card_border` | 弹窗遮挡，UNKNOWN |
| `...n4_d56184b2891fa1ac` | false | `0.798586` | `insufficient_bright_card_border` | 弹窗遮挡，UNKNOWN |
| `...n5_4d4a1700e84b73c6` | **true** | **`0.993798`** | **`visible`** | clear 正例 |

### 3 张 preview-derived 正例

| sample | visible | confidence | offset |
|---|---:|---:|---:|
| `real-preview-1787670257978-f010` | true | `0.983684` | `(0,0)` |
| `real-preview-1787670257978-f020` | true | `0.975690` | `(0,0)` |
| `real-preview-1787670572085-f0025` | true | `0.947839` | `(0,0)` |

这些图像只作为 preview-derived 辅助正例；没有把它们描述为原始 WGC。

### 正常负例

使用已有 `continuous_1787670572085` 的 `frame_0015/0060/0085/0090/0095/0105/0110/0115/0120/0130.png`，机械裁出 `1280x720` active picture。结果为 `0/10 visible`；confidence 范围 `0.397479..0.561818`，均由缺失三列四边亮框拒绝。

## 测试

构建使用既有 `outputs/tmp/build_phase2_main_verify`，未修改 CMake 或 app。

```text
cmake --build ... --target detector_test augment_frame_processor_test recognition_pipeline_test
build: PASS, MSVC /W4 /WX

ctest -R ^(detector_test|augment_frame_processor_test|recognition_pipeline_test)$
3/3 passed

detector_test
232 checks, 0 failures
```

`detector_test` 现在实际 WIC 解码全部 6 张原始 WGC、3 张 preview-derived 正例和 10 张正常负例，并断言 clear ROI IoU、弹窗拒绝以及负例不 visible。完整直调输出：`outputs/tmp/phase2_real_detector_roi/detector_test.log`。

## 变更范围与交接

修改仅位于：

- `include/lol_assistant/detector/roi.h`
- `include/lol_assistant/detector/augment_screen_detector.h`
- `src/detector/roi.cpp`
- `src/detector/augment_screen_detector.cpp`
- `tests/detector/detector_test.cpp`
- 本报告与 `outputs/tmp/phase2_real_detector_roi/**`

没有修改 app、vision、collector、capture、CMake，没有进程内存读取或输入控制。

正式 `lol_augment_assistant.exe` 尚未由本 detector 任务重编，因此没有用旧 exe 伪称最终 replay 结果。主线程重编 app 后应对 clear RAW 执行一次正式 replay；本报告中的结果来自与最终 detector 源码同次构建的 `detector_test.exe` 直调路径。

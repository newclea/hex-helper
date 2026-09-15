# Phase2 真实 icon ROI 最小修复报告

## 结论

- 2560x1600 clear WGC 的 detector icon ROI 已从宽框收紧为三块实测正方形：
  - LEFT `(625,342,240,240)`
  - CENTER `(1171,342,240,240)`
  - RIGHT `(1717,342,240,240)`
- 真实 clear WGC 回归逐槽打印以上实际矩形，三槽 icon IoU 均为 `1.0`。
- card 与 title ROI 保持不变；detector visible gating、matcher 阈值、应用和 collector 均未修改。
- 本修复不把候选召回描述为 accepted 准确率提升：生产 matcher 仍为 `0/3 accepted`、`3/3 UNKNOWN`。已知的 truth-in-top2 `3/3` 仅是诊断结果。

## 精确几何

矩形均为左上原点、半开 `xywh`。2560x1600 measured layout 为：

| Slot | card（不变） | title（不变） | icon（本次修复） |
|---|---:|---:|---:|
| LEFT | `(506,284,476,785)` | `(566,631,356,52)` | `(625,342,240,240)` |
| CENTER | `(1052,284,476,785)` | `(1112,631,356,52)` | `(1171,342,240,240)` |
| RIGHT | `(1598,284,476,785)` | `(1658,631,356,52)` | `(1717,342,240,240)` |

精确 frame-normalized icon 矩形：

```text
LEFT   = (0.244140625, 0.213750000, 0.093750000, 0.150000000)
CENTER = (0.457421875, 0.213750000, 0.093750000, 0.150000000)
RIGHT  = (0.670703125, 0.213750000, 0.093750000, 0.150000000)
pitch  = 0.213281250
```

三张 card 尺寸同为 `476x785`，icon 相对每张 card 的局部像素矩形同为 `(119,58,240,240)`。代码采用的精确 card-local normalized 值为：

```text
x      = 119 / 476 = 0.25
y      =  58 / 785 = 0.07388535031847134
width  = 240 / 476 = 0.5042016806722689
height = 240 / 785 = 0.3057324840764331
```

沿用既有两级推导：先由 frame-normalized card 得到像素 card，再以以上 card-local normalized icon 和 `floor(left/top) + ceil(right/bottom)` 得到像素 icon。因此 2560x1600 精确复现实测方框，其他支持分辨率按 normalized card/frame 几何推导，没有新增分辨率特判。

## 真实回归输出

clear WGC 样本：`sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6/RAW.png`。

```text
REAL_DETECT ... visible=1 confidence=0.993798 reason=visible offset=0,0
REAL_ROI slot=0 card_iou=1 title_iou=1 icon_rect=625,342,240,240 icon_iou=1
REAL_ROI slot=1 card_iou=1 title_iou=1 icon_rect=1171,342,240,240 icon_iou=1
REAL_ROI slot=2 card_iou=1 title_iou=1 icon_rect=1717,342,240,240 icon_iou=1
detector checks=245 failures=0
```

该结果也证明本次 icon 子 ROI 修复没有改变 clear WGC 的 detector visible 判定。

## 构建与测试

执行：

```powershell
cmake --build outputs/tmp/build_phase2_main_verify --config Debug --target detector_test
ctest --test-dir outputs/tmp/build_phase2_main_verify -C Debug -R detector --output-on-failure
outputs/tmp/build_phase2_main_verify/bin/detector_test.exe
```

结果：

- MSVC Debug 构建通过，现有 `/W4 /WX` 配置下无编译失败。
- detector 测试 `1/1 passed`，`0 tests failed`。
- `detector_test`：`245 checks`、`0 failures`。
- `ThreeCardRois::IsValid()` 和 card/title/icon frame bounds/containment 在支持分辨率回归中全部通过。
- 2560x1600 的 card、title、icon 均增加精确像素相等断言；真实 clear WGC 又独立断言三块实际 icon rect 与 IoU。
- 1280x720 preview-derived 回归按新 card-local normalized 几何得到首槽 `(312,154,121,109)` 并通过；这是缩放推导结果，不冒充原始 WGC 标注。

## 变更范围

仅修改：

- `include/lol_assistant/detector/roi.h`
- `src/detector/roi.cpp`
- `tests/detector/detector_test.cpp`
- `outputs/phase2_icon_roi_fix_report.md`

未修改 matcher 阈值、detector visible gating、app、collector 或其他产品路径。

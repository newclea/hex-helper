# Phase2 OCR/Icon 融合存活性修复报告

日期：2026-08-26

## 结论

P0 活性问题已修复。exact/normalized OCR 在 icon unavailable、unknown 或 ambiguous 时，保留 OCR 身份并可通过两帧共识进入持久化；OCR-only 结果会记录具体 icon 诊断并降低 `final_confidence`。明确唯一 icon 与 OCR 冲突仍 fail-closed，fuzzy+unknown 与 icon-only 均不能成为强共识证据。

## 实现

- `IsStrongLexicalEvidence` 只依据已识别的 exact/normalized OCR 词法来源，不再要求 `+ocr_icon_agree`。冲突结果在进入该判定前已转为 `Unknown` 并清空 ID。
- 即使没有 icon 模板，也执行融合诊断，reason 形如：
  - `exact_match+ocr_only_icon_unavailable:template_unavailable`
  - `exact_match+ocr_only_icon_unknown:hash_top1_ambiguous`
  - `normalized_match+ocr_only_icon_unknown:hash_distance_above_threshold`
- OCR-only `final_confidence` 按 `0.90` 缩放。对原本已达到配置持久化门槛的 exact/normalized，降级不会穿透该门槛；这保证 Windows Media OCR 无 backend confidence 时，0.90/0.86 的既有启发式仍有两帧持久化活性。
- OCR/icon 唯一冲突保持原安全边界：状态改为 `Unknown`、清空 augment ID/display name、置信置零。
- 未修改 icon matcher、main、CLI、detector、collector 或 dataset。

## 新增产品级两帧覆盖

| 场景 | 两帧结果 | 安全/诊断断言 |
|---|---|---|
| exact + ambiguous icon | 持久化 | OCR-only reason；无 backend confidence 时 0.90 降至门槛 0.85 |
| normalized + unknown icon | 持久化 | OCR-only reason；无 backend confidence 时 0.86 降至门槛 0.85 |
| fuzzy + unknown icon | 阻断 | `fuzzy_requires_strong_consensus`；持久化调用为 0 |
| exact + agreeing icon | 持久化 | `exact_match+ocr_icon_agree`；不降置信 |
| exact + conflicting icon | 两次 OCR 扫描均阻断 | `ocr_icon_conflict:*`；ID 清空、置信为 0、持久化调用为 0 |

## 验证

构建目录：`outputs/tmp/phase2_fusion_liveness`

- Release 全量构建：通过（首次 `scripts/build.ps1 -Configuration Release`）。
- 最终 Release 增量目标：`augment_frame_processor_test`、`icon_matcher_test`、`recognition_pipeline_test`、`lol_augment_assistant`，全部通过编译/link。
- 最终相关测试：3/3 通过，0 失败。
  - `augment_frame_processor_test`
  - `icon_matcher_test`
  - `recognition_pipeline_test`

## 变更文件

- `src/app/augment_frame_processor.cpp`
- `src/app/augment_frame_processor.h`
- `tests/integration/augment_frame_processor_test.cpp`
- `outputs/phase2_fusion_liveness_fix_report.md`


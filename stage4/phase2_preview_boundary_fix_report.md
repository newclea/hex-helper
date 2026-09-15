# Phase 2 preview-derived 数据隔离修复报告

日期：2026-08-26  
结论：P0 污染路径已关闭。3 个 debug-preview → gdigrab 样本继续保留为 `provenance=real` 的真实场景校准素材，但 validator 和独立 benchmark 均不再将其计入 original-WGC real metrics。

## 根因与修复边界

修复前，validator 的 `benchmark_eligible` 和 benchmark 的选样条件只检查 `provenance=real`。`derived_from_preview=true`、`source.kind=manual_capture` 等披露字段不参与资格判断，因此 preview-derived 样本污染 original-WGC 指标。

修复后，metadata 增加可选 typed 字段：

- `capture_boundary`: `original_wgc | preview_derived | other_real_capture | synthetic | unknown`
- `benchmark_use`: `original_wgc_metrics | real_scenario_calibration | excluded`
- `derived_from_preview`: boolean
- `source.kind` 增加 `debug_preview`

任一强信号 `derived_from_preview=true`、`source.kind=manual_capture`、`source.kind=debug_preview` 或 `capture_boundary=preview_derived` 都具有最高优先级，effective 结果固定为：

```text
provenance=real
capture_boundary=preview_derived
benchmark_use=real_scenario_calibration
benchmark_eligible=false
```

只有 clear direct `windows_graphics_capture`、非 preview-derived、effective `capture_boundary=original_wgc`、`benchmark_use=original_wgc_metrics` 且标注完整有效的 real 样本可进入指标。Validator 与 benchmark 分别执行该判定，避免单点绕过。

## Legacy 兼容与 warning

Schema v1 保持不变，新字段可选。Validator 会从既有 `source.kind` 与 preview 信号保守推断缺失字段，并产生 `legacy_benchmark_boundary_inferred` warning；preview-derived real 样本另有 `preview_derived_excluded_from_original_wgc_benchmark` warning。

独立 benchmark 继续接受完全缺少边界字段的旧 bundle/JSONL，并以 `legacy_benchmark_boundary_assumed_original_wgc` 明确告警以维持既有输入兼容。一旦输入出现任一边界信号，就应用严格隔离；`provenance=real` 或冲突的 `benchmark_use=original_wgc_metrics` 均不能覆盖 preview 排除。

## 真实 9 样本核验

执行：

```powershell
python -B scripts/phase2/validate_dataset.py `
  --dataset-root data/dataset/augment_offers `
  --knowledge data/knowledge/augments.zh-CN.json
```

结果：

| 项目 | 数量 |
|---|---:|
| `valid` | `true` |
| 总样本 | 9 |
| original-WGC boundary | 6 |
| original-WGC benchmark eligible | 1 |
| preview-derived calibration-only | 3 |
| skipped/excluded | 5 |
| synthetic / unknown | 0 / 0 |

3 个 preview-derived 样本均为 `valid=true`、`annotated=true`、`provenance=real`、`benchmark_eligible=false`。5 个 skipped 样本均保持合法且不进入 benchmark。未修改、删除或重分类任何真实样本内容。

## 测试证据

```text
python -B -m unittest discover -s tests/phase2_python -p '*_test.py' -v
Ran 15 tests — OK

python -B -m unittest discover -s tests/phase2_benchmark -p 'test_*.py' -v
Ran 9 tests — OK

tests/phase2_contract/run_contract.ps1
Ran 2 tests — OK
```

回归覆盖包括：四类 preview 强信号逐一强制排除、冲突的 original-WGC 声明不能绕过、clear direct WGC 可进入指标、legacy metadata 兼容并告警、benchmark fixture 加入“全正确 preview”后总指标和分母保持不变。

## 修改范围

仅修改用户指定文件：

- `data/dataset/augment_offers/schema.json`
- `README.md`
- `scripts/phase2/validate_dataset.py`
- `scripts/benchmark_phase2.py`
- `tests/phase2_python/dataset_tools_test.py`
- `tests/phase2_benchmark/test_benchmark_phase2.py`
- `outputs/phase2_preview_boundary_fix_report.md`

未修改 producer 新脚本、app、detector、vision 或 dataset 样本内容。

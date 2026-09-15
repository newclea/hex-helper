# Phase2 三卡人工标注数据集

本目录只保存可审计的三卡 offer 样本。真实准确率只允许使用 `real/` 下、通过
`scripts/phase2/validate_dataset.py` 严格校验且人工标注完整的样本计算。工具不会从
stdout、文件名、OCR 结果或知识库自动推断真值。

## 目录约定

```text
augment_offers/
├── schema.json
├── real/
│   └── <sample-id>/
│       ├── RAW.png
│       ├── LEFT_CARD.png
│       ├── CENTER_CARD.png
│       ├── RIGHT_CARD.png
│       ├── metadata.json
│       └── annotation.json
├── synthetic/
│   └── <sample-id>/
│       └── ...同上
└── unknown/
    └── <sample-id>/
        └── ...同上
```

- `<sample-id>` 只能包含 ASCII 字母、数字、点、下划线和连字符，长度为 1–128；
  `.` 与 `..` 禁止使用。
- `real/`、`synthetic/` 与 `unknown/` 的直接子目录才是样本。符号链接和逃逸
  dataset root 的路径会被拒绝。空的 `unknown/` 可暂时不存在，Collector 在首次写入时创建。
- `RAW.png` 是完整画面；`LEFT_CARD.png`、`CENTER_CARD.png`、`RIGHT_CARD.png`
  是恰好三张卡图。
  不允许用文件名表达标签。
- `metadata.json` 记录样本 ID、来源类型和可追溯引用。`provenance` 必须和父目录一致。
- `annotation.json` 由人工标注工具原子写入。`valid=false` 表示该卡图本身不可用于
  识别评估，此时 `augment_id` 与 `augment_name` 必须为 `null`。
- `schema.json` 同时描述 metadata 与 annotation 的 JSON Schema。验证器还执行图片、
  知识库、重复样本和路径边界检查，这些跨文件约束不能只靠 JSON Schema 表达。

最小 `metadata.json` 示例：

```json
{
  "schema_version": 1,
  "sample_id": "20260825-001",
  "provenance": "real",
  "source": {
    "kind": "phase1_capture",
    "reference": "phase1-session-id/sidecar.json"
  }
}
```

Collector 生成的 metadata 同时保留自身详细字段（`schema`/`version`、window、
resolution、ROI、detector、OCR、dedup 和 files），并提供 Dataset 必需字段：
`schema_version=1`、`sample_id`、`provenance`、`source.kind/reference`、
`captured_at_utc`。来源与分桶必须满足：

- WGC/Desktop Duplication → `real`；
- Replay → `synthetic` 或 `unknown`；
- 不确定来源 → `unknown`，且永不进入真实 benchmark。

Collector 的 `dataset_root` 可直接配置为 `augment_offers/real`、
`augment_offers/synthetic` 或 `augment_offers/unknown`。当 root 末级目录是这三个保留名
之一时，Collector 会拒绝与请求 `provenance` 不一致的写入；样本仍通过同目录 staging
后 rename 原子提交。

完整 `annotation.json` 示例：

```json
{
  "schema_version": 1,
  "sample_id": "20260825-001",
  "status": "complete",
  "cards": {
    "left": {
      "augment_id": "TechnicalAugmentId",
      "augment_name": "中文强化符文名",
      "valid": true
    },
    "center": {
      "augment_id": null,
      "augment_name": null,
      "valid": false
    },
    "right": {
      "augment_id": "AnotherTechnicalId",
      "augment_name": "另一个中文名",
      "valid": true
    }
  },
  "skip_reason": null,
  "updated_at_utc": "2026-08-25T12:00:00Z"
}
```

## 标注命令

所有命令默认使用本目录和 `data/knowledge/augments.zh-CN.json`。图片查看器默认不启动；
只有显式传入 `--open` 才会调用系统默认图片查看器。

```powershell
python scripts/phase2/annotate_dataset.py list
python scripts/phase2/annotate_dataset.py show 20260825-001
python scripts/phase2/annotate_dataset.py interactive 20260825-001
python scripts/phase2/annotate_dataset.py interactive 20260825-001 --open
python scripts/phase2/annotate_dataset.py set 20260825-001 left --valid true --augment-id TechnicalAugmentId --augment-name 中文强化符文名
python scripts/phase2/annotate_dataset.py set 20260825-001 center --valid false
python scripts/phase2/annotate_dataset.py clear 20260825-001 --side left
python scripts/phase2/annotate_dataset.py clear 20260825-001
python scripts/phase2/annotate_dataset.py skip 20260825-001 --reason "画面遮挡，待复核"
```

`list` 只列出没有完整标签的样本；加 `--all` 可列出全部。交互模式按
left → center → right 顺序逐卡输入，每张卡完成后立即原子落盘。`s` 跳过当前卡，
`c` 清除当前卡，`q` 将整个样本标为 skipped。

## 严格验证

```powershell
python scripts/phase2/validate_dataset.py
python scripts/phase2/validate_dataset.py --dataset-root data/dataset/augment_offers --knowledge data/knowledge/augments.zh-CN.json
```

验证器只向 stdout 输出一个严格 JSON 对象：通过时退出码为 0，存在 schema、文件完整性
或未完成标签等数据问题时退出码为 1。明确人工标记为 `status=skipped` 且具有非空
`skip_reason` 的 annotation 是合法排除项，只产生 `annotation_skipped` warning，不改变退出码；
缺少 `skip_reason` 或 annotation 本身损坏仍然验证失败。报告包括 real/synthetic/unknown 数量、
标签状态、三卡数量、图片分辨率分布、未知 ID、重复 sample ID、重复 sample 内容哈希和
benchmark eligibility。只有 `provenance=real`、`status=complete` 且样本自身完整有效时
`benchmark_eligible=true`；skipped、synthetic 和 unknown 始终排除。顶层
`skipped_excluded_sample_count` 单独统计合法且因 skipped 被排除的 real 样本；兼容字段
`benchmark_excluded_counts.invalid_or_unannotated_real` 保持原有宽口径。空数据集不会虚构样本，
必须报告 `real_sample_count: 0`。

## 一次迁移兼容

新写入只允许 canonical 文件名。验证器和标注器仍可识别完整的旧三件套
`LEFT.png`/`CENTER.png`/`RIGHT.png`。验证器会报告
`deprecated_artifact_names` warning（不改变退出码）；标注器的 `list --json` 和
`show --json` 会暴露 `artifact_naming=legacy_deprecated` 和同名 warning。工具不会自动重命名、
删除或覆盖旧文件。

同一样本只要同时出现任一 canonical 与任一旧卡图，就会报告
`mixed_artifact_names` 并验证失败；旧三件套或 canonical 三件套不完整也会失败。因此
迁移必须一次性重命名三张卡图，不能静默混用。真实样本目录不会由这些工具自动迁移。

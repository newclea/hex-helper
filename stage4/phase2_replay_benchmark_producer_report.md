# Phase2 Dataset → Replay → Benchmark Producer Report

## 结论

已新增单一入口 `scripts/phase2/run_dataset_replay.py`，完成可机读的
`sample_id` join：合法、非 skipped 样本的 `RAW.png` 经指定产品 executable
逐样本 Replay，严格解析 stdout JSONL，并生成独立 prediction 与未修改
`scripts/benchmark_phase2.py` 可直接读取的三流输入。

本次没有运行真实产品 executable，也没有向真实 dataset 写入 prediction。
收口检查确认 `data/dataset/augment_offers/**/recognition.json` 数量为 0；真实运行留给
主线程 detector 修复完成后执行。

## 入口与行为

入口固定使用以下产品参数：

- `--replay <sample_dir/RAW.png>`，路径为绝对路径；
- `--mode KIWI`；
- `--knowledge <absolute path>`；
- `--workspace <per-sample temporary absolute directory>`；
- `--once` 与受 producer 外部 watchdog 约束的 `--max-seconds`；
- 不传 `--preview`，并从 `session_start` 反向验证 `preview_requested=false`、mode
  和 knowledge/workspace 路径。

样本选择以现有 dataset validator 为准：所有合法、`status=complete` 的 real/synthetic
样本都会 Replay；skipped、in-progress、损坏或 unknown provenance 不会 Replay。
preview-derived real 样本可以生成校准 prediction，但不会进入 benchmark join；真实指标
只接收 validator 判定为 original-WGC benchmark eligible 的样本。synthetic 记录可进入三流，
仍由现有 benchmark 核心明确排除。

## Replay JSONL 与 prediction

parser 使用严格 UTF-8 JSONL：拒绝重复 object key、NaN/Infinity、非 object 行和未知事件。
每次 Replay 必须有一个 `session_start`、至少一个 `frame_result`、至多一个 accepted
`offer`、一个 `session_end`；accepted 状态、offer、stable detector 和 session close 状态
必须一致。兼容当前产品无 `type`、以 `current_offer` 表达 accepted offer 的 JSON 行，也支持
带 `type=offer` 和 OCR/icon/final 分阶段字段的 typed offer。

每个 `recognition.json` 使用 `phase2_replay_prediction_v1`，记录：

- raw/stable detector 的 visible、confidence、reason；
- LEFT/CENTER/RIGHT 的 OCR、icon、final 三阶段及明确状态；
- final UNKNOWN、detector false、协议/进程失败；
- Replay 报告 latency（若有），否则使用 producer 实测端到端 wall latency，并标注来源；
- benchmark 所需的 `screen_detected`、`latency_ms` 和三卡 recognition result。

annotation 不会传入进程执行或 prediction parser。它只在 prediction 已生成后转换为 benchmark
ground-truth stream。当前产品 legacy offer 若没有独立 OCR/icon 输出，producer 会记录
`UNAVAILABLE`/`null`，不会用 final、knowledge 或 annotation 回填。

timeout、无法启动、非零退出、非 UTF-8、malformed JSONL、缺失事件、session close/status
失败均会生成显式 failure prediction 和三张 UNKNOWN benchmark card；批处理继续处理后续样本。
存在这种 operational failure 时最终退出码为 1；输入/配置错误退出码为 2。

## 输出模式

默认模式使用同目录临时文件、flush/fsync、`os.replace` 原子写入
`sample_dir/recognition.json`。`--dry-run` 不做持久写入；`--output-dir` 将所有产物写到
dataset 外，推荐主线程采用此模式：

```text
<output-dir>/
├── real/<sample-id>/recognition.json
├── synthetic/<sample-id>/recognition.json
├── benchmark_input/
│   ├── metadata.jsonl
│   ├── annotations.jsonl
│   └── recognition_results.jsonl
├── producer_results.jsonl
├── producer_report.json
├── benchmark_report.json
└── benchmark_report.md
```

三条 benchmark stream 使用完全相同的 sample-id 集合；producer 随后通过现有 benchmark
loader 重新读取这个目录并生成报告，因此 join 错位会在本次命令内失败，而不是留到人工发现。

## 测试

fake executable fixtures 覆盖 success、三卡 UNKNOWN、detector false、process timeout 和
duplicate-key malformed JSONL。测试还覆盖 skipped/in-progress 过滤、preview-derived
prediction 与 original-WGC 指标隔离、默认原位原子写及重复运行、`--dry-run` 零持久写、
`--output-dir` 不修改 dataset，以及把 producer 三流再次交给未修改 benchmark CLI 的真实 join。

最终执行结果：

```text
python -B -m unittest discover -s tests/phase2_replay_benchmark -p 'test_*.py' -v
Ran 6 tests in 4.232s
OK

python -B -m unittest discover -s tests/phase2_benchmark -p 'test_*.py' -v
Ran 9 tests in 0.192s
OK
```

## 主线程真实运行命令

detector 修复、产品 executable 重建后，建议用 dataset 外输出目录运行：

```powershell
python -B F:\Realworld\lol\scripts\phase2\run_dataset_replay.py `
  --exe F:\Realworld\lol\outputs\tmp\<fixed-build>\bin\lol_augment_assistant.exe `
  --dataset-root F:\Realworld\lol\data\dataset\augment_offers `
  --knowledge F:\Realworld\lol\data\knowledge\augments.zh-CN.json `
  --output-dir F:\Realworld\lol\outputs\tmp\phase2_replay_benchmark `
  --timeout-seconds 30
```

该命令不会改真实 dataset；主线程应以 `producer_report.json` 的 operational failure 数量和
`benchmark_report.json` 的 original-WGC sample count/metrics 作为最终闭环证据，不能把 fake
fixture、preview-derived 校准样本或 annotation 当作 prediction。

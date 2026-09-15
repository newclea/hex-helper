# Phase2 Collection ↔ Dataset 契约对齐报告

日期：2026-08-25  
工作区：`F:\Realworld\lol`

## 结论

Collection 与 Dataset 现已使用同一份 canonical bundle 契约：Collector 原子提交
`RAW.png`、`LEFT_CARD.png`、`CENTER_CARD.png`、`RIGHT_CARD.png`、`metadata.json`，人工
标注工具随后原子写入 `annotation.json`。Collector metadata 同时包含 Dataset 必需字段
和原 Collector 诊断字段，validator 可直接校验 Collector 产物。

本轮全部验证使用程序生成的合成 BGRA fixture。contract 中的 `real` 分桶仅通过测试
Frame 的 WGC source enum 验证来源约束，没有连接、启动或操纵 LoL，也没有使用或修改
真实样本，因此不声称任何真实准确率。

## Canonical bundle

每个新样本固定为：

```text
<dataset-root>/<sample-id>/
├── RAW.png
├── LEFT_CARD.png
├── CENTER_CARD.png
├── RIGHT_CARD.png
├── metadata.json
└── annotation.json        # 人工标注后补
```

Collector 仍先在同一 `dataset_root` 下写唯一 `.tmp_<sample-id>`，校验五个非空文件和
metadata JSON 后 rename 到最终目录。失败只回滚本次 staging，不覆盖或删除已提交样本。

`metadata.json` 兼容字段如下：

- Dataset：`schema_version=1`、`sample_id`、`provenance`、
  `source.kind/reference`、`captured_at_utc`；
- Collector 兼容：`schema`、`version`、`sample_kind`、`timestamp`、`source.id`；
- 详细诊断继续保留：window、resolution/stride、UI scale、offer/card/title/icon ROI、
  detector、三卡 OCR、capture reason、dedup 和 files 映射。

## Provenance 与 benchmark 隔离

支持三个独立分桶：`real`、`synthetic`、`unknown`。Collector 的 `dataset_root` 可直接指向
任一分桶；root 末级目录是保留分桶名时，request provenance 不一致会失败关闭。

来源约束：

- WGC/Desktop Duplication 只能写 `real`；
- Replay 只能写 `synthetic` 或 `unknown`；
- 所有 source 都必须提供非空 reference；
- `real` 继续要求 window title 或 id；
- validator 同时校验父目录、metadata provenance、兼容 `sample_kind` 和 source kind。

Validator 为每个样本输出 `benchmark_eligible`，只有完整有效、已完成标注且
`provenance=real` 的样本为 true；报告同时输出
`benchmark_eligible_sample_count` 与 `benchmark_excluded_counts`。Synthetic 与 unknown
均保留独立计数，unknown 不会映射成 real。既有 benchmark 文件未修改；contract 将同一
批 Collector metadata/annotation 适配为其既有输入结构，并证明 benchmark 只计入 real、
排除 synthetic，且 unknown 在进入 benchmark 输入前已按 Dataset eligibility 排除。

## 一次迁移策略

选择“validator 显式兼容”而不是自动迁移脚本，避免工具触碰真实样本：

1. 新 Collector 与新测试只写 canonical `*_CARD.png`。
2. 完整旧三件套 `LEFT.png`/`CENTER.png`/`RIGHT.png` 仍可通过验证，但 report 包含
   `deprecated_artifact_names` warning，退出码保持 0。
3. 标注器 `list --json`/`show --json` 对旧样本输出
   `artifact_naming=legacy_deprecated` 与同名 warning。
4. 同一样本只要同时出现任一 canonical 与任一 legacy 卡图，validator 报
   `mixed_artifact_names` 并退出 1；标注器也拒绝该样本。
5. 任一命名组三卡不完整均失败。工具不会静默选边、自动重命名、删除或覆盖文件。

因此实际迁移必须由数据 owner 在独立流程中一次性重命名三张卡图；本次没有修改
`data/dataset/augment_offers/real/**`。

## 严格 JSON

Dataset metadata、annotation 与 knowledge loader 现在拒绝：

- 重复 object key；
- `NaN`、`Infinity`、`-Infinity`；
- 非 UTF-8、语法错误和非 object 根节点（按各文件契约）。

Validator stdout 使用 `allow_nan=false`，成功与失败都只输出一个可严格解析 JSON 对象。
Collector 生成 metadata 后仍在提交前执行自身 JSON 语法检查；contract 再以独立严格
Python parser 消费实际 Collector metadata。

## 测试与证据

### Collection 原测试与新增约束

MSVC `/std:c++20 /W4 /WX /permissive-` 编译成功，随后运行：

```text
sample collector tests=4 checks=61 failures=0
```

原有 50 个 checks 全部保留；新增 11 个 checks 覆盖 Dataset metadata 字段、source
reference、WGC 不得写 unknown、bucket provenance 不匹配失败、Replay synthetic 写入。

### Dataset 原测试

命令：

```powershell
python -B -m unittest discover -s tests\phase2_python -p '*_test.py' -v
```

结果：

```text
Ran 10 tests in 1.005s
OK
```

### 跨模块 contract

命令：

```powershell
tests\phase2_contract\run_contract.ps1
```

Runner 先以 `/W4 /WX` 编译 C++ Collector fixture，再执行 Python contract：

```text
Ran 2 tests in 4.020s
OK
```

覆盖链路：

1. Collector 分别向 temp `real`、`synthetic`、`unknown` 生成五文件 bundle；
2. annotation CLI 对每个 bundle 写入 left/center/right，最终 status=complete；
3. validator CLI 对三分桶返回 exit 0，stdout 通过严格 JSON 解析；
4. 既有 benchmark 读取同源适配记录，只计 1 个 real，排除 1 个 synthetic；unknown 未
   进入 benchmark 输入；
5. legacy 三件套通过且有 warning；canonical/legacy 混用时 validator 与 annotator 均
   失败；
6. duplicate key 与 NaN metadata 均被 validator 拒绝。

### 既有 benchmark 回归与 schema

```text
Phase2 benchmark: Ran 6 tests in 0.395s, OK
Python syntax: 4 files compiled
JSON Schema: Draft 2020-12 check_schema PASS
```

## 变更边界

修改范围限于需求白名单：Collector header/source/test、Dataset README/schema、annotation
与 validator 工具、原 Dataset 测试，以及新增 `tests/phase2_contract/**` 和本报告。

未修改 app、CMake、benchmark、icon、detector 或真实样本目录。构建产物只写入
`outputs/tmp/phase2_collection/**` 与 `outputs/tmp/phase2_contract/**`。工作区本身不是
Git repository，因此范围审计依据为所有 `apply_patch` 目标与构建输出目录，而非 git
diff。

## 复盘沉淀

根因不是单一文件名错误，而是生产者和消费者分别维护了未共享的“隐式契约”。本次把
文件命名、metadata、provenance/source 约束、迁移行为和 benchmark eligibility 同时固化
到 schema、工具和跨语言 contract test，防止再次出现“模块单测各绿、端到端必失败”。

后续正式 Live 接线的准入条件应固定为：先跑本 contract，再接调用点；真实准确率只能在
真实 `real` 样本经人工完整标注和严格验证后计算，synthetic/unknown 永不补写成 real。

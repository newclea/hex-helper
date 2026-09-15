# Phase2 Dataset 与人工标注工具交付报告

日期：2026-08-25  
工作区：`F:\Realworld\lol`

## 交付范围

- 建立 `data/dataset/augment_offers/` 的可审计目录契约、JSON Schema，以及
  `real/`、`synthetic/` 空目录保留文件。
- 新增纯 CLI 人工标注器 `scripts/phase2/annotate_dataset.py`：
  - 列出未完整标注样本；
  - 显示 RAW、三卡、metadata、annotation 的绝对路径；
  - 仅在显式 `--open` 时调用系统默认图片查看器；
  - 支持 `set`、`clear`、`skip` 和 `interactive`；
  - 每张卡由人工显式输入 `augment_id`、`augment_name`、`valid`；
  - 使用同目录临时文件、flush、fsync、`os.replace` 原子写 `annotation.json`；
  - 拒绝符号链接、路径逃逸和重复 provenance sample ID。
- 新增严格 JSON 验证器 `scripts/phase2/validate_dataset.py`：
  - 验证 RAW、LEFT、CENTER、RIGHT、metadata、annotation；
  - 验证三卡数量、PNG 结构/CRC/IDAT，可选再用 Pillow verify；
  - 验证 real/synthetic provenance 与 source kind；
  - 验证人工标签 ID 是否存在于 `data/knowledge/augments.zh-CN.json`，中文名是否一致；
  - 报告重复 sample ID、重复四图内容哈希、分辨率分布和逐样本问题；
  - stdout 始终只输出一个 JSON report，数据问题以退出码 1 表示。
- 新增 10 个纯 Python 临时 fixture 单测。测试不会向 checked-in dataset 写入样本或标签。

## 验证证据

语法检查：

```text
python -B -c <compile three files>
syntax_checks=3 failures=0
```

单测：

```text
python -B -m unittest discover -s tests\phase2_python -p "*_test.py" -v
Ran 10 tests
OK
```

覆盖项：合法数据集、无标签、错误 augment ID、缺卡与三卡计数、invalid card、损坏
PNG、`valid=false` 合法卡、real/synthetic provenance 错配、相互独立的重复 sample ID 与
重复 sample 内容哈希、原子替换失败时保留旧文件、临时文件清理、非交互
set/clear/skip、路径逃逸拒绝、CLI 在成功/失败报告中均保持 stdout 严格 JSON。

Schema 自检：

```text
schema_json=valid defs=annotation,cardAnnotation,cards,metadata,sampleId,source
```

checked-in 空真实 dataset 验证：

```text
python -B scripts\phase2\validate_dataset.py
exit_code=0
valid=true
sample_count=0
real_sample_count=0
synthetic_sample_count=0
annotated_sample_count=0
issues=[]
```

空 dataset 标注列表：

```json
{
  "count": 0,
  "samples": []
}
```

## 审计结论

- 没有生成真实或 synthetic 样本，没有生成伪造标签。
- 标注器不读取 stdout/OCR 结果来推断真值，也不从文件名或知识库补全标签。
- 默认不启动图片查看器；没有 GUI、推荐、联网、逆向、进程内存、注入、Hook、输入
  自动化或 LoL 操作。
- 本工作区不是 Git worktree，因此范围审计采用显式文件枚举；交付文件均位于需求白名单。

# Phase3 仓库扩展点快速审计（Wave1-R1）

## 1. 审计边界与基线

- 仓库根目录：`F:\Realworld\lol`。
- 只读范围：`CMakeLists.txt`、`src/knowledge/`、`src/storage/`、`src/app/cli.cpp`、`src/app/cli.h`、`scripts/`、`tests/`。
- 未扫描 `outputs/`、`stage/`、`result/` 的内容；除本报告外未写文件；未联网，未运行产品、构建或测试。
- Phase3 进入前基线事实：21/21 测试通过。本轮因“不得启动程序”未复跑；静态核对 `CMakeLists.txt` 得到 17 个 `lol_assistant_add_cpp_test(...)` 加 4 个直接 `add_test(...)`，合计 21 个 CTest 测试。
- 21 项包括：`common_contracts_test` 至 `augment_frame_processor_test` 的 17 个 C++ 测试，以及 `runtime_paths_test`、`augment_import_determinism`、`json_loads_validation`、`phase2_benchmark_contract`。

## 2. 现有边界与可用扩展点

- `CMakeLists.txt`：`lol_assistant_core` 直接编译 `src/knowledge/augment_catalog.cpp` 和 `src/storage/*.cpp`，并链接 `winsqlite3`；Phase3 不应加入该目标或新增 C++ 链接依赖。
- `CMakeLists.txt`：`find_package(Python3 REQUIRED COMPONENTS Interpreter)` 已位于 `BUILD_TESTING` 内，可直接作为 Python-only 合同测试接入点。
- `src/knowledge/augment_catalog.cpp`：只加载 schema v1 JSON，按 ID/模式查找；它是 Phase2 识别知识目录，不是文本检索或离线索引层。
- `src/storage/session_store.cpp`：schema v1 保存 `sessions`、`augment_offers`、`augment_choices`、`recognition_results`、`artifacts`，开启外键与 WAL。
- `src/storage/session_store.cpp`：公开实现均为建库/写入/事务；检查到的 `SELECT` 仅用于迁移、约束和 INSERT-SELECT，没有通用 retrieval API。
- `src/storage/async_storage_writer.cpp` 与 `src/storage/jsonl_writer.cpp`：服务运行时异步持久化，不能成为离线索引器依赖。
- `src/app/cli.cpp`、`src/app/cli.h`：参数集合与 `ApplicationOptions` 面向捕获/回放/识别；给这里增加 Phase3 子命令会把两条链路重新耦合。
- `scripts/build.ps1`：已固定发现 CPython 3.11，并把 `Python3_EXECUTABLE` 传给 CMake；Phase3 无需新增构建依赖。
- `scripts/test.ps1`：统一运行 CTest；只要在 `CMakeLists.txt` 注册 Phase3 Python 测试，此脚本无需修改。
- `scripts/phase2/run_dataset_replay.py`：已有标准库 `sqlite3`、URI `mode=ro`、参数绑定和显式关闭连接的只读范式；可参考行为，但不得导入或修改该文件。
- `scripts/package_phase2.ps1`：通过 `$fixedInputs` 白名单和 `SHA256SUMS.txt` 打包；它是 Phase2 portable 合同，不应塞入 Phase3 文件。
- `tests/phase2_python/`、`tests/phase2_benchmark/`、`tests/phase2_replay_benchmark/`：采用标准库 `unittest`、临时目录和子进程 CLI 合同，可复用测试形态而不复用 Phase2 fixture。

## 3. 建议新增的 Python-only offline pipeline

建议新增且仅新增以下 Phase3 自有树；不让 Python 包进入任何 C++ target：

```text
scripts/phase3/__init__.py
scripts/phase3/offline_pipeline.py
scripts/phase3/retrieval_db.py
scripts/phase3/query_retrieval.py
scripts/phase3/migrations/001_retrieval.sql
tests/phase3_python/test_offline_pipeline.py
tests/phase3_python/test_sqlite_retrieval.py
tests/phase3_python/fixtures/minimal_corpus.jsonl
scripts/package_phase3.ps1
```

- `offline_pipeline.py`：只接收显式本地输入路径，完成严格 UTF-8/JSON(L) 校验、规范化、分块、排序和索引构建；禁止网络、截图、OCR、回放和产品进程调用。
- `retrieval_db.py`：只使用 Python 3.11 标准库 `sqlite3`；建库写临时文件后原子替换，所有 SQL 参数绑定，事务失败即回滚。
- `query_retrieval.py`：只读打开独立库，输出稳定 JSONL；以 `score, document_id, ordinal` 明确排序并限制 `top_k`。
- 默认构建产物建议为 `outputs/phase3/retrieval.sqlite3`，与 `outputs/runtime/**/session.sqlite3` 完全分离；输入、数据库和结果路径均允许 CLI 覆盖。
- 输入可读取既有 `data/knowledge/augments.zh-CN.json`，但不得改写它；新增语料应放 Phase3 自有数据树，不能落入 `data/knowledge/augment_icons/`。
- pipeline 与 query 之间只共享数据库 schema 和稳定 JSON 合同，不导入 `scripts/phase2/`，不调用 `lol_augment_assistant.exe`。

## 4. 本地 SQLite retrieval 设计

- 独立 schema：`schema_version(version)`、`build_metadata(key,value)`、`documents(id,source_path,source_sha256,metadata_json)`、`chunks(id,document_id,ordinal,text)`、`chunks_fts`。
- `chunks_fts` 建议采用 FTS5 external-content；启动时检查 `ENABLE_FTS5`，缺失则明确失败，不能静默退化为全表扫描。
- 约束：`UNIQUE(document_id, ordinal)`、外键级联、规范化相对源路径；插入顺序按稳定 ID 排序，建库后执行一致性检查与 `PRAGMA user_version` 校验。
- 可复现性：元数据记录输入 SHA-256、pipeline schema/version 和参数；不把当前时间写入决定性内容，同输入应产生同结果集与稳定数据库内容。
- 查询库用 URI `mode=ro`；若分发后数据库不可变，可增加 `immutable=1`。不要打开或迁移 Phase2 的 `session.sqlite3`。
- retrieval 只返回语料片段、来源、分数和元数据；不得读取 `recognition_results.raw_json` 作为索引语料，以防视觉链路反向渗透。

## 5. 不可改动范围

- 视觉/采集链路：`src/capture/`、`src/detector/`、`src/vision/`、`src/replay/`、`src/collection/`。
- Phase2 应用编排：`src/app/augment_frame_processor.cpp`、`src/app/session_runtime.cpp`、`src/app/cli.cpp`、`src/app/cli.h`。
- Phase2 知识与运行时存储：`src/knowledge/`、`src/storage/`、`data/knowledge/augment_icons/`。
- Phase2 工具/合同：`scripts/phase2/`、`scripts/benchmark_phase2.py`、`scripts/package_phase2.ps1`、`tests/phase2_*`、`tests/vision/`、`tests/detector/`、`tests/replay/`。
- 生成/发布目录：`outputs/runtime/`、`outputs/tmp/`、`stage/`、`result/`；Phase3 只能写自己的 `outputs/phase3/`。

## 6. 测试与打包接入点

- `CMakeLists.txt`：在现有 Python3 发现之后新增两个 `add_test`，分别执行 `tests/phase3_python/test_offline_pipeline.py` 与 `tests/phase3_python/test_sqlite_retrieval.py`；工作目录固定为仓库根。
- 预期测试盘点：接入后 CTest 从基线 21 项增至 23 项；验收必须同时记录“原 21 项仍通过”和“新增 2 项通过”。
- 新测试覆盖：同输入确定性、重复 ID/坏 UTF-8/路径逃逸拒绝、事务回滚、schema 过新拒绝、FTS5 能力检查、只读查询、参数注入、稳定 top-k 和空结果。
- `scripts/test.ps1` 与 `scripts/build.ps1` 无需修改；不要为 Phase3 创建 C++ executable/library。
- 新建 `scripts/package_phase3.ps1`，沿用显式白名单、目标目录边界检查和 SHA-256 manifest；不得修改 `scripts/package_phase2.ps1`。
- Phase3 包只包含 `scripts/phase3/`、迁移 SQL、必要 schema/README 与可选只读索引库；不包含 Phase2 EXE、图标、截图、数据集或 session DB。

## 7. 审计结论

可行路径是“独立 Python 3.11 CLI + 独立 SQLite retrieval DB + CTest Python 合同 + 独立 Phase3 打包脚本”。唯一共享面应限定为只读知识输入、Python 解释器发现和 CTest 调度；禁止共享 C++ CLI、运行时 SessionStore、视觉 fixture 与 Phase2 发布清单。

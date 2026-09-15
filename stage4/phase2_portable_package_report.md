# Phase 2 可移植运行时与发布打包审计

日期：2026-08-26  
状态：运行时路径、集成测试和打包脚本已完成；按主线程要求，本任务不重建 `result`。

## 已完成

- `CMakeLists.txt` 将产品版本更新为 `0.2.0`，删除向产品 PE 注入 `LOL_ASSISTANT_ICON_MANIFEST_PATH` 源码绝对路径的 compile definition。
- `src/app/main.cpp` 的 icon manifest 解析顺序固定为：
  1. exe 目录相对 `../data/knowledge/augment_icons/manifest.json`；
  2. 当前工作目录相对 `data/knowledge/augment_icons/manifest.json`。
- `--help` 与 `--version` 已标识 Phase 2；`--version` 为 `lol_augment_assistant 0.2.0 (Phase2 portable)`。
- `session_start.icon_manifest` 回显实际采用的 manifest 绝对路径，供运行审计。
- 新增 `tests/integration/runtime_paths_test.cpp`，覆盖不同 cwd 的 `--help`、exe 相对路径优先、cwd fallback、replay 初始化，以及产品 PE 中源码 manifest 路径的 UTF-8/UTF-16 禁入检查。
- 新增 `scripts/package_phase2.ps1`。脚本从调用方指定的 build 复制 Phase 2 exe、catalog、icon manifest 与其引用的 163 个唯一 icons、config、运行脚本、dataset schema/工具、benchmark/import 工具和文档，并生成稳定排序的 `SHA256SUMS.txt`。
- package fixedInputs 明确包含 `scripts/phase2/run_dataset_replay.py`，保留 Dataset → Replay → Benchmark 发布链路。
- 打包目标只允许 `result` 或 workspace 内 `outputs/tmp`；不复制真实 dataset、runtime、整个 `outputs/tmp`、VC runtime 或游戏文件。
- package 前置闸门会校验精确 Phase 2 版本、245 templates/163 unique icon files，并拒绝仍含源码绝对 manifest 路径的 PE。

## 最小验证

fresh build 目录：`outputs/tmp/phase2-runtime-path-check`

```text
Release configure: PASS
Release build:     PASS (/W4 /WX)
runtime_paths_test: PASS, 1/1, 2.83 s
package_phase2.ps1 PowerShell parser: PASS
run_dataset_replay.py --help: PASS
```

`runtime_paths_test` 启动的是复制到两个 result-style 临时布局中的真实 Release exe：一组从 manifest 不存在的 foreign cwd 启动并命中 exe-relative manifest；另一组移除 exe-relative data 条件并命中 cwd fallback。两组 replay 均完成 pipeline 初始化且输出 `icon_template_count=245`，同时测试扫描构建产物，确认 PE 不含 `F:/Realworld/lol/data/knowledge/augment_icons/manifest.json` 的窄字符串或宽字符串。

未启动游戏、未启用 preview、未下载依赖、未写 workspace 外。

## 主线程打包命令

主线程完成最终 fresh Release build 后执行：

```powershell
.\scripts\package_phase2.ps1 `
  -BuildDirectory .\outputs\tmp\<fresh-release-build> `
  -DestinationDirectory .\result
```

成功输出会报告 package 文件数、icon 数和 `SHA256SUMS.txt` 自身 SHA-256。正式交付前应再从非仓库 cwd 执行 `result\bin\lol_augment_assistant.exe --help` 和一次 replay，并按 `result\SHA256SUMS.txt` 复核全部文件。当前 `result` 未由本任务覆盖，这是主线程明确保留的最后一步。

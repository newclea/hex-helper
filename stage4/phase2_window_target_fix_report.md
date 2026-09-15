# Phase2 LoL 游戏窗口目标校验修复报告

日期：2026-08-26

## 结论

Live 输入现仅接受当前可见且标题精确为 `League of Legends (TM) Client` 的游戏窗口。标题为 `League of Legends` 的 launcher 会被 `--window-title` 和直接 `--hwnd` 两条路径明确拒绝。HWND 仅在运行时枚举和校验，没有硬编码。

Replay 的解析与执行分支未改动；未启动预览或真实游戏采集，未增加焦点、输入、进程内存或模块查询 API。

## 修复内容

- `--window-title` 从大小写不敏感子串匹配改为大小写敏感的完整标题匹配。
- `--hwnd` 不再只检查句柄存在/可见；它必须出现在当前可见顶层窗口快照中，且窗口标题必须精确等于游戏标题。
- launcher 标题有独立、明确的拒绝错误，不会落入采集初始化。
- 首次选中后立即重新枚举并按同一 HWND 再校验一次；窗口关闭、隐藏、标题变化或句柄从快照中消失都会在采集启动前失败。
- 多个可见窗口同时具有精确游戏标题时返回 ambiguous 错误及候选列表。
- Live 选择错误保持 JSON 输出，并始终包含 `candidates` 数组；没有候选时输出空数组。
- 帮助文本已说明 exact-title 约束。

## 测试覆盖

`tests/integration/cli_test.cpp` 覆盖：

- launcher 标题拒绝；
- 精确游戏标题接受；
- 子串和大小写不精确标题拒绝；
- 直接 launcher HWND 拒绝；
- 直接游戏 HWND 接受；
- ambiguous 双游戏窗口拒绝并保留枚举顺序候选；
- invalid HWND 拒绝；
- 首次有效、确认快照中消失的 ephemeral HWND 拒绝；
- 不可见同标题窗口不参与选择；
- replay 解析既有行为保持；
- 产品源码禁止 focus/input、进程内存和模块枚举 API。

## Release 验证命令与结果

构建：

```powershell
cmake --build outputs/tmp/build_phase2_main_verify --config Release --target cli_test lol_augment_assistant --parallel
```

结果：`cli_test.exe` 与 `lol_augment_assistant.exe` Release 构建成功。

相关测试：

```powershell
ctest --test-dir outputs/tmp/build_phase2_main_verify -C Release -R "^(cli_test|capture_minimal_test|replay_tests)$" --output-on-failure
```

结果：3/3 通过，0 失败：`capture_minimal_test`、`replay_tests`、`cli_test`。

动态 launcher HWND 拒绝验证（不硬编码 HWND）：

```powershell
$exe = (Resolve-Path 'outputs/tmp/build_phase2_main_verify/bin/lol_augment_assistant.exe').Path
$windows = (& $exe --list-windows | ConvertFrom-Json).windows
$launcher = @($windows | Where-Object { $_.title -ceq 'League of Legends' })[0]
& $exe --hwnd $launcher.hwnd
```

结果：退出码 3；返回 `type=source_error`，错误明确指出 HWND 是 launcher，并包含动态枚举得到的 launcher 候选。游戏 HWND 接受由无副作用的合成快照测试验证，没有启动真实游戏采集。

禁止 API 静态检查：

```powershell
rg -n "OpenProcess|ReadProcessMemory|WriteProcessMemory|CreateToolhelp32Snapshot|EnumProcessModules|SetForegroundWindow|SetFocus\(|AttachThreadInput|SendInput|keybd_event|mouse_event" src/app/main.cpp src/app/cli.cpp src/app/cli.h
```

结果：无匹配。

## 修改范围

- `src/app/main.cpp`
- `src/app/cli.cpp`
- `src/app/cli.h`
- `tests/integration/cli_test.cpp`
- `outputs/phase2_window_target_fix_report.md`

未修改 detector、vision、collector 或其他产品源文件。

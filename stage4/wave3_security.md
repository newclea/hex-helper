# Wave3-B 独立安全边界审计

- 审计日期：2026-08-25（Asia/Shanghai）
- 工作区：`F:\Realworld\lol`
- 审计方式：全新上下文、静态源码审计、全新 Release 构建、PE imports/strings 检查
- 正式产物：仅本文件
- 临时产物：仅 `outputs/tmp/audit_security/**`

## 1. 结论

**核心产品安全边界 PASS。** 未发现游戏/第三方进程内存读取、远程写入、代码注入、函数/Present Hook、私有协议、网络下载、输入模拟或自动控制路径。产品的实时输入链路是：用户明确选择的当前可见顶层窗口 -> `IGraphicsCaptureItemInterop::CreateForWindow` -> Windows Graphics Capture 帧 -> 本进程 D3D11 staging texture -> CPU BGRA buffer。窗口 PID 只由 `GetWindowThreadProcessId` 作为枚举元数据输出，从未传给 `OpenProcess` 或任何进程内存 API。

问题计数：**P0=0，P1=0，P2=0，P3=2**。

两个 P3 均属于本地持久化硬化：一个是路径校验与实际写入之间的 reparse-point 竞态，一个是 SQLite/JSONL/截图跨介质事务的崩溃一致性。它们不产生游戏进程读取、注入、Hook、网络或输入自动化能力，但在严格的“绝不越界写/事务不逃逸”模型下不能写成零风险。

## 2. 审计范围与约束

覆盖：

- `include/**`：30 个文件
- `src/**`：29 个文件，其中 24 个 `.cpp` 全部被根 `CMakeLists.txt` 引用，无孤立产品实现
- `tests/**`：16 个文件，生成 14 个 C++ 测试 PE
- `scripts/**`：5 个文件
- 根 `CMakeLists.txt`
- 全新构建的产品 EXE 与全部 14 个 C++ 测试 EXE

目标树 `include/src/tests/scripts` 共 80 个文件、521,275 字节；根 `CMakeLists.txt` 另行审阅。

遵守限制：未运行 IDA，未逆向 LoL，未联网，未运行产品或测试 EXE，未发送输入，未打开/读取任何进程内存，未使用 Git，未修改业务文件。只执行了编译和静态二进制读取；编译输出位于允许的 `outputs/tmp/audit_security/build`。

路径判定口径：文档化的 `--workspace <path>` 是用户显式授权的输出根；用户主动选择不同于默认 `outputs/runtime` 的绝对根不算“逃逸”。“越界写”指产品在选定根之外产生写/删副作用，或在本地并发 reparse-point 对手模型下可被重定向到根外。

## 3. 边界逐项判定

| 边界 | 结果 | 可复核证据 |
|---|---|---|
| 外部进程内存读取 | PASS | 产品、测试源码中 `OpenProcess`/`ReadProcessMemory`/Nt/Zw 变体、Toolhelp/PSAPI/minidump 均零命中；产品和 14 个测试 PE 的禁止 imports 均为 0。D3D11 `Map(..., D3D11_MAP_READ)` 只映射 WGC 帧复制出的本进程 staging texture（`src/capture/windows_graphics_capture_source.cpp:519-581`）。 |
| 外部进程写入/注入 | PASS | `WriteProcessMemory`、`VirtualAllocEx`、`VirtualProtectEx`、`CreateRemoteThread[Ex]`、`NtCreateThreadEx`、APC、线程上下文修改均零源码命中、零 PE import、零二进制敏感字符串。 |
| Hook/Present Hook | PASS | `SetWindowsHookEx`、`CallNextHookEx`、MinHook/Detours/EasyHook/PolyHook、swap-chain `Present`/`IDXGISwapChain`、`D3D11CreateDeviceAndSwapChain` 均零命中；实际图形设备只调用 `D3D11CreateDevice`（capture source `:113-138`）。 |
| 输入自动化/游戏控制 | PASS | `SendInput`、`mouse_event`、`keybd_event`、键鼠消息注入、`PostMessage`/`SendMessage`、前台/焦点操纵均零产品调用和零禁止 import。测试中的 `SetWindowPos` 只调整测试自己创建的 WGC 烟测窗口（`tests/capture/capture_minimal_test.cpp:76-145`），不是产品调用。 |
| 私有协议/网络/下载 | PASS | 无 Winsock/WinHTTP/WinINet/URLMon/curl/asio/websocket 源码调用、链接库或 PE DLL；产品 DLL 集合无 `WS2_32.dll`、`WINHTTP.dll`、`WININET.dll`、`URLMON.dll`。CMake 无 FetchContent/ExternalProject/file(DOWNLOAD)，Python/PowerShell 无 requests/urllib/socket/下载命令。 |
| 动态解析/IPC 绕过 | PASS | 源码中 `GetProcAddress`/`LoadLibrary*`/Ldr/Nt/Zw/syscall/`DeviceIoControl`/共享内存/命名管道/`WM_COPYDATA` 均零命中。PE 的通用运行库含 `LoadLibraryExW`/`GetProcAddress`，但源码无引用，且 ASCII 与 UTF-16LE 二进制字符串中无禁止 API、网络 DLL、Hook 框架或 Riot/League 进程名。 |
| WGC 来源边界 | PASS | 唯一 capture item 创建点是 `CreateItemForWindow` 调用 `IGraphicsCaptureItemInterop::CreateForWindow`（capture source `:102-110`）；无 `CreateForMonitor`、picker、desktop duplication、BitBlt、进程读取。会话由 `CreateCaptureSession`/`StartCapture` 启动（`:344-380`）。 |
| 可见窗口选择/枚举 | PASS | 枚举只接受 `IsWindow` 且 `IsWindowVisible`、有可见标题、PID 非零的顶层窗口（`src/capture/window_info.cpp:19-52,84-88`）；标题模糊匹配要求唯一候选；直接 HWND 再次校验存在和可见（`src/app/main.cpp:606-633`）。PID 仅作为 JSON 元数据输出（main `:254-273`），没有后续进程句柄调用。 |
| WGC 生命周期/资源清理 | PASS | `Stop` 停止接收、请求 worker 停止、join、清队列（capture source `:240-258`）；关闭时注销 FrameArrived/Closed token，关闭 session/frame pool，并释放 item、texture、D3D 对象（`:643-675`）；异常路径在 main 中执行 `source.Stop()` 和 `CloseAfterException`（`src/app/main.cpp:724-738`）。 |
| 路径规范化与常规越界写 | PASS（并发竞态见 P3-01） | CLI 将相对路径转为绝对并 lexical normalize（`src/app/cli.cpp:27-36,137-140,263-266`）；SessionRuntime 拒绝非绝对或含 `..` 的根（`src/app/session_runtime.cpp:305-313`），创建 128-bit 随机安全 session 目录并 canonical/strict-descendant 校验（`:396-435`）；SQLite/JSONL 使用 `ResolveWorkspacePath` canonicalize 后拒绝根外目标（`src/storage/storage_internal.h:71-119`）；artifact session component 被净化并重复做根内校验（`src/output/debug_artifact_writer.cpp:22-55,260-305`）。 |
| 正常失败下事务/清理 | PASS（突然崩溃见 P3-02） | SQLite 使用 `BEGIN IMMEDIATE`、回调失败/异常 `ROLLBACK`、成功 `COMMIT`（`src/storage/session_store.cpp:680-719`）；AcceptOffer 失败删除 artifact、回退 fingerprint，并按原长度截断/重开 JSONL（`src/app/session_runtime.cpp:800-850`）；Close 有 JSONL rollback boundary 且幂等 closed 状态（`:857-915`）。 |
| Preview 非 topmost/nonactivate | PASS | 产品 preview 的 extended style 是 `WS_EX_NOACTIVATE | WS_EX_APPWINDOW`，普通 `WS_OVERLAPPEDWINDOW`，使用 `SW_SHOWNOACTIVATE`（`src/output/debug_preview_window.cpp:116-132`）；无 `WS_EX_TOPMOST`、`HWND_TOPMOST`、`SetWindowPos`、`SetForegroundWindow`、`SetFocus`，消息过程不处理键鼠控制（`:178-214`）。 |
| 构建/脚本边界 | PASS | 24/24 `src/*.cpp` 均被 CMake 引用；链接仅 Normaliz/bcrypt/D3D/DXGI/DWM/GDI/OLE/WinRT/Shcore/User32/WIC/WinSQLite（`CMakeLists.txt:75-128`）。构建/清理目录被限制在 `outputs/tmp` 且 clean 前重复校验（`scripts/build.ps1:56-68,123-128`）。脚本不启动 LoL；run/test 只执行构建目录中的本项目 EXE，离线 catalog 测试只启动 CPython 脚本。 |
| 最终 PE | PASS | 新鲜产物为 COFF-x86-64/64-bit，产品 41 个 import DLL、354 个 import symbols、Delay Import Directory 为 0；禁止 import 0。14 个测试 PE 均成功读取 imports 且禁止 import 0。 |

## 4. 精确问题

### P3-01：根内路径检查与实际文件创建之间存在 reparse-point TOCTOU

- 文件/行：`src/output/debug_artifact_writer.cpp:260-269,298-313`；同类 check/use 分离见 `src/storage/storage_internal.h:82-118` 到 `src/storage/session_store.cpp:425-428`、`src/storage/jsonl_writer.cpp:181-184`。
- 事实：artifact writer 先对 session directory 做 `weakly_canonical` 和 `IsUnderRoot`，随后才调用 WIC/`ofstream` 逐个打开目标。检查与打开没有绑定到同一个已验证目录句柄，也没有拒绝每个路径组件上的 reparse point。
- 影响：拥有 runtime root 写权限的本地并发对手若能在检查后、文件打开前把 session 目录替换为 junction/symlink，可能把新截图/sidecar 重定向到选定 workspace 之外、但仍为当前用户可写的位置。正常单进程使用、无并发目录篡改时，现有 canonical/descendant 校验有效；该问题不提供权限提升，也不触达游戏进程，因此定为 P3。
- 建议：以受控 ACL 创建 runtime/session 目录；Windows 上逐组件拒绝 reparse point，并使用目录/文件句柄绑定的打开方式（`FILE_FLAG_OPEN_REPARSE_POINT`，打开后用 `GetFinalPathNameByHandleW` 复核），把“检查”和“使用”合并到同一对象；对 SQLite/JSONL/artifact 统一采用该安全打开封装。

### P3-02：SQLite、JSONL 与截图的补偿事务不具备崩溃原子性

- 文件/行：`src/app/session_runtime.cpp:687-713,766-810`；SQLite 实际 `COMMIT` 在 `src/storage/session_store.cpp:690-719`。
- 事实：截图在进入 SQLite transaction 前写入；`accepted_offer` JSONL 在 SQLite transaction callback 内写入并 flush，但 SQLite `COMMIT` 在 callback 返回后发生。只有 `RunInTransaction` 正常返回失败时，代码才删除 artifacts 并按原长度截断 JSONL。
- 影响：若进程被强制终止、系统掉电或崩溃发生在 artifact/JSONL 已落盘而 SQLite 尚未 commit 的窗口，补偿代码不会执行，可留下无对应数据库行的截图或 JSONL 事件。所有残留仍位于 workspace（不构成路径逃逸），但事务一致性和“失败即无持久化”的语义被破坏，截图保留时间也可能超出调用者预期，因此定为 P3。
- 建议：为每次 offer 使用 workspace 内 staging 目录/事务日志；先落盘临时文件并记录 pending，SQLite commit 后用原子 rename 发布并写 committed marker；启动时扫描并回收 pending/orphan 文件、对账 JSONL 与数据库。若继续使用补偿事务，应明确其保证仅覆盖可捕获的同步失败，不覆盖进程/系统崩溃。

## 5. 源码命中解释：测试文本不等于产品能力

- `tests/integration/cli_test.cpp:131-132` 的 `"League of Legends"`、`"League Client"` 只是标题匹配测试数据；产品源码没有 Riot/League 可执行文件名、客户端 lockfile 或进程发现逻辑。
- `scripts/import_augments.py` 中的 `LCU` 仅描述已提取的离线静态 JSON；脚本只导入 argparse/collections/hashlib/json/pathlib/sys/typing，无网络模块。
- broad `network` 词扫描命中的 `bind` 是 `sqlite3_bind_*`/错误消息，`accept` 是 `AcceptOffer` 业务动作，不是 socket `bind/accept`。
- `tests/capture/capture_minimal_test.cpp` 会创建、显示、resize 测试自己的窗口，以验证 WGC；其 `CreateWindowExW`/`SetWindowPos` 不属于产品游戏控制。产品 `CreateWindowExW` 仅用于独立 debug preview。
- 最终 PE 含 `GetCurrentProcess`、`GetCurrentProcessId`、`TerminateProcess`、`IsDebuggerPresent`、`LoadLibraryExW`、`GetProcAddress` 等通用运行库/WinRT 基础导入；产品源码没有对应调用，且缺少 `OpenProcess`/远程内存/远程线程/调试附加配套原语，不能形成外部游戏进程访问路径。

## 6. PE imports 证据

### 6.1 产品 PE

- 文件：`outputs/tmp/audit_security/build/bin/lol_augment_assistant.exe`
- 大小：691,712 bytes
- SHA-256：`4E9FA339E39C2E34E900305F741E936245593FD88FAD47D25FB2BFAB0E2E0EE4`
- 格式：`COFF-x86-64`，`IMAGE_FILE_MACHINE_AMD64`，64-bit，CUI
- 安全属性：Dynamic Base、High Entropy VA、NX Compatible
- Import DLL：41；Import symbols：354；Delay imports：0
- 非 CRT/API-set 功能 DLL：`bcrypt.dll`, `d3d11.dll`, `GDI32.dll`, `Normaliz.dll`, `ole32.dll`, `OLEAUT32.dll`, `USER32.dll`, `winsqlite3.dll`
- 完整 Import DLL 集：`Normaliz.dll`, `bcrypt.dll`, `d3d11.dll`, `GDI32.dll`, `ole32.dll`, `api-ms-win-core-winrt-l1-1-0.dll`, `USER32.dll`, `api-ms-win-core-string-l1-1-0.dll`, `api-ms-win-core-localization-l1-2-0.dll`, `api-ms-win-core-errorhandling-l1-1-0.dll`, `api-ms-win-core-libraryloader-l1-2-0.dll`, `winsqlite3.dll`, `MSVCP140.dll`, `VCRUNTIME140.dll`, `VCRUNTIME140_1.dll`, `api-ms-win-crt-runtime-l1-1-0.dll`, `api-ms-win-crt-heap-l1-1-0.dll`, `api-ms-win-crt-stdio-l1-1-0.dll`, `api-ms-win-crt-string-l1-1-0.dll`, `api-ms-win-crt-math-l1-1-0.dll`, `api-ms-win-crt-filesystem-l1-1-0.dll`, `api-ms-win-crt-time-l1-1-0.dll`, `api-ms-win-crt-locale-l1-1-0.dll`, `api-ms-win-core-heap-l2-1-0.dll`, `api-ms-win-core-synch-l1-1-0.dll`, `api-ms-win-core-synch-l1-2-0.dll`, `api-ms-win-core-processthreads-l1-1-0.dll`, `api-ms-win-core-sysinfo-l1-1-0.dll`, `api-ms-win-core-processenvironment-l1-1-0.dll`, `api-ms-win-core-file-l1-1-0.dll`, `api-ms-win-core-file-l1-2-2.dll`, `api-ms-win-core-handle-l1-1-0.dll`, `api-ms-win-core-file-l2-1-0.dll`, `api-ms-win-core-rtlsupport-l1-1-0.dll`, `api-ms-win-core-processthreads-l1-1-1.dll`, `api-ms-win-core-profile-l1-1-0.dll`, `api-ms-win-core-interlocked-l1-1-0.dll`, `api-ms-win-core-debug-l1-1-0.dll`, `api-ms-win-core-winrt-error-l1-1-1.dll`, `OLEAUT32.dll`, `api-ms-win-core-heap-l1-1-0.dll`
- 网络 DLL：0
- 禁止 import：0
- 与可见屏幕链路相关的允许 imports：`D3D11CreateDevice`、`CreateDirect3D11DeviceFromDXGIDevice`、`RoGetActivationFactory`、`EnumWindows`、`IsWindowVisible`、`GetWindowTextW`、`GetWindowThreadProcessId`。
- Preview 允许 imports：`CreateWindowExW`、`ShowWindow`、paint/GDI/message-loop API；没有 topmost/focus/input injection imports。

### 6.2 核心测试 PE

| PE | bytes | Import DLL 数 | SHA-256 | 禁止 imports |
|---|---:|---:|---|---:|
| `capture_minimal_test.exe` | 126,976 | 29 | `9DC09A4B6BBEB2D18A1293E253761A046D60BFC487833C9D6D8B5C6D4D60EA1F` | 0 |
| `session_runtime_test.exe` | 454,144 | 36 | `7CD585F660FCC4A6DC97F2D81CC49AEFC5C803326B9551A8029F44C446C006BE` | 0 |
| `storage_worker_tests.exe` | 312,320 | 29 | `F2B4280EF3D8542219A4B487F5CA77568C3C2DD765EB0CCF0EB599606E09D3DC` | 0 |
| `augment_frame_processor_test.exe` | 496,640 | 36 | `4F1AD2E369552B9C6CA54628897674531E3AB715D5D67C6D625F673AAB6B5144` | 0 |

另外 10 个 C++ 测试 PE（augment catalog、CLI、contracts、detector、OCR、output、recognition、replay、state、text matcher）逐一执行等效 imports 检查，工具退出码均为 0，禁止 imports 均为 0。共检查 15 个 PE（1 产品 + 14 测试）。

## 7. 关键命令与退出码

说明：`rg` 的退出码 1 表示“无匹配”，在负向安全扫描中是预期 PASS；没有把测试 EXE/产品 EXE 当程序运行。

| 命令（规范化展示） | 退出码 | 结果 |
|---|---:|---|
| `rg --files include src tests scripts` + 文件/字节统计 | 0 | 80 文件，521,275 bytes |
| `rg`：产品 OpenProcess/RPM/WPM/remote alloc/thread/Hook/SendInput 精确集合 | 1 | 无匹配 |
| `rg`：tests/scripts 同一精确集合 | 1 | 无匹配 |
| `rg`：产品 PostMessage/SendMessage/焦点/键鼠消息集合 | 1 | 无匹配 |
| `rg`：产品 Present/swap-chain/Hook framework 集合 | 1 | 无匹配 |
| `rg`：Winsock/WinHTTP/WinINet/URLMon/download/CMake FetchContent 集合 | 1 | 无真实网络/下载命中 |
| `rg`：动态解析、Ldr/Nt/Zw、syscall、驱动 IO、共享内存、命名管道、WM_COPYDATA | 1 | 产品与 tests/scripts 均无匹配 |
| `rg -n "CreateFor(Window|Monitor)|..." include src tests scripts CMakeLists.txt` | 0 | 唯一 item 创建为 `CreateForWindow`；另有预期的 frame pool/session 调用 |
| `rg`：preview topmost/nonactivate/focus styles | 0 | 仅 `WS_EX_NOACTIVATE` + `SW_SHOWNOACTIVATE`；topmost/focus 产品命中 0 |
| `scripts/build.ps1 -Configuration Release -BuildDirectory outputs/tmp/audit_security/build` | 0 | configure=0，build=0，产品及 14 测试 PE 构建成功 |
| `llvm-readobj --file-headers --coff-imports .../lol_augment_assistant.exe` | 0 | AMD64；41 DLL/354 symbols；delay import 0；禁止 import 0 |
| 对 build/bin 全部 15 个 EXE 循环 `llvm-readobj --coff-imports` | 0 | 每个子调用 exit=0；每个禁止 import hits=0 |
| 对全部 15 个 EXE 循环 `strings -a -e s` 与 `strings -a -e l` | 0 | 每个 ASCII/UTF-16LE 子调用 exit=0；敏感字符串 hits=0 |

## 8. 最终判断

产品的可达采集链路止于用户选择的可见窗口 WGC 像素和显式 replay 文件；没有游戏进程句柄、进程内存、注入、Hook、私有协议、网络下载或输入控制链路。PE imports 与源码结论一致，且 tests 中的 League 标题、测试窗口和业务 `accept`/SQLite `bind` 已与产品能力明确分离。

因此，**反作弊/只读屏幕信息安全边界通过**。若验收标准同时要求面对同权限本地目录竞争者也绝不越界、并要求 SQLite/JSONL/截图在电源故障下跨介质原子，则需关闭 P3-01 与 P3-02 后才能对这两项作无条件 PASS。

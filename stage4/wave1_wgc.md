# Wave1-D Windows Graphics Capture 与本机 SDK 编译可行性

审计日期：2026-08-25（Asia/Shanghai）  
固定工作目录：`F:\Realworld\lol`  
结论：**可行**。本机已用同一份最小 C++20/CMake 探针完成两条 x64 编译/链接链路：

1. CMake Visual Studio generator + MSVC 19.44.35225：配置退出 `0`，构建退出 `0`，`0` 警告、`0` 错误。
2. CMake Ninja generator + clang-cl/lld-link 19.1.7：配置退出 `0`，构建退出 `0`。

探针只被编译、链接和静态检查；**未运行探针 EXE，未启动或捕获 LoL，未执行任何窗口/显示器捕获**。

## 1. 本机基线

| 项目 | 已验证值 |
|---|---|
| Windows | Microsoft Windows 11 家庭版 中文版，DisplayVersion `25H2` |
| OS 内核/补丁 | `10.0.26200`，CurrentBuild `26200`，UBR `9168`，即 `26200.9168` |
| BuildLabEx | `26100.1.amd64fre.ge_release.240331-1435` |
| 架构 | OS `64 位`，当前进程 `X64` |
| Windows SDK 根目录 | `C:\Program Files (x86)\Windows Kits\10\` |
| Windows SDK | Include/Lib/References/UnionMetadata 均安装 `10.0.26100.0`；Include 仅发现这一版 |
| C++/WinRT 投影版本 | `2.0.250303.1`，来自 SDK `cppwinrt\winrt\base.h` 的 `CPPWINRT_VERSION` |
| Universal API Contract 元数据 | `19.0.0.0` |
| Visual Studio | Enterprise 2022 `17.14.29`，安装版本 `17.14.37111.16`，路径 `D:\Downloads\VisualStudio\Enterprise` |
| MSVC toolset | 目录版本 `14.44.35207` |
| cl.exe | `19.44.35225.0`，x64 host/x64 target |
| link.exe | `14.44.35225.0` |
| MSBuild | `17.14.40+3e7442088` |
| rc.exe | SDK 路径 `bin\10.0.26100.0\x64\rc.exe`，文件版本 `10.0.26100.7705` |
| clang-cl | `19.1.7`，target `x86_64-pc-windows-msvc` |
| lld-link | LLD `19.1.7` |
| CMake | `3.31.5` |
| Ninja | `1.12.1` |

当前普通 PowerShell PATH 上可直接解析 `clang-cl.exe`、`cmake.exe`、`ninja.exe`；不能直接解析 `cl.exe`、`msbuild.exe`、`rc.exe`。VS 与 MSVC 实体文件存在且可由 CMake Visual Studio generator 直接调用。

## 2. WGC、WinRT 与 D3D11 本机实体

以下均为本机文件检查结果，不是文档推断。

| 能力 | 本机路径/证据 | 结果 |
|---|---|---|
| C++/WinRT WGC 投影 | `C:\Program Files (x86)\Windows Kits\10\Include\10.0.26100.0\cppwinrt\winrt\Windows.Graphics.Capture.h` | 存在；含 `Direct3D11CaptureFramePool::Create`、`CreateFreeThreaded`、`FrameArrived`、`TryGetNextFrame`、`Recreate`、`CreateCaptureSession` |
| C++/WinRT D3D11 投影 | `...\cppwinrt\winrt\Windows.Graphics.DirectX.Direct3D11.h` | 存在 |
| `IGraphicsCaptureItemInterop` | `...\um\windows.graphics.capture.interop.h` | 存在；IID `3628E81B-3CAC-4C60-B7F4-23CE0E0C3356`；声明 `CreateForWindow` 与 `CreateForMonitor` |
| D3D11/WinRT interop | `...\um\windows.graphics.directx.direct3d11.interop.h` | 存在；声明 `CreateDirect3D11DeviceFromDXGIDevice` 与 `CreateDirect3D11SurfaceFromDXGISurface` |
| D3D11 API | `...\um\d3d11.h` | 存在；声明 `D3D11CreateDevice` |
| DXGI 1.2 API | `...\shared\dxgi1_2.h` | 存在 |
| WinRT 元数据 | `...\UnionMetadata\10.0.26100.0\Windows.winmd`，7,425,024 bytes | 存在 |
| D3D11 x64 import lib | `...\Lib\10.0.26100.0\um\x64\d3d11.lib` | 存在；`dumpbin /linkermember:1` 找到 `D3D11CreateDevice` 与 `__imp_D3D11CreateDevice` |
| DXGI x64 import lib | `...\Lib\10.0.26100.0\um\x64\dxgi.lib` | 存在 |
| WindowsApp x64 lib | `...\Lib\10.0.26100.0\um\x64\windowsapp.lib` | 存在；找到 `CreateDirect3D11DeviceFromDXGIDevice`、`RoInitialize`、`RoGetActivationFactory` |
| RuntimeObject x64 lib | `...\Lib\10.0.26100.0\um\x64\runtimeobject.lib` | 存在；找到 `RoInitialize`、`RoGetActivationFactory` |

关键 include 结论：SDK 同时包含 `Include\10.0.26100.0\winrt\...` ABI 头和 `Include\10.0.26100.0\cppwinrt\winrt\...` C++/WinRT 投影。CMake 探针用 `BEFORE` 显式把 `cppwinrt` 目录放在前面，防止 `<winrt/Windows.Graphics.Capture.h>` 被同名 ABI 目录遮蔽。

### 2.1 本机运行时注册证据

注册表 `HKLM\SOFTWARE\Microsoft\WindowsRuntime\ActivatableClassId` 中确认存在：

- `Windows.Graphics.Capture.GraphicsCaptureItem`
- `Windows.Graphics.Capture.Direct3D11CaptureFramePool`
- `Windows.Graphics.Capture.GraphicsCapturePicker`
- `Windows.Graphics.Capture.GraphicsCaptureSession`

其中 `GraphicsCaptureItem` 与 `Direct3D11CaptureFramePool` 的 `DllPath` 均为 `C:\Windows\System32\GraphicsCapture.dll`。该 DLL 存在，文件版本 `10.0.26100.8972`，SHA-256 `10A4CFFF9AF1DEE1ACC4063C5A46F4EBA766AEF39DDEBE585F1D9AD33F3F92EE`。这确认了类注册和服务 DLL 实体；本任务没有调用 activation factory 做运行时捕获测试。

## 3. 最小编译/链接探针

源文件：

- `F:\Realworld\lol\outputs\tmp\wgc_probe\CMakeLists.txt`
- `F:\Realworld\lol\outputs\tmp\wgc_probe\wgc_probe.cpp`

探针覆盖的链接/API 面：

- `D3D11CreateDevice`，带 `D3D11_CREATE_DEVICE_BGRA_SUPPORT`。
- `IDXGIDevice` 到 WinRT `IDirect3DDevice` 的 `CreateDirect3D11DeviceFromDXGIDevice` 包装。
- `IGraphicsCaptureItemInterop::CreateForWindow` 与 `CreateForMonitor`。
- `Direct3D11CaptureFramePool::CreateFreeThreaded`、`TryGetNextFrame`、`Recreate`。
- `CreateCaptureSession` 与 WinRT `Close` 生命周期调用。

`wmain` 仅在 `argc == 31337` 时引用运行时代码，以确保符号进入最终 PE；本任务没有运行该 EXE。

源文件校验：

| 文件 | SHA-256 |
|---|---|
| `CMakeLists.txt` | `AE09A0745A8C11FCD860D3B8F1413FDCCBF27A2C62AFC00032907090318810F4` |
| `wgc_probe.cpp` | `29472B87767FB4683E2AF3F9565EAF1CB7981F357BEFD072B920CD3969D68288` |

### 3.1 推荐主链路：CMake Visual Studio generator + MSVC

工作目录固定为 `F:\Realworld\lol`。实际执行命令：

```powershell
$probeRoot = (Resolve-Path -LiteralPath 'outputs\tmp\wgc_probe').Path
$tempPath = Join-Path $probeRoot 'temp'
New-Item -ItemType Directory -Force -Path $tempPath | Out-Null
$env:TEMP = $tempPath
$env:TMP = $tempPath

& 'D:\Env\MinGW\bin\cmake.exe' `
  -S 'outputs\tmp\wgc_probe' `
  -B 'outputs\tmp\wgc_probe\build-msvc' `
  -G 'Visual Studio 17 2022' `
  -A x64 `
  -T host=x64 `
  '-DCMAKE_GENERATOR_INSTANCE=D:/Downloads/VisualStudio/Enterprise' `
  '-DCMAKE_SYSTEM_VERSION=10.0.26100.0'
# 退出码：0

& 'D:\Env\MinGW\bin\cmake.exe' `
  --build 'outputs\tmp\wgc_probe\build-msvc' `
  --config Release `
  --target wgc_probe `
  --verbose
# 退出码：0
```

CMake 识别：`MSVC 19.44.35225.0`，编译器为 `D:/Downloads/VisualStudio/Enterprise/VC/Tools/MSVC/14.44.35207/bin/Hostx64/x64/cl.exe`。

实际链接结果：

```text
link.exe ... /OUT:...\build-msvc\Release\wgc_probe.exe ...
  d3d11.lib dxgi.lib windowsapp.lib runtimeobject.lib ... /MACHINE:X64
wgc_probe.vcxproj -> F:\Realworld\lol\outputs\tmp\wgc_probe\build-msvc\Release\wgc_probe.exe
已成功生成。
    0 个警告
    0 个错误
BUILD_EXIT_CODE=0
```

产物：

- EXE：`F:\Realworld\lol\outputs\tmp\wgc_probe\build-msvc\Release\wgc_probe.exe`
- 大小：38,400 bytes
- SHA-256：`406DC46B2D335E0CF8688CF7F2F181B518D45B399EA0BEBF4937A273C0C724F2`
- PE：machine `x64`，subsystem `Windows CUI`，linker version `14.44`
- 导入表确认：`D3D11CreateDevice`、`CreateDirect3D11DeviceFromDXGIDevice`、`RoGetActivationFactory`

### 3.2 已验证备选链路：CMake Ninja + clang-cl/lld-link

该路径不依赖损坏的 VS 开发者命令提示初始化脚本；显式提供 MSVC/SDK include、lib、rc、mt 路径。

```powershell
$msvc = 'D:\Downloads\VisualStudio\Enterprise\VC\Tools\MSVC\14.44.35207'
$sdk = 'C:\Program Files (x86)\Windows Kits\10'
$sdkCMake = $sdk.Replace('\', '/')
$ver = '10.0.26100.0'
$probeRoot = (Resolve-Path -LiteralPath 'outputs\tmp\wgc_probe').Path

$env:TEMP = Join-Path $probeRoot 'temp'
$env:TMP = $env:TEMP
$env:INCLUDE = @(
  "$sdk\Include\$ver\cppwinrt",
  "$msvc\include",
  "$sdk\Include\$ver\ucrt",
  "$sdk\Include\$ver\shared",
  "$sdk\Include\$ver\um",
  "$sdk\Include\$ver\winrt"
) -join ';'
$env:LIB = @(
  "$msvc\lib\x64",
  "$sdk\Lib\$ver\ucrt\x64",
  "$sdk\Lib\$ver\um\x64"
) -join ';'
$env:LIBPATH = @(
  "$msvc\lib\x64",
  "$sdk\UnionMetadata\$ver",
  "$sdk\References\$ver"
) -join ';'
$env:Path = @(
  'D:\Env\MinGW\bin',
  "$msvc\bin\Hostx64\x64",
  "$sdk\bin\$ver\x64",
  $env:Path
) -join ';'

& 'D:\Env\MinGW\bin\cmake.exe' `
  -S 'outputs\tmp\wgc_probe' `
  -B 'outputs\tmp\wgc_probe\build-clang-fixed' `
  -G Ninja `
  '-DCMAKE_BUILD_TYPE=Release' `
  '-DCMAKE_SYSTEM_VERSION=10.0.26100.0' `
  '-DCMAKE_CXX_COMPILER=D:/Env/MinGW/bin/clang-cl.exe' `
  '-DCMAKE_LINKER=D:/Env/MinGW/bin/lld-link.exe' `
  '-DCMAKE_CXX_FLAGS_INIT=-fuse-ld=lld' `
  "-DCMAKE_RC_COMPILER=$sdkCMake/bin/$ver/x64/rc.exe" `
  "-DCMAKE_MT=$sdkCMake/bin/$ver/x64/mt.exe"
# 退出码：0

& 'D:\Env\MinGW\bin\cmake.exe' `
  --build 'outputs\tmp\wgc_probe\build-clang-fixed' `
  --verbose
# 退出码：0
```

实际链接器为 `D:\Env\MinGW\bin\lld-link.exe`，实际命令包含：

```text
d3d11.lib dxgi.lib windowsapp.lib runtimeobject.lib ... /machine:x64
CLANG_BUILD_EXIT_CODE=0
```

产物：

- EXE：`F:\Realworld\lol\outputs\tmp\wgc_probe\build-clang-fixed\wgc_probe.exe`
- 大小：37,376 bytes
- SHA-256：`3821BBD9A29CABAF92BBEAAE7B7AC61579C1E7B4EDC72460B867B8EF250B2617`
- PE：machine `x64`，subsystem `Windows CUI`
- 导入表确认：`D3D11CreateDevice`、`CreateDirect3D11DeviceFromDXGIDevice`、`RoGetActivationFactory`

## 4. 失败原文与已验证替代路径

### 4.1 VS 环境初始化脚本损坏

`D:\Downloads\VisualStudio\Enterprise\Common7\Tools\VsDevCmd.bat` 和经 `VC\Auxiliary\Build\vcvars64.bat` 间接调用的同一路径均退出 `1`。原始错误：

```text
[ERROR:VsDevCmd.bat] Script "vsdevcmd\ext\Active" could not be found.
[ERROR:VsDevCmd.bat] *** VsDevCmd.bat encountered errors. Environment may be incomplete and/or incorrect. ***
EXIT=1
```

已验证替代：不调用该脚本，直接使用 CMake Visual Studio generator，并通过 `CMAKE_GENERATOR_INSTANCE` 指定已安装 VS 实例。配置与构建均退出 `0`。

影响边界：依赖 `VsDevCmd.bat`/`vcvars64.bat` 的手工命令行或 CI 初始化会失败；本报告推荐的 Visual Studio generator 主链路不受该错误阻断。本任务遵守“不安装系统组件”，未执行 VS 修复。

### 4.2 clang-cl 首次 CMake 配置路径转义错误

首次输出目录保留在 `outputs\tmp\wgc_probe\build-clang`。首次配置退出 `1`，原始核心错误：

```text
CMake Error at build-clang/CMakeFiles/3.31.5/CMakeRCCompiler.cmake:1 (set):
  when parsing string
    C:\Program Files (x86)\Windows Kits\10/bin/10.0.26100.0/x64/rc.exe
  Invalid character escape '\P'.
CLANG_CONFIGURE_EXIT_CODE=1
```

根因是传给 CMake cache 的 `CMAKE_RC_COMPILER` 使用了混合斜杠，反斜杠进入生成的 CMake 源后被解析为转义符。已验证替代：用 `$sdk.Replace('\', '/')` 生成全正斜杠路径，在新目录 `build-clang-fixed` 配置退出 `0`、构建退出 `0`。

## 5. 正式 Capture 技术建议

### 5.1 选定构建路线

本机正式工程优先采用：**C++20 + CMake Visual Studio 17 2022 generator + MSVC + Windows SDK 10.0.26100.0 + x64**。

理由是该路径由 CMake 直接发现 VS/MSBuild、MSVC 与 SDK，不依赖当前损坏的 `VsDevCmd.bat`，且生成的链接命令已验证 WGC/WinRT/D3D11 全链路。clang-cl 19.1.7 + Ninja + lld-link 已作为可用备选，但必须显式固定 MSVC/SDK include 与 lib 环境。

正式 CMake 应保留以下约束：

- 把 `...\Include\10.0.26100.0\cppwinrt` 放在 ABI `winrt` 目录之前。
- 固定 x64 和 SDK `10.0.26100.0`，避免环境升级后静默换 SDK。
- 显式链接 `d3d11`、`dxgi`、`windowsapp`、`runtimeobject`。
- 启动 Capture 前调用 `GraphicsCaptureSession::IsSupported()`；返回 false 时禁用 Capture 功能并给出明确状态。

### 5.2 帧线程模型

推荐用 `Direct3D11CaptureFramePool::CreateFreeThreaded`：

1. 控制线程负责状态机与窗口选择；Capture 状态按 `Idle -> Starting -> Running -> Recreating/Stopping -> Stopped` 单向迁移。
2. `CreateFreeThreaded` 的 `FrameArrived` 由 frame pool 内部 worker thread 触发，不要求调用线程持有 `DispatcherQueue`。
3. 回调只做短路径：循环/单次取最新 frame，读取 `ContentSize`，把纹理复制到自有 GPU/缓冲资源，立即释放 `Direct3D11CaptureFrame`，再把轻量描述符送入有界队列。
4. 编码、CPU readback、文件/网络 I/O、UI 通知不得阻塞 `FrameArrived` 回调。实时链路积压时丢弃旧帧，不能无限扩张队列。
5. D3D11 immediate context 采用单线程所有权。若 Capture 和下游同时使用同一 immediate context，必须串行化；不要把同一 context 无保护地跨线程调用。

选择普通 `Create` 时，调用线程必须提供 DispatcherQueue 并持续泵消息；对本项目的独立桌面 Capture worker，`CreateFreeThreaded` 的依赖更少。

### 5.3 窗口/显示器选择

已知目标窗口句柄时采用本探针已编译的桌面 interop 路径：

```text
GraphicsCaptureItem activation factory
  -> QueryInterface IGraphicsCaptureItemInterop
  -> CreateForWindow(HWND, IID_IGraphicsCaptureItem)
```

建议在创建 item 前执行：

- `IsWindow(hwnd)`；将子窗口归一到 `GetAncestor(hwnd, GA_ROOT)`。
- 记录窗口所属 PID 与进程创建身份，防止关闭后的 HWND 值被系统复用后误绑定。
- 把 `GraphicsCaptureItem::Closed` 作为目标失效的权威信号。

整屏/指定显示器使用 `CreateForMonitor(HMONITOR, ...)`。需要用户明确选择时使用 `GraphicsCapturePicker`，并在桌面应用中把 picker 初始化到所属 HWND；不要把 picker 与已知 HWND 的无 UI 路径混成一个隐式分支。

### 5.4 资源生命周期

建议所有权顺序：

```text
COM/WinRT apartment
  -> ID3D11Device + immediate context
  -> IDXGIDevice / WinRT IDirect3DDevice wrapper
  -> GraphicsCaptureItem
  -> Direct3D11CaptureFramePool
  -> GraphicsCaptureSession
  -> event revokers + downstream textures/queues
```

停止顺序反向执行，并先阻止新回调进入：

1. 原子地把状态切为 `Stopping`，拒绝新帧入队。
2. 撤销 `FrameArrived` 与 `GraphicsCaptureItem::Closed` handler；等待已进入的回调退出。
3. `Close` session，再 `Close` frame pool。
4. 清空队列并释放所有 frame/surface/texture；最后释放 WinRT device wrapper、D3D11 context/device 与 apartment。

事件 handler 使用 revoker 或显式保存 token；回调捕获共享状态时使用 `weak_ptr`/等价弱引用，避免 frame pool、session 与 handler 形成生命周期环。

### 5.5 resize、最小化、关闭与设备丢失

- 每帧比较 `Direct3D11CaptureFrame::ContentSize` 与当前池尺寸。
- 尺寸变化时先完成当前帧复制并释放该 frame，再调用 `framePool.Recreate(device, format, bufferCount, newSize)`；同步重建依赖尺寸的自有纹理/编码表面。
- `ContentSize` 为 `0 x 0` 时进入暂停态，不以零尺寸调用 `Recreate`；等待后续非零尺寸帧。
- 输出只使用 `ContentSize` 有效区域，不能把池纹理边缘的旧像素当作当前画面。
- 收到 `GraphicsCaptureItem::Closed` 后只发停止信号，由资源 owner 执行统一 teardown，避免在事件回调中与并发 `FrameArrived` 互相销毁资源。
- 对 `DXGI_ERROR_DEVICE_REMOVED`/`DXGI_ERROR_DEVICE_RESET` 执行完整重建：关闭 session/pool，释放 WinRT D3D wrapper 和 D3D11 资源，重新创建设备、item、pool、session；仅调用 `Recreate` 不能修复设备丢失。
- SDR 默认使用 `B8G8R8A8UIntNormalized`。需要保留 HDR 时单独建立 `R16G16B16A16Float` 与色彩管理链路，不在 BGRA8 路径里隐式截断。

## 6. 产物边界与未决风险

本任务新建/生成目录均位于：

```text
F:\Realworld\lol\outputs\tmp\wgc_probe\
  CMakeLists.txt
  wgc_probe.cpp
  build-msvc\
  build-clang\          # 保留首次失败证据
  build-clang-fixed\
  temp\
```

构建输出明确把 obj/exe/CMake/MSBuild/Ninja 中间文件写入上述目录。本任务的变更调用只命中 `outputs\wave1_wgc.md`、`outputs\tmp\wgc_probe\CMakeLists.txt`、`outputs\tmp\wgc_probe\wgc_probe.cpp`；CMake 的 `-B`、MSBuild/Ninja 输出和 `TEMP`/`TMP` 也全部指向 `outputs\tmp\wgc_probe`。

最终时间戳审计覆盖整个共享工作区：从本任务首次写入前的 `2026-08-25 17:19:00` 起共有 117 个文件发生写入，其中 90 个位于本任务允许写域，27 个位于 `outputs\wave1_repo_env.md`、`outputs\wave1_vision_ocr.md`、`outputs\tmp\vision_audit`、`outputs\tmp\game_assets`、`outputs\tmp\reverse` 等其他 Agent 写域。共享工作区存在并发任务，时间戳不能作为写入归因；本任务工具调用记录中没有以这 27 个文件为写目标的命令，它们均被原样保留。

未决风险均标记为“未验证”，不作推断：

- 未验证实际 GPU、驱动、D3D feature level、跨适配器复制与 device-lost 恢复；探针只编译/链接。
- 未验证 `GraphicsCaptureSession::IsSupported()` 的本机运行时返回值、activation factory 调用与 `IGraphicsCaptureItemInterop` 运行时 QI；只确认注册表、DLL、头文件、库、链接和 PE 导入。
- 未验证窗口最小化、resize、DPI、HDR、受保护内容、独占/无边框模式下的帧语义和吞吐。
- 未验证 LoL 的任何 Capture 行为；任务明确禁止运行/捕获 LoL。
- VS 的 `VsDevCmd.bat`/`vcvars64.bat` 初始化路径当前退出 `1`；依赖该脚本的后续 CI 必须绕过或在另行授权后修复 VS 安装。

## 7. 核心证据命令

以下命令均以 `F:\Realworld\lol` 为工作目录执行：

```powershell
# OS
Get-CimInstance Win32_OperatingSystem
Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'

# SDK 根目录与版本
Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows Kits\Installed Roots'
Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10\Include' -Directory

# VS/MSVC
& "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe" `
  -all -products * -format json -utf8
& 'D:\Downloads\VisualStudio\Enterprise\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64\cl.exe' /Bv

# 工具版本
clang-cl --version
lld-link --version
cmake --version
ninja --version

# 导入库符号
& 'D:\Downloads\VisualStudio\Enterprise\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64\dumpbin.exe' `
  /nologo /linkermember:1 `
  'C:\Program Files (x86)\Windows Kits\10\Lib\10.0.26100.0\um\x64\windowsapp.lib'

# 运行时类注册
Get-ItemProperty `
  'HKLM:\SOFTWARE\Microsoft\WindowsRuntime\ActivatableClassId\Windows.Graphics.Capture.GraphicsCaptureItem'
Get-ItemProperty `
  'HKLM:\SOFTWARE\Microsoft\WindowsRuntime\ActivatableClassId\Windows.Graphics.Capture.Direct3D11CaptureFramePool'

# 最终 PE 头与导入表；没有运行 EXE
$dumpbin = 'D:\Downloads\VisualStudio\Enterprise\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64\dumpbin.exe'
& $dumpbin /headers 'outputs\tmp\wgc_probe\build-msvc\Release\wgc_probe.exe'
& $dumpbin /imports 'outputs\tmp\wgc_probe\build-msvc\Release\wgc_probe.exe'
```

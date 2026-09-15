# Wave1-A 仓库与工程现状审计

## 1. 审计元数据

- 审计根目录：`F:\Realworld\lol`
- 审计时间：起点快照为 2026-08-25T17:15:50.7393197+08:00；最终并发树快照为 2026-08-25T17:21:08.7458126+08:00（Asia/Shanghai）。
- 命令工作目录：所有审计命令均以 `F:\Realworld\lol` 作为进程工作目录执行。
- 审计方式：只读目录/文件枚举、Git 查询、命令发现与 `--version`/等价版本查询。
- 写入范围：本报告是本任务唯一创建的文件；未创建本任务临时文件。
- 明确未做：未编辑工程文件，未安装依赖，未检查游戏安装目录，未读取进程内存，未注入/Hook，未抓协议，未控制输入，未触碰 Vanguard，未研究 WGC API 或 SDK 细节。

## 2. 结论摘要

1. **审计起点的工作区为空。** 首次递归快照为 0 个文件、0 个子目录、0 字节；`rg --files -uu -g '!.git/**'` 无输出且退出码为 1（无匹配）。
2. **该路径不是 Git 仓库。** 根目录没有 `.git`；`git rev-parse` 和 `git status` 均返回 `fatal: not a git repository`。因此 Git 的 clean/dirty 状态为“不适用”，不能报告成“clean”。
3. **没有现有技术栈。** 未发现源文件、构建描述、依赖清单、测试目录/配置、脚本、文档或可复用代码。
4. **没有可归属为“未提交变更”的内容。** 原因不是仓库干净，而是不存在 Git 元数据，且起点文件数为 0。
5. **本机具备可用的 Windows C/C++ 工具链。** 已验证 MSVC 19.44.35225（需显式路径或激活 VS 开发环境）、Clang/clang-cl 19.1.7、GCC/G++ 14.2.0、CMake 3.31.5（PATH）与 3.31.6-msvc6（VS 自带）、Ninja 1.12.1。
6. **Python 的可靠入口是 `python` 或 `py -3.11`。** 二者均为 CPython 3.11.6；`py` 默认登记的 3.12 解释器文件不存在，`py --version`/`py -3.12 --version` 不可用；`python3` 是 Microsoft Store 应用执行别名而非可用解释器。
7. 审计过程中观察到其他任务持续写入 `outputs\tmp\vision_audit\`、`game_assets\`、`reverse\` 和 `wgc_probe\`。本任务没有创建、修改或删除这些路径；它们不是审计起点的工程内容。最终快照时根目录除 `outputs\` 外仍为 0 条目。

## 3. 文件树与时序快照

### 3.1 审计起点快照（首次写入前）

```text
F:\Realworld\lol\
└── (empty)
```

关键计数：

```text
Root         : F:\Realworld\lol
TotalEntries : 0
Files        : 0
Directories  : 0
Bytes        : 0
```

根对象本身是普通目录，不是符号链接或目录联接：

```text
FullName   : F:\Realworld\lol
Attributes : Directory
LinkType   :
Target     :
```

### 3.2 首次并发快照

17:16 复核时出现了其他任务创建的空目录：

```text
F:\Realworld\lol\
└── outputs\
    └── tmp\
        └── vision_audit\
```

当时计数为 3 个目录、0 个文件。该变化发生在本任务执行任何文件写入之前，未被本任务改动。

### 3.3 最终并发快照（2026-08-25T17:21:08.7458126+08:00）

其他任务继续在各自临时域写入。为避免读取或改动其他 Agent 的具体工作内容，本任务只统计元数据：

```text
outputs\tmp\game_assets\   17 files, 2 child directories, 74,252,594 bytes
outputs\tmp\reverse\        2 files, 2 child directories,        236 bytes
outputs\tmp\vision_audit\   0 files, 0 child directories,          0 bytes
outputs\tmp\wgc_probe\     52 files, 26 child directories,   300,222 bytes
```

`game_assets` 的最近写入时间在枚举期间推进到 17:21:10，证明该快照是并发活动中的时点数据，不是稳定的仓库基线。根目录直接条目仍然只有 `outputs\`，`outputs\` 之外为 0 条目，因此工程源码/构建/测试现状没有被这些输出域活动改变。

### 3.4 本报告写入后的本任务产物

本任务只新增：

```text
outputs\wave1_repo_env.md
```

本任务没有使用 `outputs\tmp\repo_env\`，因为所有取证结果可直接从只读命令输出形成报告，无需临时文件。

## 4. AGENTS.md 与仓库约束

执行：

```powershell
rg --files -uu -g 'AGENTS.md' -g '!.git/**'
```

结果：无输出，退出码 1。审计范围内不存在 `AGENTS.md`，所以没有额外的仓库级代理指令可加载。

## 5. Git 状态

### 5.1 事实

- `F:\Realworld\lol\.git` 不存在。
- 当前路径及其父级没有可供 Git 识别的仓库元数据。
- 没有分支、HEAD、提交、远端、索引或暂存区可报告。
- Git clean/dirty：**不适用（not a repository）**。
- “未提交变更”：**无法采用 Git 语义判断**；但审计起点工作区本身是 0 文件，因此没有已有文件可作为工作区变化或复用资产。

### 5.2 关键输出

```text
dotgit_exists=False
fatal: not a git repository (or any of the parent directories): .git
fatal: not a git repository (or any of the parent directories): .git
```

命令：

```powershell
Test-Path -LiteralPath '.git'
git -C 'F:\Realworld\lol' rev-parse --is-inside-work-tree
git -C 'F:\Realworld\lol' status --short --branch --untracked-files=all
```

## 6. 语言、构建系统、依赖与测试

### 6.1 检测结果

以下工程签名均为 0：

- C/C++：`*.c`、`*.cc`、`*.cpp`、`*.cxx`、`*.h`、`*.hpp`
- CMake/原生构建：`CMakeLists.txt`、`CMakePresets.json`、`CMakeUserPresets.json`、`meson.build`、`Makefile`
- Visual Studio/.NET：`*.sln`、`*.vcxproj`、`*.csproj`、`*.fsproj`
- Python：`*.py`、`pyproject.toml`、`requirements*.txt`、`Pipfile`、`poetry.lock`
- 其他常见栈：`package.json`、`Cargo.toml`、`go.mod`、`*.rs`、`*.go`、`*.js`、`*.ts`
- 文档：`*.md`（审计起点为 0；本报告写入后不再为 0）

对应扫描命令只输出了：

```text
signature_matches_complete
```

即没有任何模式命中。

### 6.2 可复用资产

- 可复用源代码：无。
- 可复用构建配置：无。
- 可复用测试：无。
- 可复用脚本或自动化：无。
- 可复用文档/设计：无。
- 已安装工具链可复用，详见下一节。

### 6.3 测试现状

仓库内没有测试源码、测试清单、测试运行器配置或 CI 配置。PATH 上存在 CTest 3.31.5，但这只是环境能力，不代表工程已有测试。

## 7. 主机与工具链实测

### 7.1 主机

```text
OS                  : Microsoft Windows NT 10.0.26200.0
OSArchitecture      : X64
ProcessArchitecture : X64
PowerShell          : 7.6.4
```

### 7.2 Git

- PATH：`D:\Env\Git\cmd\git.exe`
- 另一个 PATH 命中：`C:\Users\zmycml\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe`
- 实际解析版本：`git version 2.53.0.windows.2`

### 7.3 Visual Studio / MSVC / MSBuild

- Visual Studio：Enterprise 2022，产品显示版本 `17.14.29 (March 2026)`，安装版本 `17.14.37111.16`。
- 安装状态：`isComplete=true`、`isLaunchable=true`、`isRebootRequired=false`。
- VS 安装根：`D:\Downloads\VisualStudio\Enterprise`
- VC 工具集目录：`14.44.35207`
- x64 编译器：`D:\Downloads\VisualStudio\Enterprise\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64\cl.exe`
- `cl.exe` 实测版本：`用于 x64 的 Microsoft (R) C/C++ 优化编译器 19.44.35225 版`
- MSBuild：`17.14.40.60911`
- 当前 shell 的 `cl`：PATH 上未找到。
- 当前 shell 的 VS 环境变量 `VSCMD_VER`、`VisualStudioVersion`：均未设置。

结论：MSVC 已安装且可执行，但当前普通 shell 未激活 Visual Studio 开发环境。使用 CMake 时应显式选择 MSVC 工具链/VS 环境，不能假设裸 `cl` 可用。

### 7.4 PATH 上的其他 C/C++ 编译器

- `clang`：19.1.7，目标 `x86_64-w64-windows-gnu`，路径 `D:\Env\MinGW\bin\clang.exe`
- `clang-cl`：19.1.7，目标 `x86_64-pc-windows-msvc`，路径 `D:\Env\MinGW\bin\clang-cl.exe`
- `gcc`：14.2.0，MinGW-W64 `x86_64-ucrt-posix-seh`，路径 `D:\Env\MinGW\bin\gcc.exe`
- `g++`：14.2.0，MinGW-W64 `x86_64-ucrt-posix-seh`，路径 `D:\Env\MinGW\bin\g++.exe`
- `CC`、`CXX`：均未设置。

### 7.5 CMake / Ninja / CTest

PATH 解析结果：

- CMake：`D:\Env\MinGW\bin\cmake.exe`，版本 `3.31.5`
- Ninja：`D:\Env\MinGW\bin\ninja.exe`，版本 `1.12.1`
- CTest：`D:\Env\MinGW\bin\ctest.exe`，版本 `3.31.5`

Visual Studio 自带结果：

- CMake：`D:\Downloads\VisualStudio\Enterprise\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe`，版本 `3.31.6-msvc6`
- Ninja：`D:\Downloads\VisualStudio\Enterprise\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe`，版本 `1.12.1`

当前环境覆盖：

```text
CMAKE_GENERATOR=<unset>
CMAKE_TOOLCHAIN_FILE=<unset>
```

结论：CMake/Ninja 均存在，但同时存在 PATH 版和 VS 自带版。Phase 1 应在 preset/bootstrap 中固定所用工具链和生成器，避免不同入口选择不同 CMake。

### 7.6 Python

可用入口：

- `python` → `D:\Env\Python311\python.exe`
- 版本：CPython `3.11.6`，64 位，`MSC v.1935 64 bit (AMD64)`
- `py -3.11 --version` → `Python 3.11.6`，退出码 0

不可用/异常入口：

- `py -0p` 将 3.12 标成默认：`-V:3.12 * D:\Env\Python312\python.exe`
- `D:\Env\Python312\python.exe` 实测不存在。
- `py -3.12 --version` 失败，退出码 101。
- `py --version` 因默认 3.12 路径缺失而失败，退出码 101。
- `python3` 解析到 `C:\Users\zmycml\AppData\Local\Microsoft\WindowsApps\python3.exe`，执行时提示从 Microsoft Store 安装，退出码 9009，不能作为项目解释器。

结论：自动化脚本应显式使用 `python` 或 `py -3.11`，不得依赖裸 `py`、`py -3.12` 或 `python3`。

## 8. 技术栈建议（建议，不是现有状态）

仓库没有历史包袱，建议以已验证的本机能力建立最小、可复现的 Windows 原生工程基线：

1. 主语言使用 C++20；构建描述使用 CMake；本地生成器使用 Ninja。
2. 主编译器优先 MSVC x64 19.44.35225；将 clang-cl 19.1.7 保留为后续交叉编译/告警验证选项。不要默认采用 MinGW GCC 作为 Windows 原生主链路。
3. 使用 `CMakePresets.json` 固定架构、生成器和构建目录，并在 bootstrap 文档中明确如何激活 MSVC 环境；不要依赖当前 shell 的隐式 PATH。
4. 通过 CTest 接入测试。仓库当前没有测试框架，应在 Phase 1 选定后显式落依赖策略，不能假定已有框架。
5. Python 仅用于辅助脚本时固定为 3.11，并在脚本入口或文档中明确 `python`/`py -3.11`。
6. 在写实现前先初始化 Git、提交空基线/工程骨架并配置忽略规则，以便后续准确区分用户文件与 Agent 变更。

选择依据仅为本次实测到的本机工具链与“空仓库”事实；本报告没有对 WGC API、SDK、游戏安装或运行时能力作任何判断。

## 9. 阻塞项与风险

### 9.1 当前阻塞项

1. **没有仓库内容。** 无需求文档、源代码、构建文件或测试，无法基于“已有实现”继续拆分 Phase 1；下一阶段只能在确认项目目标后建立新基线。
2. **没有 Git 元数据。** 无法保留/比较历史，也无法用 Git 判定后续变更归属。开始实现前需要初始化或恢复正确仓库。
3. **MSVC 未进入当前 PATH。** 直接执行 `cl` 会失败；需要激活 VS 开发环境或在 CMake preset/bootstrap 中显式配置。
4. **Python launcher 默认项损坏。** 裸 `py` 指向不存在的 3.12，自动化若使用该入口会立即失败；可用入口是 `python` 或 `py -3.11`。

### 9.2 协作风险

- 审计期间存在并发任务写入 `outputs\tmp\vision_audit\`。后续 Agent 必须维持各自独立输出路径，并在写前复核目标是否已存在，避免覆盖。
- 同时安装两套 CMake，版本分别为 3.31.5 与 3.31.6-msvc6。当前 PATH 解析到 3.31.5，Visual Studio 内置入口解析到 3.31.6-msvc6；Phase 1 必须固定入口，避免 IDE 与终端使用不同二进制。

## 10. 可复现命令与关键输出

以下命令均以 `F:\Realworld\lol` 为工作目录；均为只读或版本查询。

### 10.1 工作区与文件树

```powershell
Get-Location
Get-ChildItem -Force
rg --files -uu -g '!.git/**'

$root = (Resolve-Path -LiteralPath '.').Path
$items = @(Get-ChildItem -LiteralPath $root -Force -Recurse -ErrorAction Stop)
$files = @($items | Where-Object { -not $_.PSIsContainer })
$dirs = @($items | Where-Object { $_.PSIsContainer })
[pscustomobject]@{
  Root = $root
  TotalEntries = $items.Count
  Files = $files.Count
  Directories = $dirs.Count
  Bytes = (($files | Measure-Object -Property Length -Sum).Sum ?? 0)
}
```

起点关键输出：`TotalEntries=0; Files=0; Directories=0; Bytes=0`。

### 10.2 Git

```powershell
Test-Path -LiteralPath '.git'
git -C 'F:\Realworld\lol' rev-parse --is-inside-work-tree
git -C 'F:\Realworld\lol' status --short --branch --untracked-files=all
```

关键输出：`False`；两条 Git 命令均为 `fatal: not a git repository (or any of the parent directories): .git`。

### 10.3 PATH 命令发现

```powershell
$names = 'git','cl','clang-cl','clang','gcc','g++','cmake','ninja','python','python3','py'
foreach ($name in $names) {
  Get-Command $name -All -ErrorAction SilentlyContinue
}
```

关键结果：`cl` 不在 PATH；其余上述命令有命中，但 `python3` 只是 Store alias，`py` 的默认解释器登记失效。

### 10.4 版本

```powershell
git --version
cmake --version
ninja --version
ctest --version
python --version
python3 --version
python -c "import platform,sys; print(sys.executable); print(sys.version); print(platform.architecture()[0])"
py -0p
py --version
py -3.11 --version
py -3.12 --version
clang --version
clang-cl --version
gcc --version
g++ --version
```

### 10.5 Visual Studio 与 MSVC

```powershell
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
& $vswhere -all -products '*' -format json -utf8

$vs = & $vswhere -latest -products '*' `
  -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
  -property installationPath
$toolset = Get-ChildItem -LiteralPath (Join-Path $vs 'VC\Tools\MSVC') -Directory |
  Sort-Object { [version]$_.Name } -Descending |
  Select-Object -First 1
$cl = Join-Path $toolset.FullName 'bin\Hostx64\x64\cl.exe'
& $cl
```

关键输出：工具集 `14.44.35207`；x64 `cl.exe` 版本 `19.44.35225`。

## 11. 写域合规确认

- 本任务没有编辑 `src`、`scripts`、`result`、`stage*` 或任何其他 Agent 报告。
- 本任务没有在 `F:\Realworld\lol` 之外创建、修改或复制文件。
- 本任务唯一新增文件是 `F:\Realworld\lol\outputs\wave1_repo_env.md`。
- `outputs\tmp\vision_audit\`、`game_assets\`、`reverse\`、`wgc_probe\` 是审计期间观察到的其他任务临时域；本任务只读取目录元数据，未修改其内容。
- 本任务未创建临时文件；授权的 `outputs\tmp\repo_env\` 保持未使用。

交付后可用以下只读命令复核全部文件与目录：

```powershell
$root = (Resolve-Path -LiteralPath '.').Path
Get-ChildItem -LiteralPath $root -Force -Recurse |
  Sort-Object FullName |
  Select-Object Mode,Length,CreationTime,LastWriteTime,
    @{N='RelativePath';E={$_.FullName.Substring($root.Length + 1)}}
```

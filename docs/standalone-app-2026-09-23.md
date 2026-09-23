# 最新引擎本地接入与独立应用包

本地工作分支：`codex/standalone-app`，基于 `main@56c01bb`。本轮通过独立分支交付，尚未合并到 `main`。
`main` 是精简发布仓库，因此没有将源码分支的整个目录树 merge 进来；本地从
`fix/death-force-ocr-engine@1e43732` 编译引擎并替换发布 EXE，Python 保留当前 main 的修复。

## 引擎

- 使用 MSVC 2022、Windows SDK、Release 配置本地编译，构建成功。
- `session_runtime_test`、`augment_frame_processor_test` 通过，覆盖同轮冲突的
  `offer_round_conflict` 返回值；Python 已有对应刷新重启回归测试。
- 新 EXE SHA256：`79274834a11bd48ae543e23737801d792100aa27520b261492c45bc960e4c1f1`。
- 来源与哈希写入根目录 `BUILD.txt`。旧 EXE 仅备份在本地 `outputs/local-build/previous-engine.exe`。

扩大运行 14 项 C++ 测试，10 项通过、4 项失败，不能宣称全套通过：

| 失败项 | 排查结果 |
| --- | --- |
| detector_test | 所需 `data/dataset/augment_offers/real`、`outputs/phase2_emergency_real` 实机图片不在该检出中 |
| selected_card_detector_test | 所需实机图片、`outputs/tmp/hud_selection_audit` 样本缺失 |
| cli_test | 源码约束只允许 `SW_SHOWNOACTIVATE`，把原有 `ShowWindow(console, SW_HIDE)` 也判为激活窗口 |
| runtime_paths_test | 仍期待 245 个 icon 模板，而当前实现显式设为 0；旧发布 EXE 同样失败 |

上述测试和相关检测实现不在 `210de16..1e43732` 的修改中。本轮未修改这些旧测试或
用合成图片替代缺失的实机样本。另行运行 Windows 中文 OCR smoke test，通过；它使用空白合成图，
只证明 OCR 后端可用，不代表真实游戏识别准确率。

Python 全套：258 项通过。包内 EXE 还通过真实 `--help` 子进程启动验证。
完整对局、死亡/复活与刷新场景仍需实机联调。

## 交付结构

`outputs/local-build/dist/GameBuddy-Windows-x64.zip` 解压后：

```text
GameBuddy/
  GameBuddy.exe
  OfflineSpeechWorker.exe
  _internal/                 Python、Tcl/Tk、VC 运行库、语音库、引擎和资料
  licenses/
  使用说明.txt
  BUILD-DEPENDENCIES.txt
  PACKAGE-SHA256.json
```

普通用户双击 `GameBuddy.exe` 即可。无需 Git、Python、pip、LFS，也无需首次启动联网安装依赖。
语音适配器在打包状态下改用同目录的 `OfflineSpeechWorker.exe`，避免把主程序 EXE 当 Python 解释器。
打包采用 PyInstaller onedir，模型随目录保留，不在每次启动时解压到临时目录。
资料依据：[PyInstaller 打包机制](https://pyinstaller.org/en/stable/operating-mode.html)、
[打包运行路径](https://pyinstaller.org/en/stable/runtime-information.html)。

用户配置、日志和历史仍写入 `%LOCALAPPDATA%/LoLRecognitionOverlay`。
构建按目录白名单收集资源，不含本机 `overlay.json`、Token、`txtt.txt` 或游戏安装路径。
每个发布文件和 ZIP 都生成 SHA256。构建工具在独立 venv 中安装并固定版本。

## 构建与检查

构建机需 Python 3.11 x64 和完整 LFS 模型；只做发布包无需安装 MSVC。
需要重新编译识别引擎时，才在引擎源码检出中使用 `scripts/build.ps1`。

```powershell
./scripts/package_recognition_overlay.ps1
py -3.11 -B scripts/packaging/test_portable_archive.py `
  outputs/local-build/dist/GameBuddy-Windows-x64.zip `
  outputs/local-build/portable-clean-environment.json
```

构建脚本会执行包内自检：资源校验、Windows 中文 OCR 能力、真实 Tk/动画窗口初始化、
引擎进程启动、实际模型合成、三段固定缓存读取，以及主程序与语音 EXE 的 ready/close 协议。
自检不调用在线 Agent，不播放音频。

异地解压检查将 ZIP 解压到仓库之外，逐文件校验，移除 Python/虚拟环境变量，
PATH 仅保留 Windows System32，并为 LOCALAPPDATA/APPDATA 使用全新空目录。
这验证打包产物没有依赖本机 Python 配置或仓库路径；不等同于另一台全新 Windows 的验收。

另行运行了显式声卡测试 `scripts/packaging/test_packaged_playback.py`：
固定胜利句和动态测试句均返回 started/finished，无 error；worker ready 约 0.797 秒。
播放协议成功不等同于用户确认音色与听感。

本地证据在 `outputs/local-build/`：`engine-build.log`、`engine-tests.log`、
`baseline-runtime-test.log`、`python-tests.log`、`package-self-test.json`、
`portable-clean-environment.json`、`packaged-playback.json`。

## 尚未完成的发布条件

- 此包是自带环境的便携应用，不是 Windows Sandbox/虚拟机，不做系统权限隔离。
- 要求 Windows 10/11 x64 和简体中文 OCR 系统组件；诊断会报告组件缺失，不会自动修改系统功能。
- Agent 的服务地址和凭据必须由用户自行配置；未配置时保留本地推荐回退。
- 尚未制作安装器或代码签名；未上传 Release；ZIP 是本地构建产物，不随 Git 提交。
- 尚未在另一台全新 Windows、实际完整游戏中验收。

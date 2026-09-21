# Hex Helper

这是可直接运行的精简版 GameBuddy 小猫 Overlay。

## 运行环境

- 64 位 Windows
- Python 3.11
- Visual C++ 2015–2022 x64 运行库

## 启动

双击根目录的 `一键启动小猫.cmd`。

启动器会调用内置视觉引擎和本地 Python 代码，不会修改 PowerShell 的全局执行策略。
英雄联盟客户端启动后，小猫会读取本机 LCU、Live Client 数据并识别屏幕上的海克斯三选一。

## 小猫交互

- 右键点击小猫会显示仅含“退出”的菜单；选择后立即关闭 GameBuddy。
- 左键短按小猫仍会在可重试时重新识别；按住 500ms 后可拖动整个 Overlay。
- 拖动时小猫保持在当前显示器内，位置只在本次运行中有效，重启后恢复默认位置。
- 小猫会随等待、打招呼、识别、观察、推荐成功和识别失败切换循环动画。
- 动画资源缺失或损坏时自动使用 `assets/gamebuddy-cat.png`，不影响识别与推荐。

Windows 实机验收步骤见 `docs/windows-gamebuddy-acceptance.md`。

## 诊断

双击 `打开诊断日志.cmd` 可打开运行日志目录。

运行时产生的日志、选择记录和视觉工作区位于：

```text
%LOCALAPPDATA%\LoLRecognitionOverlay\
```

## 目录说明

- `assets/`：小猫界面图片
- `data/`：英雄、海克斯及推荐数据
- `scripts/`：启动脚本与 Overlay 运行代码
- `outputs/tmp/build/bin/lol_augment_assistant.exe`：Windows x64 视觉引擎
- `BUILD.txt`：视觉引擎构建时间和 SHA-256

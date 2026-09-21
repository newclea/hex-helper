# Hex Helper

这是可直接运行的精简版 GameBuddy 小猫 Overlay。

## 运行环境

- 64 位 Windows
- Python 3.11
- Visual C++ 2015–2022 x64 运行库

## 启动

双击根目录的 `一键启动小猫.cmd`。

启动器会调用内置视觉引擎和本地 Python 代码，不会修改 PowerShell 的全局执行策略。
英雄联盟客户端启动后，小猫会读取本机 LCU 和 Live Client 数据。
小猫随后会识别屏幕上的海克斯三选一。

## 小猫交互

- 右键点击小猫会显示仅含“退出”的菜单；选择后立即关闭 GameBuddy。
- 左键短按小猫仍会在可重试时重新识别；按住 500ms 后可拖动整个 Overlay。
- 拖动使用虚拟桌面绝对坐标和当前显示器的实际工作区，支持连续跨屏和负坐标。
- 小猫在每块显示器内保持完整可见；位置只在本次运行中有效。
- 短文本气泡按内容收缩，较长文本自然换行，有效文本宽度最大约 780px。
- 小猫靠近屏幕四边或四角时，气泡自动放到相反方向，不会改变小猫位置。
- 五种参考姿态用于等待、启动挥手、推荐指向、未识别和抓头。
- OCR 识别与长按拖动共用抓头姿态；各状态约每 3 秒自然眨眼一次。
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

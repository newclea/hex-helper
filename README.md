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

- 右键点击小猫会显示动态的“开启语音”或“关闭语音”，以及“退出”。
- 语音默认开启；切换后会保存，下次启动继续使用上次的设置。
- 左键短按小猫仍会在可重试时重新识别；按住 500ms 后可拖动整个 Overlay。
- 拖动使用虚拟桌面绝对坐标和当前显示器的实际工作区，支持连续跨屏和负坐标。
- 小猫在每块显示器内保持完整可见；位置只在本次运行中有效。
- 短文本气泡按内容收缩，较长文本自然换行，有效文本宽度最大约 780px。
- 小猫靠近屏幕四边或四角时，气泡自动放到相反方向，不会改变小猫位置。
- 五种参考姿态用于等待、启动挥手、推荐指向、未识别和抓头。
- OCR 识别与长按拖动共用抓头姿态；各状态约每 3 秒自然眨眼一次。
- 动画资源缺失或损坏时自动使用 `assets/gamebuddy-cat.png`，不影响识别与推荐。

Windows 实机验收步骤见 `docs/windows-gamebuddy-acceptance.md`。

## 语音陪伴

- Windows 内置 `System.Speech` 会播报适合听取的简短摘要，不会逐字朗读整段气泡。
- 推荐和关键错误使用高优先级；高优先级消息可以打断正在播放的低优先级陪伴消息。
- OCR 持续时间严格超过 2 秒时播报一次识别进度；低优先级陪伴消息至少间隔 60 秒。
- 重复或过期消息不会播报。
- 语音组件不可用时会静默停用，不影响启动、识别、推荐和退出。
- 退出 GameBuddy 时会停止播报并关闭常驻语音进程，不应遗留 PowerShell worker。

可在 `%LOCALAPPDATA%\LoLRecognitionOverlay\overlay.json` 中配置语音：

```json
{
  "league_root": "E:\\WeGameApps\\英雄联盟（含经典模式）",
  "voice_enabled": true,
  "voice_name": "Microsoft Xiaoxiao Online (Natural) - Chinese (Mainland)"
}
```

`voice_enabled` 控制是否启用语音，缺省为 `true`；`voice_name` 可指定已安装的 Windows 语音。
程序依次选择指定语音、已安装的 `zh-CN` 女声、Windows 默认语音。
更新语音设置不会删除 `league_root`。

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

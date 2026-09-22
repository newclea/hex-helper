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

FP32 模型通过 Git LFS 存储。首次克隆或更新时需安装 Git LFS，并在仓库运行
`git lfs install`、`git lfs pull`，再启动小猫。直接下载源码 ZIP 可能只包含模型指针。

- 项目内置 sherpa-onnx 与 MeloTTS Chinese 离线语音，不使用 Windows `System.Speech`
  或电脑上已安装的语音。
- 首次启动会校验内置资源，并从本地 wheelhouse 准备专用运行目录；整个过程不访问网络。
- 动态语音使用 MeloTTS FP32、8 个 CPU 线程；13900HX 本机样本平均约 13.3 字/秒。
  缓存欢迎语先播放，模型同时在后台加载；缓存缺失时需等待模型加载并重新合成。
- worker 在主线程初始化 numpy 和音频依赖；欢迎语和胜负固定句预先合成为
  `assets/speech/fixed/` 下的 WAV，跨启动复用并整段连续播放。
- 固定音频按模型内容、文案及合成参数匹配；缺失或损坏时回退合成并尝试补回缓存。
  修改固定文案后，可在已配置语音运行目录的 Python 环境运行
  `scripts/product/prepare_fixed_speech.py` 重新生成；该命令不会播放声音。
- 启动时播报悠米欢迎语；选英雄时播报与气泡一致的 1～3 个短英雄名。
- 离线依赖包含 numpy，支持选人、赛果等新文本的流式合成；旧语音运行目录会自动升级。
- 海克斯选择时优先播报 Agent 生成的话术，Agent 不可用时回退本地推荐摘要。
- 赛后从 LCU 获取明确胜负，每个 `gameId` 最多播报一次；结果未知时不猜测。
- 推荐和关键错误使用高优先级；高优先级消息可以打断正在播放的低优先级陪伴消息。
- OCR 持续时间严格超过 2 秒时播报一次识别进度；低优先级陪伴消息至少间隔 60 秒。
- 重复或过期消息不会播报。
- 语音资源、模型或音频设备不可用时会静默停用语音，不影响启动、识别、
  推荐和退出。
- 退出 GameBuddy 时会停止播报并关闭常驻 Python 语音 worker。

可在 `%LOCALAPPDATA%\LoLRecognitionOverlay\overlay.json` 中配置语音：

```json
{
  "league_root": "E:\\WeGameApps\\英雄联盟（含经典模式）",
  "voice_enabled": true
}
```

`voice_enabled` 控制是否启用语音，缺省为 `true`。旧配置中的 `voice_name`
会被保留但不再使用；更新语音设置不会删除 `league_root`。

## Agent 文案能力测试

当前版本会在每轮海克斯推荐出现时后台调用一次 Agent，并把当前英雄、三张候选、
已选海克斯和推荐结果作为上下文。Agent 请求失败时使用本地推荐摘要，
不影响气泡、OCR 和推荐流程。受控测试期间，可在测试机的
`%LOCALAPPDATA%\LoLRecognitionOverlay\overlay.json` 中增加：

```json
{
  "agent": {
    "provider": "taiji_direct",
    "endpoint": "http://stream-server-online-openapi.turbotke.production.polaris:8080/openapi/app_platform/app_create",
    "forward_service": "hyaide-application-22837",
    "token": "仅存放在测试机本地的 Token",
    "timeout_seconds": 10
  }
}
```

真实 Token 不得写入仓库根目录的 `overlay.json`，也不得提交到 Git。然后在项目根目录运行：

```bat
set PYTHONPATH=scripts\product;scripts\recognition_overlay
py -3.11 -B scripts\recognition_overlay\agent_probe.py --prompt "请只回复：连接成功"
```

成功时输出包含 `"ok": true` 的 JSON；失败时返回非零退出码和稳定的 `error_code`。
诊断输出不会显示 Token。该命令不启动 Overlay、不播放语音，也不代表未来网关已经验证。
正常使用仍只需双击 `一键启动小猫.cmd`，无需额外启动 Agent 进程。

## 定向 Debug 模式

正常使用继续双击 `一键启动小猫.cmd`。排查赛果读取时双击
`一键启动小猫-Debug-赛果.cmd`，只额外记录 `DEBUG[game-result]`。排查完整语音链路时双击
`一键启动小猫-Debug-语音.cmd`，只额外记录 `DEBUG[speech]`，包括排队、Agent 请求、worker、
合成等待、音频流开始、完成、取消、超时和原生日志。排查同一等级刷新候选时双击
`一键启动小猫-Debug-海克斯刷新.cmd`，只额外记录 `DEBUG[hex-refresh]`，包括等级、轮次、
刷新前后候选、冲突判定和识别 worker 重启结果。这些入口都会启动相同的完整程序，
不会改变 LCU 请求、推荐、界面或语音行为。
诊断日志仍位于 `%LOCALAPPDATA%\LoLRecognitionOverlay\overlay.log`。

## 海克斯 OCR 时机

- 保留 3 级首轮海克斯判断。
- 每次从存活转为死亡时记录当时等级，并使用已实际确认的选择数决定是否扫描。
- 死亡等级低于 7 不扫描；7–10 级已选 2 次、11–14 级已选 3 次、
  15 级及以上已选 4 次时不再扫描。
- 死亡数据没有有效等级时本次不扫描；已真实检测到的三选一仍会完成 OCR。
- 应用刚启动的挥手阶段不显示失败姿态、OCR 错误气泡或错误语音。

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

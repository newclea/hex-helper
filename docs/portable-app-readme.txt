悠米 GameBuddy — Windows 64 位便携版

1. 将整个 ZIP 解压到普通文件夹。
2. 双击 GameBuddy.exe。请保留 _internal 和 OfflineSpeechWorker.exe。
3. 无需安装 Python、Git、Git LFS 或运行 pip，首次启动无需下载依赖。

运行条件：Windows 10/11 64 位，英雄联盟客户端与游戏安装在本机。
屏幕 OCR 使用 Windows OCR，系统需要具有游戏文字对应的语言识别能力。
离线语音、固定音频、推荐资料、识别引擎和动画均已内置。
Agent 在线服务仍需网络和用户自己的服务配置；未配置时使用本地推荐回退。

设置和日志保存在 %LOCALAPPDATA%\LoLRecognitionOverlay，更新程序不会覆盖它们。
程序包不包含开发者的 Token、游戏安装路径、选人历史和个人配置。
这是一份自带运行环境的应用包，不是 Windows 安全沙箱，不提供系统权限隔离。

离线诊断：GameBuddy.exe --self-test C:\某个可写目录\diagnostic.json
诊断验证资源、识别引擎启动、语音合成与 worker 通信，不播放声音，不调用在线 Agent。

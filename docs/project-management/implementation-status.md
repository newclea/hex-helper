# 实现状态

更新时间：2026-09-21

## 状态矩阵

| 范围 | 需求 ID | 代码完成 | 自动化通过 | Windows 实机 | 已推送发布 |
| --- | --- | --- | --- | --- | --- |
| 右键菜单与 500ms 拖动 | REQ-UI-001～002 | 是 | 是 | 待实机 | 否 |
| 多屏坐标与动态气泡 | REQ-UI-003～007 | 是 | 是 | 待实机 | 否 |
| 五姿态与动画 | REQ-ANI-001～004 | 是 | 是 | 待实机 | 否 |
| 语音配置、队列与策略 | REQ-VOICE-001、004～009 | 是 | 是 | 待实机 | 否 |
| Windows `System.Speech` | REQ-VOICE-002～003 | 是 | 契约测试通过 | 待实机 | 否 |
| 启动、双远端与范围隔离 | REQ-REL-001～003 | 代码已保留 | 是 | 待实机 | 否 |

“待实机”表示没有真实 Windows 验收结果，不能据此声明功能发布完成。

## Overlay 交互与气泡

- 主要文件：`scripts/product/overlay_interaction.py`、`scripts/product/rich_text_layout.py`、
  `scripts/product/cat_overlay.py`。
- 主要提交：`8ee0fbb`、`7937881`、`9bb0370`、`4439c69`。
- 已实现：候选猫中心选屏、实际工作区限制、负坐标原生移动、气泡方位选择、
  内容测量宽度和猫咪绝对锚定。
- 自动化覆盖：跨屏候选点、负坐标、四边四角、原生移动失败、动态宽度、
  孤字换行、字重和菜单行为。
- 未验证：真实多显示器跨屏连续性、任务栏工作区、Windows DPI 和游戏置顶环境。

## 动态形象

- 主要文件：`scripts/product/cat_animation.py`、`assets/gamebuddy/animations.json`、
  `assets/gamebuddy/*/*.png`。
- 主要提交：`d79b9c5`、`ee166a3`。
- 已实现：五种批准姿态、等待和选英雄复用、OCR 与拖动抓头、约 3 秒眨眼循环、
  局部动作像素约束和静态回退。
- 自动化覆盖：清单完整性、状态映射、拖动覆盖与恢复、眨眼周期、素材缺失回退。
- 未验证：Windows 透明窗口中的实际观感、动作自然度和不同缩放比例下的显示。

## 语音陪伴

- 主要文件：`scripts/product/speech.py`、`scripts/product/speech_policy.py`、
  `scripts/product/windows_speech.py`、`scripts/product/windows_speech_worker.ps1`、
  `scripts/recognition_overlay/overlay_config.py`、`scripts/recognition_overlay/app.py`。
- 主要提交：`47064ed`、`2504901`、`77ae559`、`4c9a586`、`610b865`、
  `23d561b`、`600864b`、`e885e84`。
- 已实现：配置保留、动态语音菜单、消息队列、摘要策略、OCR 延时提示、
  持久 PowerShell worker、打断、静音、关闭和失败隔离。
- 已预留：`SpeechMessage.source` 支持未来远端消息，但首版未接远端服务。
- 自动化覆盖：优先级、去重、过期、60 秒节流、并发失效、进程代际、
  worker 协议、配置持久化和应用生命周期。
- 未验证：真实 Windows `System.Speech` 初始化、中文女性语音选择、听感、
  播放中静音，以及退出后无残留 PowerShell 进程。

## 自动化快照

- 提交：`e885e848bce7dd2b8e206757c36573c6e00052c6`。
- 日期：2026-09-21。
- 命令：

```bash
PYTHONPATH=scripts/product:scripts/recognition_overlay \
  python3 -m unittest discover -s tests -v
```

- 结果：132 项通过，0 项失败，耗时约 0.96 秒。
- 边界：该结果来自 macOS 上的单元和协议测试，不等于 Windows 实机或真实语音播放验收。

后续代码变更必须产生新的测试快照；本节的 132 项仅代表上述提交。

## 发布快照

- 文档创建时本地代码提交：`e885e848bce7dd2b8e206757c36573c6e00052c6`。
- 文档创建时 Woa Git `feature/cat-ui-recommendation`：`60111f4e0abe3ef7b68954cc3041ca7971e5033a`。
- 文档创建时 GitHub `main`：`60111f4e0abe3ef7b68954cc3041ca7971e5033a`。
- 结论：本轮 Overlay、动画和语音提交在该快照时尚未推送。

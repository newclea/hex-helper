# GameBuddy 离线语音与死亡 OCR 时机设计

日期：2026-09-22  
需求提出人：恒祥

## 1. 背景与目标

当前 GameBuddy 使用 Windows `System.Speech`。实机能够显示海克斯推荐气泡，
但除选英雄提示外，后续推荐可能没有声音。应用刚启动、尚未进入游戏时，
也可能被识别状态覆盖为失败姿态。

本次变更有三个目标：

1. 使用完全离线的开源中文语音替换 `System.Speech`，断网时仍能播报全部现有业务消息。
2. 启动挥手期间不展示或播报 OCR 失败，未检测到真实三选一前不进入 OCR 展示状态。
3. 记录每次死亡的等级，结合实际确认的海克斯选择数量，减少不必要的死亡 OCR。

本规格替换《GameBuddy Windows Overlay 修订与语音陪伴设计》的 Windows TTS 适配器部分，
不改变已经实现的语音消息、优先级、去重、右键开关和业务摘要策略。

## 2. 已确认的产品行为

### 2.1 离线语音

- 采用 sherpa-onnx 与 MeloTTS Chinese，运行期间不得访问网络。
- 不再调用 PowerShell、`System.Speech` 或已安装的 Windows 语音。
- 选英雄、OCR 进度、海克斯推荐、推荐装备及现有其他陪伴消息继续按当前策略播报。
- 每轮推荐都播报；即使推荐内容与上一轮相同，也不能被跨轮去重。
- 右键“开启语音”和“关闭语音”及其持久化行为保持不变。
- 语音不可用时只停用本次进程的语音，不能阻塞启动、OCR、推荐或退出。

### 2.2 启动状态

- 应用启动后的 3 秒挥手动画优先于业务动画。
- 挥手期间不显示失败姿态、OCR 错误气泡，也不播报 OCR 失败。
- 挥手结束且尚未进入游戏时回到等待姿态。
- 只有真实检测到海克斯三选一后，才允许展示 `ocr_reading`、`ocr_confirming`、
  `ocr_updating` 或 `ocr_error`。
- 启动状态门控只影响展示和语音，不能伪造或清除识别结果。

### 2.3 死亡 OCR 时机

保留现有 3 级首次海克斯判断。除此之外，
只在 `isDead` 从 `false` 变为 `true` 时记录本次死亡，
并使用死亡瞬间的等级和实际确认成功的选择数量决定是否允许死亡 OCR。

| 死亡瞬间等级 | 实际确认选择数量 `confirmed_count` | 死亡 OCR |
| --- | ---: | --- |
| 等级缺失 | 任意 | 不允许 |
| `< 7` | 任意 | 不允许 |
| `7–10` | `< 2` | 允许 |
| `7–10` | `>= 2` | 不允许 |
| `11–14` | `< 3` | 允许 |
| `11–14` | `>= 3` | 不允许 |
| `>= 15` | `< 4` | 允许 |
| `>= 15` | `>= 4` | 不允许 |

这里的“已选择次数”只使用 `confirmed_count`，不使用能够跨过历史缺口的 `completed_stage`。
同一次死亡首次事件缺少等级时，本次死亡固定为不允许 OCR；
后续补到等级也不重新开启。

已经真实检测并正在处理的三选一始终允许完成 OCR，不受死亡门控中断。

## 3. 离线语音架构

### 3.1 组件边界

保留现有 `SpeechMessage`、`SpeechQueue`、`SpeechService` 和 `CompanionSpeechPolicy`。
应用只把底层适配器由 `WindowsSpeechAdapter` 换成 `OfflineSpeechAdapter`。

新增常驻离线语音 worker，职责如下：

1. 启动后加载本地 sherpa-onnx 运行库和 MeloTTS Chinese 模型。
2. 通过标准输入接收 JSON Lines 命令。
3. 合成本地 PCM 音频并通过 sounddevice 播放。
4. 通过标准输出发送 `ready`、`started`、`finished` 和 `error` 事件。
5. 支持 `speak`、`cancel` 和 `close`，退出时不得遗留子进程或音频播放。

worker 与 OCR、推荐和 Tk 主线程隔离。模型加载、推理和播放均不得占用 UI 线程。
模型在 worker 生命周期内只加载一次，不为每条消息重复加载。

### 3.2 固定依赖

离线包固定使用以下组件：

- sherpa-onnx `1.13.8`；
- sherpa-onnx-core `1.13.8`；
- sounddevice `0.5.3`；
- Windows x64、CPython 3.11 对应的 CFFI 及 pycparser 依赖；
- `csukuangfj/vits-melo-tts-zh_en` 修订
  `a0d5c6a264c0ef92d70d8661d8cc502d79627cd6`；
- 上述模型中的 `model.int8.onnx`、tokens、lexicon、FST 和中文分词词典。

不包含约 170 MB 的 `model.onnx`，只使用约 53 MB 的 `model.int8.onnx`。
实现时必须为全部 vendored 文件生成并提交 SHA-256 清单；启动安装前校验清单。

### 3.3 启动与播放

第一次启动时，现有一键启动脚本从仓库内的 wheel 目录安装依赖到 GameBuddy 专用运行目录。
安装命令必须使用 `--no-index` 和明确的本地 `--find-links`，不得回退到网络源。
后续启动复用该运行目录。

`OfflineSpeechAdapter.start()` 启动 worker 并等待 `ready`。
模型加载失败、文件校验失败、音频设备不可用或协议异常时，
适配器返回不可用并记录具体原因。`SpeechService` 随后停止派发语音，但主应用继续运行。

旧 `overlay.json` 中的 `voice_name` 字段允许保留但不再读取；`voice_enabled` 语义不变。
首版不新增音色、语速或音量设置。

## 4. 启动状态门控

`AnimationStateController` 在 greeting 截止时间之前始终选择 `greeting`，拖动覆盖除外。
底层瞬时 failure view 不能提前结束挥手动画。

产品展示层继续以真实 `offer_visible` 作为 OCR 气泡入口。
未进入游戏或没有三选一检测证据时，即使视觉进程报告一般性错误，
也不把界面转换为 OCR 失败状态。

语音策略只消费最终可见 view。`bubble_visible` 为 false 时，不产生 OCR 进度或错误消息，
并清除尚未触发的 OCR 延迟提示。

## 5. 死亡状态与 OCR 门控

### 5.1 对局状态

识别模型新增当前对局内的死亡状态：

- 最近一次死亡序号；
- 最近一次死亡瞬间等级；
- 最近一次死亡是否允许 OCR。

状态只在存活到死亡的边沿更新。持续收到 `isDead=true` 不增加序号，不重新计算资格，
也不延长一场本来不符合条件的死亡 OCR。

新对局初始化和对局清理必须同时清空这些字段，不能继承上一局的等级或资格。

### 5.2 门控顺序

OCR 是否开放按以下优先级判断：

1. 已有真实三选一正在展示或处理时保持开放，直至本轮选择关闭。
2. 现有选择后的短暂确认扫描保持开放，避免破坏选择确认链路。
3. 第一次海克斯继续使用现有 3 级判断。
4. 其他死亡及其复活扫描窗口必须满足本次死亡资格表。
5. 其余情况关闭 OCR。

符合条件的死亡沿用现有死亡及复活扫描窗口。不符合条件的死亡不能通过 probe 或
`seconds_since_respawn` 间接重新开放 OCR。

每次死亡只记录一条 INFO 日志，包含对局标识、死亡序号、死亡等级、`confirmed_count`、
允许结果和原因。日志不进入用户气泡。

## 6. 资源分发与许可证

为了保证克隆后的项目在断网环境可启动，Windows CPython 3.11 wheels、模型、词典、FST
和许可证随仓库分发。任何单文件必须小于 GitHub 的 100 MB 单文件限制；
预计仓库增加约 75–100 MB。

第三方说明至少包含：

- sherpa-onnx 的 Apache-2.0 许可证和来源；
- MeloTTS Chinese 模型的 MIT 许可证、原始来源和转换模型来源；
- sounddevice、CFFI、pycparser 及其运行依赖的许可证和来源；
- 固定版本或修订、文件名和 SHA-256。

两个 Git 远端必须包含同一份源代码和离线资源。不得在应用启动时从 GitHub、Hugging Face
或其他地址下载模型或 wheel。

## 7. 代码范围

预计只修改或新增以下职责范围：

- `scripts/product/offline_speech.py`：worker 生命周期、JSON Lines 协议和适配器实现；
- `scripts/product/offline_speech_worker.py`：模型加载、合成和音频播放；
- `scripts/recognition_overlay/app.py`：接入离线适配器；
- `scripts/recognition_overlay/hexcore_gate.py`：纯死亡资格和 OCR 门控函数；
- `scripts/recognition_overlay/view_model.py`：记录死亡等级、资格并重置对局状态；
- `scripts/product/cat_animation.py`：启动 greeting 覆盖顺序；
- `scripts/run_recognition_overlay.ps1`：从本地 wheel 目录准备运行依赖；
- 离线模型、wheel、许可证、SHA-256 清单、README 和测试。

旧 Windows TTS 文件在新适配器通过测试后删除，避免继续携带 PowerShell 和 `System.Speech` 链路。
不修改推荐算法、OCR 文字识别算法、LCU 协议、气泡布局、拖动或动画素材。

所有新增或修改的方法不超过 80 行，单行不超过 120 个字符。
不做需求范围外的格式化、重命名、注释调整或重构。

## 8. 错误处理

- 离线资源缺失或 SHA-256 不匹配：记录具体文件，语音本次会话不可用，业务继续。
- 本地依赖安装失败：启动主应用，但语音本次会话不可用。
- worker 启动、模型加载、推理或播放失败：结束 worker，记录一次根因，业务继续。
- 音频播放超时：停止当前播放，继续处理仍有效的后续消息。
- 死亡等级缺失：本次死亡不允许 OCR，并记录原因。
- Live Client 重复发送死亡状态：不重复记录死亡，不重复延长 OCR。
- 已检测到真实三选一：即使死亡数据缺失，也允许完成当前 OCR。

## 9. 验证与验收

### 9.1 自动化测试

- 离线适配器的启动、连续播报、完成、失败、取消、超时和关闭协议测试；
- worker 模型路径、配置、音频调用和异常测试，测试中使用替身，不加载大模型；
- 现有语音队列和全部业务播报策略回归测试；
- 3、7、11、15 级边界及各 `confirmed_count` 组合的参数化测试；
- 死亡等级缺失、重复死亡帧、复活窗口和新对局重置测试；
- 真实 offer 与选择后确认扫描优先于死亡门控的测试；
- greeting 期间 failure view 不覆盖启动动画的测试；
- `bubble_visible=false` 时不产生 OCR 错误语音的测试；
- 全量现有测试通过，且所有修改行满足项目长度规则。

### 9.2 Windows x64 实机验收

1. 禁用网络后，在未安装 sherpa-onnx 的 Windows x64 电脑上首次双击启动。
2. 确认依赖只从本地 wheel 安装，应用可启动并听到选英雄语音。
3. 完成至少四轮海克斯选择，确认每轮推荐均播报，包括连续相同推荐。
4. 确认推荐装备及现有其他业务消息能够播报。
5. 在 3、7、11、15 级边界制造死亡，核对 OCR 是否符合资格表。
6. 确认启动挥手和未进入游戏阶段没有失败姿态、错误气泡或错误语音。
7. 关闭再开启语音，确认当前播放停止，重新开启后新消息可以播报。
8. 退出应用，确认没有遗留语音 worker 或持续音频。
9. 临时移走模型或禁用音频设备，确认日志明确且推荐功能继续工作。
10. 核对两个远端指向同一提交，并确认离线资源和许可证均已推送。

## 10. 完成标准

- 完全断网环境可首次准备依赖并播报中文。
- 不再启动 PowerShell 或调用 Windows `System.Speech`。
- 启动挥手阶段不显示或播报 OCR 失败。
- 死亡 OCR 严格使用死亡瞬间等级和 `confirmed_count` 判断。
- 真实三选一和已有选择确认链路不被新门控中断。
- 语音故障不影响启动、OCR、推荐和退出。
- 自动化测试与 Windows 实机验收均留下可审计结果。
- 实现提交成功推送到 WOA Git 和 GitHub，两个远端提交一致。

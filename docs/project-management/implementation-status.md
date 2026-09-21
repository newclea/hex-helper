# 实现状态

更新时间：2026-09-22

## 状态矩阵

| 范围 | 需求 ID | 代码完成 | 自动化通过 | Windows 实机 | 代码已推送 |
| --- | --- | --- | --- | --- | --- |
| 右键菜单与 500ms 拖动 | REQ-UI-001～002 | 是 | 是 | 待实机 | 是 |
| 多屏坐标与动态气泡 | REQ-UI-003～007 | 是 | 是 | 待实机 | 是 |
| 五姿态与动画 | REQ-ANI-001～004 | 是 | 是 | 待实机 | 是 |
| 语音配置、队列与策略 | REQ-VOICE-001、004～009 | 是 | 是 | 待实机 | 是 |
| 离线中文语音 | REQ-VOICE-002～003 | 是 | 是 | 未执行 | 否（待本轮推送） |
| 启动展示与死亡 OCR 门控 | 本轮规格 | 是 | 是 | 未执行 | 否（待本轮推送） |
| 启动、双远端与范围隔离 | REQ-REL-001～003 | 代码已保留 | 是 | 待实机 | 是 |

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

## 离线语音陪伴

- 主要文件：`scripts/product/speech.py`、`scripts/product/speech_policy.py`、
  `scripts/product/offline_speech.py`、`scripts/product/offline_speech_worker.py`、
  `scripts/product/offline_speech_assets.py`、
  `scripts/recognition_overlay/overlay_config.py`、`scripts/recognition_overlay/app.py`。
- 本轮主要提交：`6b43075`、`f376d9f`、`167c8ad`、`7f427a7`、`b8483fc`、
  `50c05d5`、`26dc873`、`d01337f`。
- 已实现：配置保留、动态语音菜单、消息队列、摘要策略、OCR 延时提示、
  常驻 Python worker、打断、静音、关闭和失败隔离。
- 固定版本：sherpa-onnx `1.13.8`、sherpa-onnx-core `1.13.8`、sounddevice `0.5.3`；
  MeloTTS Chinese 修订为 `a0d5c6a264c0ef92d70d8661d8cc502d79627cd6`。
- 已内置 Windows x64 / CPython 3.11 的五个 wheel、INT8 模型、词典、FST、
  SHA-256 清单和第三方许可说明。启动安装使用 `--no-index`。
- 已预留：`SpeechMessage.source` 支持未来远端消息，但首版未接远端服务。
- 自动化覆盖：优先级、去重、过期、60 秒节流、并发失效、进程代际、
  worker 协议、请求编号、资源校验、本地安装参数、配置持久化和应用生命周期。
- 未验证：Windows x64 断网首次安装、真实中文合成与播放、音频设备降级、
  播放中静音，以及退出后无残留 Python 语音 worker。

## 启动展示与死亡 OCR 时机

- 主要文件：`scripts/product/cat_animation.py`、`scripts/recognition_overlay/hexcore_gate.py`、
  `scripts/recognition_overlay/view_model.py`。
- 主要提交：`a1d1666`、`0844ba6`、`4505359`、`9f2b1fe`、`29cf10e`。
- 已实现：启动 3 秒挥手阶段优先于失败展示；死亡边沿记录瞬时等级，
  按 `confirmed_count` 和 7、11、15 级边界决定死亡/复活 OCR；缺少等级不开放 OCR。
- 保留：3 级首轮规则、真实三选一优先级和选择后确认扫描。
- 未验证：Windows 真实对局中的启动观感、死亡等级边界和重复死亡帧。

## 历史自动化快照

- 提交：`3a189260c985ab6ccbf7b94cdb8c342fcf226db0`。
- 日期：2026-09-21。
- 命令：

```bash
PYTHONPATH=scripts/product:scripts/recognition_overlay \
  python3 -m unittest discover -s tests -v
```

- 结果：134 项通过，0 项失败。
- 边界：该结果来自 macOS 上的单元和协议测试，不等于 Windows 实机或真实语音播放验收。

后续代码变更必须产生新的测试快照；本节的 134 项仅代表上述代码提交。

## 2026-09-22 自动化快照

- 受测生产代码提交：`d01337f1c5001399a86c73508718bc8279ffcd32`。
- 结果：173 项通过，0 项失败；`compileall`、动画 JSON、`git diff --check`
  和离线资源 SHA-256 校验通过。
- 离线包大小：93,088 KiB（约 90.9 MiB）；最大文件为 53,517,430 字节的
  `assets/speech/melo-tts-zh_en-int8/model.int8.onnx`。
- 边界：macOS 自动化只验证逻辑与协议，不等于 Windows x64 离线安装、
  真实音频播放或进程清理验收。
- 提交 `d01337f` 是本次测试覆盖的最新生产代码；其后的 `ea97393`
  和本记录提交只修改文档，没有更改运行逻辑或测试。

## 代码推送基线

- Woa Git `feature/cat-ui-recommendation` 已包含 `d45e2ad2e8c62f16da02983283573818d3b9919f`。
- GitHub `main` 已包含 `d45e2ad2e8c62f16da02983283573818d3b9919f`。
- 结论：两个远端已包含同一份 Overlay、动画和语音代码基线。
- 边界：Windows 多屏和真实语音仍待验收，代码推送不等于实机发布通过。

本轮离线语音和死亡 OCR 改动尚未推送；推送后需另行记录两个远端完整 SHA。

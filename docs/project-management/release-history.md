# 发布历史

更新时间：2026-09-22

本文件只记录已经核对的事实。没有 Windows 实机结果或远端提交证据时，不写“验收通过”或“发布完成”。

## 远端基线

| 日期 | Woa Git 分支 | GitHub 分支 | 核对提交 | Windows 验收 | 说明 |
| --- | --- | --- | --- | --- | --- |
| 2026-09-21 | `feature/cat-ui-recommendation` | `main` | `60111f4` | 未通过新需求验收 | 本轮修复前共同基线 |

该基线不包含 `8ee0fbb` 至 `3a18926` 的跨屏、气泡、动画修订和语音陪伴实现。
基线完整提交为 `60111f4e0abe3ef7b68954cc3041ca7971e5033a`。

## 本轮发布候选

| 项目 | 记录 |
| --- | --- |
| 代码提交 | `3a189260c985ab6ccbf7b94cdb8c342fcf226db0` |
| 首次双远端推送 HEAD | `d45e2ad2e8c62f16da02983283573818d3b9919f` |
| 自动化 | 134 项通过，0 项失败；仅代表上述代码提交的 macOS 测试快照 |
| Windows 多屏 | 待实机 |
| Windows 真实语音 | 待实机 |
| 最新候选包 | `hex-helper-windows-3a18926-candidate.zip` |
| 候选包 SHA-256 | `00be2f37a6b546e527c40404eb593733d614a7e9aad42b7715a167ebbd1d4d1e` |
| Woa Git 推送 | `feature/cat-ui-recommendation` 已推送至 `d45e2ad` |
| GitHub 推送 | `main` 已推送至 `d45e2ad` |

新候选包包含代码提交 `3a18926`，但不能替代 Windows 实机验收。

## 代码推送记录

| 日期 | Woa Git 分支 | GitHub 分支 | 共同提交 | Windows 验收 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 2026-09-21 | `feature/cat-ui-recommendation` | `main` | `d45e2ad` | 待实机 | 代码已同步，非正式验收发布 |

## 2026-09-22 离线语音与 OCR 时机候选

| 项目 | 记录 |
| --- | --- |
| 受测代码提交 | `0994f93d74548517eef5f636297f6919d49bd1fb` |
| 语音运行库 | sherpa-onnx `1.13.8`、sherpa-onnx-core `1.13.8`、sounddevice `0.5.3` |
| 语音模型 | MeloTTS Chinese `a0d5c6a264c0ef92d70d8661d8cc502d79627cd6`，只内置 `model.int8.onnx` |
| 离线资源 | Windows x64 / CPython 3.11 五个 wheel、模型、词典、FST、SHA-256 与许可说明 |
| 自动化 | 173 项通过，0 项失败；编译、JSON、差异空白和离线资源校验通过 |
| 离线包大小 | 93,088 KiB（约 90.9 MiB） |
| 最大内置文件 | `model.int8.onnx`，53,517,430 字节 |
| Windows x64 断网安装 | 未执行；当前验证主机为 macOS |
| Windows 真实音频播放 | 未执行；不以 mock 或 macOS 测试代替 |
| Windows 进程检查与清理 | 未执行 |
| Woa Git 推送 | 未执行 |
| GitHub 推送 | 未执行 |

本候选用内置离线语音替换了 Windows `System.Speech`，并增加启动挥手展示优先级、
基于死亡瞬间等级与 `confirmed_count` 的 OCR 门控。代码推送和 Windows 实机验收
尚未完成，不能标记为正式发布。

## 发布记录要求

每次正式候选发布必须记录：

1. 源提交完整 SHA 和工作树状态。
2. 候选包文件名、SHA-256、Windows 架构和生成时间。
3. 自动化命令、用例数及结果。
4. Windows 版本、缩放、显示器排列、音频设备和实机结论。
5. Woa Git 与 GitHub 的分支名和远端完整 SHA。
6. 两个远端、候选包源提交三者的一致性结论。

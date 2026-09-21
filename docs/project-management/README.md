# GameBuddy 项目管理

更新时间：2026-09-21

本目录是当前 GameBuddy Windows Overlay 迭代的状态入口。详细设计和实施步骤仍以
`docs/superpowers/` 下的规格与计划为准，本目录不复制其全文。

## 当前版本

| 项目 | 状态 |
| --- | --- |
| 本地分支 | `feature/cat-ui-recommendation` |
| 文档创建前代码 HEAD | `e885e848bce7dd2b8e206757c36573c6e00052c6` |
| 自动化验证 | 132 项通过，见 `implementation-status.md` |
| Windows 实机 | 多屏拖动、动画观感和真实 `System.Speech` 待验收 |
| 当前代码推送 | 文档创建时尚未推送，本地代码领先两个远端的 `60111f4` |
| 当前候选包 | 旧候选包已被本轮修复取代，尚未生成本轮候选包 |

## 进度概览

| 工作流 | 代码完成 | 自动化通过 | Windows 实机通过 | 已推送发布 |
| --- | --- | --- | --- | --- |
| Overlay 交互与气泡 | 是 | 是 | 待实机 | 否 |
| 动态形象 | 是 | 是 | 待实机 | 否 |
| 语音陪伴首版 | 是 | 是 | 待实机 | 否 |
| 本轮打包发布 | 不适用 | 不适用 | 待实机 | 否 |

以上状态是文档创建时的快照。“代码完成”不代表 Windows 实机验收或发布完成。

## 当前门槛

1. 在真实 Windows 多显示器环境完成 `regression-checklist.md` 的实机检查。
2. 验证已安装的中文女性语音、默认语音回退、静音和退出时的真实进程行为。
3. 实机通过后生成与最新提交一致的新候选包。
4. 推送后核对 Woa Git 分支和 GitHub `main` 指向同一提交。

## 文档索引

- [需求与验收标准](requirements.md)
- [实现状态](implementation-status.md)
- [回归清单](regression-checklist.md)
- [关键决策](decisions.md)
- [发布历史](release-history.md)
- [后续事项](backlog.md)
- [修订规格](../superpowers/specs/2026-09-21-gamebuddy-windows-overlay-voice-design.md)
- [Overlay 实施计划](../superpowers/plans/2026-09-21-gamebuddy-windows-overlay-revision.md)
- [语音实施计划](../superpowers/plans/2026-09-21-gamebuddy-voice-companion.md)

## 状态维护规则

- 每项需求使用稳定 ID，后续不得因排序变化而改号。
- 分别记录代码、自动化、Windows 实机和推送发布状态，不用一个“完成”概括四者。
- 自动化结果必须带提交号、日期和命令；失败或未运行时明确记录。
- 实机结果必须带环境、验收人、日期和证据；未执行时保持“待实机”。
- 发布记录只写已核对的提交、分支和包，不把计划中的推送写成成功。
- 新需求同步更新 `requirements.md`、`implementation-status.md` 和
  `regression-checklist.md`。

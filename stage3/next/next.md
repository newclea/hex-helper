# Phase 3 暂停交接

## 当前状态

Phase 3 已按用户要求暂停并收束。现有 Phase 2 Capture / Detector / OCR / Storage / Replay 未被修改；本轮没有生成在线采集器、真实爬取语料、融合知识库、SQLite 检索库或 Benchmark，禁止据此声称 Phase 3 已完成。

本轮实际完成：

- 仓库架构边界审计；
- Riot 官方、攻略/统计站、社区来源的可用性与合规调研；
- Phase 3 严格数据契约及四份 JSON Schema；
- 数据契约测试 22 项，全部通过。

## 已确认结论

1. Phase 3 应保持为独立的 Python 3.11 离线管线，并使用独立 SQLite；不要把网络采集或检索代码接入 Phase 2 的 C++ 捕获/识别链路、CLI 或 session.sqlite3。
2. 数据链路应固定为 `SourceRecord -> Claim -> KnowledgeRecord -> QueryResult`，所有最终结论必须能沿 claim 回溯到精确 source。
3. patch 只能来自页面显式证据；没有明确版本时必须为 `UNKNOWN`，不得按发布日期推断，也不得在融合阶段升级为当前版本。
4. 来源类型固定区分 `OFFICIAL / STATISTICAL / GUIDE / COMMUNITY / INFERENCE`；单一来源不能伪装为多来源共识，冲突必须保留。
5. Riot LoL 政策页面禁止展示 Augment 胜率，并把替玩家规定决定列为不获批准用例。面向玩家的输出只能解释多个选项的联动、玩法和装备影响。
6. 自动采集应 fail-closed：仅 HTTPS 精确 allowlist，先核对 robots/ToS，遇到 403、429、CAPTCHA、MIME/大小/跨域跳转异常立即停止；Reddit/YouTube 默认进入人工元数据流程。
7. Mobalytics 仅作人工 GUIDE 参考，不作 Mayhem 统计真值；调研发现其页面把普通 ARAM 数据与评级型 Mayhem 信息混用，并存在版本标记冲突。

## 已验证文件

规范化、可直接复验的最小工作区位于 `files/workspace_subset/`：

- `files/workspace_subset/scripts/phase3/contracts.py`
- `files/workspace_subset/scripts/phase3/__init__.py`
- `files/workspace_subset/data/knowledge/web/schema/*.schema.json`
- `files/workspace_subset/tests/phase3_contract/test_contracts.py`
- `files/workspace_subset/outputs/phase3_repo_architecture_audit.md`
- `files/workspace_subset/outputs/phase3_official_sources_research.md`
- `files/workspace_subset/outputs/phase3_official_sources.json`
- `files/workspace_subset/outputs/phase3_guide_stats_sources_research.md`
- `files/workspace_subset/outputs/phase3_community_sources_research.md`

完整 `outputs` 与 `scripts` 快照分别位于 `../outputs/` 和 `../scripts/`。

复验命令：

```powershell
Set-Location 'F:\Realworld\lol'
py -3.11 -B tests\phase3_contract\test_contracts.py -v
py -3.11 -B -m py_compile scripts\phase3\__init__.py scripts\phase3\contracts.py
```

复验结果：`Ran 22 tests`，`OK`；两个 Python 文件编译检查通过。

## 未完成项

- `--live` 显式启用的网页采集 CLI；
- source registry、robots/ToS 决策与限流实现；
- 原始页面 archive、元数据、URL/SHA 去重与增量抓取；
- Riot / ARAMMayhem / METAsrc 等页面解析器；
- COMMUNITY 人工元数据导入器；
- Claim 抽取、跨来源融合与冲突持久化；
- 独立 SQLite 构建器和只读本地检索 CLI；
- 当前 patch 覆盖率、组合数、装备联动数、UNKNOWN 比例与检索延迟 Benchmark；
- Phase 3 打包、完整文档和最终 Exp。

注意：停止时采集器 Agent 尚未写入指定文件，因此不存在值得保留的半成品采集器。社区调研报告文字提到一个 JSON companion，但工作区中实际没有该文件；后续不得假设它存在。

## 推荐续接路线

1. 先以现有 contracts/schema 为不可绕过的边界，实现 `collect_web.py` 和 `source_registry.json`，只用本地 fixture 测试安全策略，不立即大规模联网。
2. 对 Riot 官方页、ARAMMayhem.com 与 METAsrc 做逐站 robots/ToS 复核；只有明确通过的站点才进入自动 allowlist，其余进入人工来源队列。
3. 实现页面 archive 与解析器，先形成少量可人工核验的 `SourceRecord + Claim`；不要先追求英雄覆盖数量。
4. 用明确版本的 Riot 交互修复/机制条目建立第一批高可信 claim；把未显式标版本的组合攻略保持为 `UNKNOWN`。
5. 实现融合器和独立 SQLite，只读查询输入为 champion、三个 offered augments、已选 augments、patch；返回三项并列解释、冲突和来源，不返回胜率或唯一选择。
6. 最后运行离线 Benchmark，并如实报告来源数、英雄数、海克斯数、组合数、装备联动数、当前 patch 覆盖率、UNKNOWN/旧版本比例、冲突案例及平均/P95 检索延迟。

建议第一批人工验证英雄仍可用：Veigar、Brand、Vayne、Varus、Yasuo；这是工作队列建议，不是知识结论。

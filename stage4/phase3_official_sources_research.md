# Wave1-R2：官方固定页面事实表

## 核验范围

- 核验日期：`2026-08-26`
- 核验时区：`Asia/Shanghai`
- 页面数量：`3`
- 来源限制：仅下表中的 3 个 Riot 官方固定 URL；未扩展搜索，未使用非官方来源。
- 日期规则：补丁页采用页面显示的发布时间；政策页未显示可核验的发布日期，因此记为 `null`，不以页脚版权年份代替发布日期。

## 官方来源事实表

| url | title | publish_date | crawl_date | patch | summary | type |
|---|---|---|---|---|---|---|
| https://www.leagueoflegends.com/en-gb/news/game-updates/league-of-legends-patch-26-16-notes/ | League of Legends Patch 26.16 Notes | 2026-08-11 | 2026-08-26 | 26.16 | 官方 26.16 补丁页；ARAM: Mayhem 部分聚焦战士体验，加入 Upgrade Sundered Sky 与 Upgrade Ravenous Hydra 两个 Gold Augment，调整 Locke 和多项 Augment，并修复多项交互问题。 | OFFICIAL |
| https://www.leagueoflegends.com/en-us/news/game-updates/league-of-legends-patch-26-12-notes/ | League of Legends Patch 26.12 Notes | 2026-06-09 | 2026-08-26 | 26.12 | 官方 26.12 补丁页；ARAM: Mayhem 加入 Ability Augments 与 Quest Augments，引入可多阶段成长的 Quest Augment，移除当季 Trait System，并把部分 Trait 效果改为独立 Augment。 | OFFICIAL |
| https://developer.riotgames.com/docs/lol | Developer API Policy | null | 2026-08-26 | null | LoL 第三方产品政策页；规定注册、变现、安全与游戏完整性边界，并列出获批及不获批用例。页面未提供可核验的发布或最后修改日期。 | OFFICIAL |

## 结论

### 1. 26.12 与 26.16 的固定事实

- `26.12` 是这组固定页面中的 Mayhem 机制变更锚点：Ability Augments、Quest Augments、多阶段 Quest，以及移除当季 Trait System 均由该页正文明确说明。
- `26.16` 是这组固定页面中的后续 Mayhem 调整锚点：新增两个战士物品升级类 Gold Augment，并调整英雄、Augment 与相关交互。
- 本轮没有核验其他补丁页，因此不对 `26.16` 之后的版本、当前线上版本或完整 Augment 池作结论。

### 2. LoL policy 的 `multiple choices` 边界

LoL Developer API Policy 的 Game Integrity 条款要求产品不要移除玩家决策；产品可以突出重要决策，并 `give multiple choices` 帮助玩家自行判断。结合同页把“替玩家规定决定”的应用列为不获批准用例，产品边界是：可以展示多个选择及其中性取舍，但不能替玩家给出唯一处方、自动选择，或实质上移除玩家决策。

这是一条通用政策边界，不等于 Riot 已批准某个具体产品；面向玩家的产品仍需按该页要求注册并接受具体用例审查。

### 3. “禁止 augment win rate”是否只在 TFT

逐字条款为：

> Products cannot display win rates for Augments or Arena Mode items. This applies to all websites, applications and overlays.

结论：**不是只在 TFT。** 该条款就位于本轮固定核验的 LoL 页面 `https://developer.riotgames.com/docs/lol`，在 `Game Policy` → `Examples of Unapproved Use Cases` 下；原文本身没有写 `TFT`，并且同时明确提到 LoL 的 Arena Mode items。把这条禁令标成“TFT-only”属于误报。

26.12 与 26.16 两个 LoL 补丁页又明确把 ARAM: Mayhem 中的选择称为 Augments。因此，在这 3 个固定页面的合并语境下，第三方 LoL 产品不得展示 Mayhem Augment 胜率。需要保持措辞颗粒度：政策原句没有逐字写 `ARAM: Mayhem`，这是由 LoL 政策的 `Augments` 类别与两份 LoL 补丁页的术语对应得出的适用结论。

本轮没有打开 TFT 政策页，所以不判断 TFT 是否另有相同条款；这不影响“该禁令并非只存在于 TFT 范围”的结论。

## 产品边界摘要

- 禁止：展示 Augment 胜率；适用页面、应用与 overlay。
- 禁止或不获批准：替玩家规定决定、提供玩家此前不知道的本局特定信息、利用客户端未呈现的信息制造竞争优势。
- 可行方向：突出重要决策，提供多个非处方式选择及中性信息，让玩家自己决定。
- 必要条件：面向玩家的产品应注册；通用政策文本不构成对具体产品的单独批准。

结构化数据见 `outputs/phase3_official_sources.json`。

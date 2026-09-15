# Wave1-C：ARAM Mayhem 专业攻略/统计站来源可用性审计

审计日期：2026-08-26（Asia/Shanghai）  
审计方式：公开页面、小样本逐页验证；未登录、未调用私有 API、未绕过反爬、未批量抓取。  
候选数：7 个独立来源。

## 1. 结论先行

本轮可以形成一个“有条件 allowlist”，但不能把任何站点视为无约束的数据 API：

| 决策 | 来源 | 可用边界 | 主要理由 |
|---|---|---|---|
| `ALLOW_CONDITIONAL` | METAsrc | Mayhem champion、augment、item；按 patch 小批量、低频获取公开 HTML/内嵌 JSON | Mayhem 路由和普通 ARAM 明确分开；冠军页同时提供三维；有 patch 历史和公开 `application/json` 行数据 |
| `ALLOW_CONDITIONAL` | Blitz.gg | Mayhem champion、augment、item；优先 SSR HTML，按 patch 缓存 | 冠军页标题、正文、模式标签均明确 Mayhem，且包含增幅与物品区；但条款正文尚未完成审阅 |
| `ALLOW_CONDITIONAL` | ARAMMayhem.com | Mayhem champion、augment、item；优先静态 HTML/JSON-LD，需交叉验证 patch | 专门 Mayhem 站，三维齐全、URL 稳定；但统计方法和 ToS 未公开，且首页局部 patch 标签与页面主 patch 不一致 |
| `ALLOW_CONDITIONAL` | U.GG | 仅 champion×augment 的 Mayhem tier/推荐；不把通用 Items 页算作 Mayhem item 数据 | 冠军 Mayhem 路由和普通 ARAM 分开，公开页无需登录；样例页未提供 Mayhem item 构筑 |
| `ALLOW_REFERENCE_ONLY` | Riot Games Support | 只用于模式机制、可用性、选择阶段和官方规则 | 官方、明确 Mayhem，但不是 champion×augment×item 统计源 |
| `ALLOW_REFERENCE_ONLY` | League of Legends Wiki | 用于 augment 名称、描述、tier、禁用状态及部分 item 机制；遵守 CC BY-SA 3.0 | 页面及公开 Lua 数据模块结构清楚；无胜率，也不是冠军构筑统计源；页面存在反自动化遥测信号 |
| `REJECT_STATISTICAL` / `GUIDE_ONLY` | Mobalytics | 只可用于专家评级/机制或构筑说明；不得作为纯 Mayhem 胜率、物品统计来源 | 页面明确声明 Riot 不提供 Mayhem API 数据，站点混合普通 ARAM 数据与 rating-based augment 数据；页面还存在 `26.16`/`16.16` patch 冲突 |

硬边界：本报告不抓取、不列出、也不把任何 Augment 胜率作为产品结论。即使页面可见胜率字段，也只能把“该字段存在”当作结构线索；产品侧必须丢弃该字段。

## 2. 判定口径

- “明确 Mayhem”要求样例页的 URL、标题或正文明确出现 `ARAM Mayhem` / `ARAM: Mayhem` / `Mayhem`，且不能仅从普通 ARAM 页面推断。
- `FULL` 表示样例页存在该维度的 Mayhem 专属实体或构筑区；`PARTIAL` 表示仅有机制、评级或混合口径；`NONE` 表示审计页不支持该维度。
- patch 只记录页面直接显示或 JSON-LD 直接给出的值；相对时间（如“3 hours ago”）不转换成伪造的绝对时间。
- `robots` 的 meta 标签只是搜索引擎索引提示，不等同于允许抓取。未成功读取 `robots.txt` 或未完整审阅 ToS 的站点，只能列为有条件使用。
- 采集设计只允许公开页面、浏览器可见 HTML 和页面随附的公开内嵌 JSON；不调用私有接口，不复现客户端请求，不绕登录/CAPTCHA/限流。

## 3. 来源逐项审计

### 3.1 ARAMMayhem.com

- `source_name`：ARAMMayhem.com
- `base_url`：<https://arammayhem.com/>
- 类型：`STATISTICAL`
- 明确 Mayhem：是。站点品牌、首页、About 页和冠军页均明确写出 ARAM Mayhem。
- 维度：champion=`FULL`；augment=`FULL`；item=`FULL`。样例冠军页同时出现冠军、增幅、增幅组合和物品实体链接。
- patch/date：首页和 Brand 页主口径为 patch `26.16`；首页显示更新日期 `2026-08-24`，Brand 页 JSON-LD 的 `datePublished`/`dateModified` 也是 `2026-08-24`。但首页 `Top Augments` 区域仍标注 `PATCH 26.12`，因此 augment 全局页必须单独做 patch 一致性校验。
- 可访问性：公开、无需登录；静态正文和实体链接可直接读取。页面包含广告脚本，但核心内容不依赖登录。
- HTML/JSON 线索：Astro 风格静态页面；稳定路径包括 `/build/{champion}/`、`/augments/{augment}`、`/items/{item}/`、`/combo/{slug}/`。冠军页有 `Article` JSON-LD、`BreadcrumbList`，并包含 patch 与修改日期。
- robots/ToS：审计中浏览器打开 `robots.txt` 被客户端级规则拦截，不能据此判断站点允许或禁止；页脚未发现 Terms/Privacy 链接，仅见版权、Riot 非背书声明和 “All rights reserved”。
- 推荐采集策略：按 patch 低频抓取冠军索引和单个冠军静态页；以 URL 实体为主，JSON-LD 仅用于 patch/date；采集前后校验页面主 patch 与局部区块 patch。丢弃所有 augment 胜率字段；组合说明只作为 GUIDE 文本。
- 直接证据：
  - 首页与 patch/date：<https://arammayhem.com/>
  - champion×augment×item 样例：<https://arammayhem.com/build/brand/>
  - 模式说明：<https://arammayhem.com/about/>
  - robots 目标（本轮未能验证正文）：<https://arammayhem.com/robots.txt>

### 3.2 U.GG

- `source_name`：U.GG
- `base_url`：<https://u.gg/>
- 类型：`STATISTICAL`
- 明确 Mayhem：是。样例 URL、页面标题、模式标签、筛选器和正文都明确为 ARAM Mayhem，并与普通 `ARAM` 标签并列。
- 维度：champion=`FULL`；augment=`FULL`；item=`NONE`（本次 Mayhem 冠军样例只提供增幅 tier；站点顶部通用 Items 链接不能当作 Mayhem item 数据）。
- patch/date：样例页直接显示 patch `26.16`；未显示可持久引用的绝对更新时间。
- 可访问性：公开、无需登录即可读取核心 tier；页面加载 reCAPTCHA、订阅/支付及广告相关脚本，但未阻断公开内容。
- HTML/JSON 线索：React 页面；HTML 中存在 `script#reactn-preloaded-state`，内容为 `window.__REACTN_PRELOADED_STATE__ = {...}`，另有 `__LOADABLE_REQUIRED_CHUNKS__`。这不是受支持的公共 API，字段可能变动。
- robots/ToS：公开 ToS 页显示最后更新于 `2018-05-25`，包含专有创意/功能与知识产权约束；审阅到的短 ToS 未明确给出爬虫许可。`robots.txt` 本轮未读取，故不能把站点加入无条件自动采集名单。
- 推荐采集策略：只用公开冠军 Mayhem HTML 的 tier bucket 和增幅名称；每 patch 小样本刷新并缓存；不要把通用 Items 页拼接进 Mayhem。内嵌状态只作为 HTML 解析失败时的结构线索，不依赖未公开接口。
- 直接证据：
  - champion×augment 样例：<https://u.gg/lol/champions/aram-mayhem/brand-aram-mayhem>
  - Mayhem tier 入口：<https://u.gg/lol/aram-mayhem-tier-list>
  - Terms：<https://u.gg/terms-of-service>
  - robots 目标（本轮未读取）：<https://u.gg/robots.txt>

### 3.3 Blitz.gg

- `source_name`：Blitz.gg
- `base_url`：<https://blitz.gg/>
- 类型：`STATISTICAL`
- 明确 Mayhem：是。独立 augment 页、tier 页及冠军页均使用 `aram-mayhem` 路由；冠军页正文明确写出 Mayhem。
- 维度：champion=`FULL`；augment=`FULL`；item=`FULL`。Brand 页具有 rarity 分组的 augment、playstyle build、starting/completed/situational items 及技能顺序。
- patch/date：Brand 页正文和底部指南直接显示 patch `26.16`；页首模板中的 “for Patch” 值为空，属于渲染瑕疵。页面只给相对更新时间（审计时分别看到“约 3 小时前”和 augment 页“约 50 分钟前”），不能持久化为绝对日期。
- 可访问性：公开、无需登录；SSR 正文完整。广告、登录和下载 overlay 不是读取核心内容的前置条件。
- HTML/JSON 线索：SvelteKit SSR；可从语义标题和页面分区读取。未在样例中识别到可承诺稳定的公开业务 JSON；应把 HTML 视为唯一受审计的输入。稳定样例路径为 `/lol/champions/{Champion}/aram-mayhem`。
- robots/ToS：页面 meta robots 为 `index, follow, max-image-preview:large`，这不等于抓取授权。页脚 Terms/Privacy 直链已验证，但用户要求立即收束前未完成条款正文审阅；`robots.txt` 也未读取。
- 推荐采集策略：低频读取 SSR HTML，按 champion+patch 缓存；只有正文和页内模式标签同时指向 Mayhem 时才入库。任何 augment 的百分比/胜率字段均丢弃；不要调用页面背后的非公开数据请求。
- 直接证据：
  - augment 目录：<https://blitz.gg/lol/aram-mayhem-augments>
  - champion×augment×item 样例：<https://blitz.gg/lol/champions/Brand/aram-mayhem>
  - Terms：<https://blitz.gg/legal/terms-of-service>
  - Privacy：<https://blitz.gg/legal/privacy-policy>
  - robots 目标（本轮未读取）：<https://blitz.gg/robots.txt>

### 3.4 METAsrc

- `source_name`：METAsrc
- `base_url`：<https://www.metasrc.com/>
- 类型：`STATISTICAL`
- 明确 Mayhem：是。站点把 Ranked、League Classic、Arena、普通 ARAM、`ARAM: Mayhem`、`Mayhem Classic-ish` 分成独立路由和标签，降低混用风险。
- 维度：champion=`FULL`；augment=`FULL`；item=`FULL`。Brand Mayhem 页同时提供增幅、起始物品、核心/顺序、后续槽位选项。
- patch/date：主页和 Brand 页均直接显示 patch `26.16`，还提供 `26.15` 至更早 patch 的显式查询链接。JSON-LD `dateModified` 为 `2026-08-23T06:13:33.957Z`。
- 可访问性：公开、无需登录，主要内容服务端渲染。
- HTML/JSON 线索：HTMX/SSR 页面；Brand 页存在 `application/json`、id=`ArenaWidget_row_data` 的公开内嵌数组，记录 augment 的 `key`、`title`、`tierClass`、`image`、`values`、`displays`。名称虽含 Arena，当前页面路由和正文明确是 Mayhem，仍需用页面模式和 patch 双重校验。
- robots/ToS：meta robots 只给 `max-image-preview:large`。Terms 与 Privacy 链接已验证，但正文未在收束前完成审阅；`robots.txt` 未读取。因此仅建议条件 allowlist。
- 推荐采集策略：优先公开内嵌 JSON 的实体名称/tier，再以 SSR 标题、URL、patch 校验模式；item 区从 HTML 提取。按 patch 全量一次、平时不轮询；不提取或展示 augment 胜率。
- 直接证据：
  - Mayhem 主页/patch 入口：<https://www.metasrc.com/lol/mayhem>
  - champion×augment×item 样例：<https://www.metasrc.com/lol/mayhem/champions/brand/build>
  - augment tier 入口：<https://www.metasrc.com/lol/mayhem/tier-list/augments>
  - Terms：<https://www.metasrc.com/terms>
  - Privacy：<https://www.metasrc.com/privacy>
  - robots 目标（本轮未读取）：<https://www.metasrc.com/robots.txt>

### 3.5 Mobalytics

- `source_name`：Mobalytics
- `base_url`：<https://mobalytics.gg/>
- 类型：`GUIDE`
- 明确 Mayhem：页面和 URL 明确 Mayhem，但数据口径不是纯 Mayhem。
- 维度：champion=`PARTIAL`；augment=`FULL`（专家/rating-based 评级，不是纯 Mayhem 胜率）；item=`NONE`（样例明确标为 `ARAM ITEMS`，不能算作 Mayhem item 统计）。
- patch/date：tier 页标题/meta 为 `26.16`，冠军页标题/meta 也为 `26.16`，但冠军页正文两次显示 `16.16`，存在无法忽略的版本冲突。tier 页只显示相对的“11 days ago”。
- 可访问性：公开、无需登录即可读取核心页。
- HTML/JSON 线索：React SSR；页面含体积很大的 `window.__PRELOADED_STATE__` 和 loadable chunk JSON。由于条款明确限制自动化访问，不能把这些预载数据当作可采集接口。
- robots/ToS：Terms 最后更新 `2026-07-23`，明确规定个人、非商业使用；禁止通过 spiders、robots、crawlers、data-mining tools 等机制搜索/下载内容（站点提供工具或一般网页浏览器除外），并限制商业使用。故不进入自动采集 allowlist。
- 推荐采集策略：只允许人工/浏览器参考专家 tier、augment 说明和构筑思路；产品中必须标记为 GUIDE。禁止自动化采集其预载状态；禁止用普通 ARAM items 或 build 胜率冒充 Mayhem 数据。
- 拒绝统计的直接原因：页面明确写出“Riot 不提供 Mayhem API 数据，因此组合 ARAM 数据与 rating-based augment 数据”；这正是本任务要求避免的口径混合。
- 直接证据：
  - 专家 augment tier：<https://mobalytics.gg/lol/tier-list/mayhem>
  - Brand Mayhem 混合口径样例：<https://mobalytics.gg/lol/champions/brand/mayhem-builds>
  - Terms：<https://mobalytics.gg/terms/>
  - robots 目标（本轮未读取）：<https://mobalytics.gg/robots.txt>

### 3.6 Riot Games Support

- `source_name`：Riot Games Support
- `base_url`：<https://support.riotgames.com/>
- 类型：`GUIDE`
- 明确 Mayhem：是，官方专题页。
- 维度：champion=`NONE`；augment=`PARTIAL`（选择阶段、tier、Quest、Ability Augment、进度解锁等机制）；item=`NONE`。
- patch/date：页面未声明 patch；显示文章时间 `2026-05-18 16:23`。不能把该日期推断成 patch。
- 可访问性：公开、无需登录；文章正文、标题和表格可直接读取。
- HTML/JSON 线索：语义化文章标题、章节和表格；未识别需要使用的业务 JSON。适合作为规则基线，不适合作为统计源。
- robots/ToS：页脚给出 Riot Terms/Privacy 直链；本轮未审阅其全文，也未读取 support 子域 `robots.txt`。官方公开文章仍只建议人工或低频机制同步。
- 推荐采集策略：人工核对并摘录机制事实，保留文章日期与 URL；不得从该页推导 champion/augment/item 表现或胜率。
- 直接证据：
  - 官方 Mayhem 机制页：<https://support.riotgames.com/en-us/league-of-legends/events/league-of-legends-aram-mayhem-game-mode>
  - Riot Terms：<https://www.riotgames.com/en/terms-of-service>
  - robots 目标（本轮未读取）：<https://support.riotgames.com/robots.txt>

### 3.7 League of Legends Wiki

- `source_name`：League of Legends Wiki
- `base_url`：<https://wiki.leagueoflegends.com/>
- 类型：`GUIDE`
- 明确 Mayhem：是。页面标题、描述与公开模块文档均明确为 ARAM: Mayhem。
- 维度：champion=`NONE`（无冠军构筑）；augment=`FULL`（名称、描述、tier、禁用状态、notes）；item=`PARTIAL`（只含 augment 与物品的机制交互/冷却表，不是 champion×item 构筑统计）。
- patch/date：页面未声明 patch；页脚显示最后编辑于 `2026-08-18 23:27`。公开模块提供 permanent link `oldid=4053383`，适合做可复现快照。
- 可访问性：公开、无需登录；正文表格和 Lua 模块源码均可直接读取。
- HTML/JSON 线索：MediaWiki；主表头为 `Augment / Effect / Tier`，另有 item cooldown 和 Quest 表。页面直接指向 `Module:MayhemAugmentData/data`，模块格式为 `['Augment name'] = { description=..., tier=... }`，属于公开网页中的结构化 Lua 文本，不是私有 API。
- robots/ToS：meta robots 为 `max-image-preview:standard`。页脚明确 CC BY-SA 3.0，并提示 additional terms；Terms/Privacy 直链可见。页面脚本还出现向站点反自动化端点报告的遥测逻辑，故不建议批量或高频自动抓取，即使数据模块公开。
- 推荐采集策略：优先人工/低频读取指定 oldid 的公开模块或主表，保存 attribution、source URL、oldid 和 CC BY-SA 许可信息；只用于机制与描述，不展示胜率。
- 直接证据：
  - Augment 表：<https://wiki.leagueoflegends.com/en-us/ARAM:_Mayhem/Augments>
  - 公开 Lua 数据模块：<https://wiki.leagueoflegends.com/en-us/Module:MayhemAugmentData/data>
  - 可复现 permanent link：<https://wiki.leagueoflegends.com/en-us/Module:MayhemAugmentData/data?oldid=4053383>
  - CC BY-SA 3.0：<https://creativecommons.org/licenses/by-sa/3.0/>
  - Terms：<https://weirdgloop.org/terms>
  - robots 目标（本轮未读取）：<https://wiki.leagueoflegends.com/robots.txt>

## 4. 结构化抽取边界

允许进入产品数据层的字段应限制为：`source`、`source_url`、`mode=ARAM_MAYHEM`、`patch_or_revision`、`champion_id/name`、`augment_id/name/tier/description`、`item_id/name`、构筑顺序或 tier 标签、`observed_at`、`license/provenance`。每条记录必须保存直接页面 URL。

以下字段或推导禁止进入本阶段产品结论：

- 任意来源的 Augment 胜率、由其排序反推出的胜率、跨站聚合后的 Augment 胜率。
- Mobalytics 冠军页中的普通 ARAM items/build 统计作为 Mayhem item 统计。
- 普通 ARAM、Arena、Mayhem Classic-ish 与 ARAM Mayhem 的跨模式拼接。
- 未在页面直接声明的 patch；相对更新时间推导出的绝对时间。
- 通过私有 API、客户端请求复现、登录态、反爬绕过或批量并发得到的数据。

推荐的最小验证门：

1. URL 和正文必须同时指向 Mayhem；若存在模式切换器，选中项必须是 Mayhem。
2. patch 必须来自页面可见文本、meta/JSON-LD 或明确 patch query；多个区块不一致时整页降级并人工复核。
3. item 区必须明确属于 Mayhem；只写 `ARAM ITEMS` 的区块不得录入 Mayhem。
4. GUIDE 与 STATISTICAL 分库存储，禁止把专家 tier 当作胜率。
5. 每 patch 低频刷新并缓存；遇到登录、CAPTCHA、403/429 或 robots/ToS 不清晰时停止，不尝试绕过。

## 5. 已知缺口

- 除 Mobalytics 外，本轮没有完整审阅 Blitz、METAsrc、Riot 的条款正文；其 allowlist 均为条件式，不是法律授权结论。
- `robots.txt` 未形成跨站全文矩阵：ARAMMayhem.com 的目标被浏览器客户端拦截，其余站点在用户要求立即收束前未读取。报告明确保留 `UNKNOWN/NOT_REVIEWED`，没有把未知写成允许。
- 统计站未公开统一、可审计的数据采样方法；需要在产品侧保留 source-level provenance，并对 patch、模式和字段做独立校验。

## 6. 可执行建议

首选链路为：METAsrc（结构化 Mayhem 三维）→ Blitz（SSR 三维交叉验证）→ ARAMMayhem.com（专门站补充与交叉验证）→ U.GG（champion×augment tier 交叉验证）。Riot 和 Wiki只负责官方/机制层，Mobalytics只作为人工 GUIDE 参考。

在任何自动化上线前，先完成四个条件站点的 robots/ToS 复核；若条款不允许自动化，则退化为人工编辑或获取授权。无论授权结果如何，Augment 胜率都不属于本产品结论范围。

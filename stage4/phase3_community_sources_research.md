# Wave1-D：ARAM Mayhem 社区经验来源与抽取审计

生成时间（UTC）：`2026-08-25T18:13:48Z`

## 结论

Reddit、YouTube 与 note 公开创作者文章都能补充 ARAM Mayhem 的 champion–augment–build 经验，但只适合作为低权重的发现与假设来源，不能单独升级为机制事实、版本事实或推荐真值。本次仅核验 3 个真实样例，没有批量采集、登录、调用绕行 API、下载视频或保存长正文/字幕。

- Reddit：**有条件可用**。帖子标题、正文、作者、评论树可公开读取，但日期常以相对时间展示，直接浏览还可能触发 CAPTCHA；帖子与每条评论必须是不同内容单元。
- YouTube：**有条件可用**。标题、频道、`datePublished`、时长、描述和 chapters 较稳定；字幕必须逐视频核验。本次样例明确显示“无法显示字幕”，所以只能抽取描述/章节，不得把章节冒充字幕或口播结论。
- note：**有条件可用且文本边界最清晰**。可得到 canonical URL、标题、作者、日期和完整文章正文；`?hl=en` 是平台 AI 翻译面，必须保留原文语言和翻译标记。
- Discord 邀请不等于消息公开。其消息通常缺乏无需登录的稳定公开 URL，因此在本任务约束下不纳入。
- 任一来源没有明确 patch 文本时，`patch.status` 必须为 `UNKNOWN`、`patch.value` 必须为 `null`。发布日期不能反推 patch。

结构化结果、样例和可程序化规则见同目录的 `phase3_community_sources.json`。

## 调研边界与证据面

只验证下列公开页面，没有继续扩大来源范围：

1. Reddit：[Alt builds for champs in aram mayhem](https://www.reddit.com/r/arammayhem/comments/1ujrpi0/alt_builds_for_champs_in_aram_mayhem/)，公开索引给出日期 `2026-06-30`；公开文本面可见作者、标题、帖子正文和评论。普通浏览器直开在本次核验中触发 CAPTCHA，因此不应把 Reddit HTML 直抓当成稳定生产接口。
2. YouTube：[The Best Augment Combos and Sets in New Aram Mayhem!](https://www.youtube.com/watch?v=fhxUMg-tZdk)，公开元数据 `datePublished=2026-01-27T13:15:36-08:00`、时长 `PT11M28S`；描述含带时间戳的 chapters。播放器字幕控件显示不可用。
3. note：[【ARAMメイヘム】新シーズンのアイテムビルドについて【パッチ26.1】](https://note.com/aram_build/n/n1f1c31baac1c)，页面日期为 `2026-01-09`，标题和正文都明确出现 Patch 26.1。

本报告记录“当时公开页面能提供什么”，不承诺平台未来仍以同一 DOM、索引或访问策略提供内容。作者也可能编辑或删除内容，所以后续采集应保存 `retrieved_at_utc`、内容单元哈希和 `edited_observed`，但不保存整篇受版权保护正文。

## COMMUNITY 与 INFERENCE

### COMMUNITY

`COMMUNITY` 是可以直接归因给作者的短摘要，必须有公开 URL、发布日期、内容单元和定位锚点。例如：“帖子作者把 Renata Glasc 的替代路线描述为 Nashor/AP 普攻构筑，并列出 Marksmage、Wooglet 及攻速/AP 类强化。”它只证明“作者这样说过”，不证明玩法准确。

### INFERENCE

`INFERENCE` 是分析器额外做出的规范化或推断，例如把帖子中的缩写 `wooglet` 映射到本地目录的 `ARAM_Quest_WoogletsWitchcap`，或由 YouTube 的总标题与 chapter 标题推断作者可能把组合视为正向案例。INFERENCE 必须：

- 单独存储，使用独立 ID；
- 列出 `derived_from_claim_ids` 和方法；
- 置信权重上限低于 COMMUNITY；
- 不得反写或覆盖原 COMMUNITY 摘要；
- 不得用发布日期推断 patch；这种推断直接拒绝，不入库。

## 分渠道评估

### Reddit

可得到的元数据与边界：

- 帖子级：subreddit、帖子 ID、作者显示名、标题、正文、公开 URL；公开索引有时能给出绝对发布日期。
- 评论级：评论作者、正文、层级、edited 标记和 permalink；评论是独立内容单元，不继承主帖作者身份。
- 帖子正文、每条评论和每条回复必须分别抽取。把整条讨论串拼成一个“共识”会抹掉冲突和归因。
- 相对日期（如“1mo ago”）不能满足生产入库门槛；必须找到同一公开内容单元的绝对日期，否则拒绝或只进入人工待审队列。
- 本次直接浏览触发 CAPTCHA；不能绕过，也不能把搜索缓存当成稳定批量接口。适合少量人工/公开索引核验，不适合作为无 API 的稳定批采源。

Patch 规则：仅当帖子或该条评论明确写出 patch 才可赋值。评论最多可记录父帖的 `context_patch`，但不能自动把它写成评论自身的 `claim_patch`。本次样例没有 patch，故为 `UNKNOWN`。

风险：自报伤害、胜率和“最强”等结果缺乏对局样本、队伍、装备完成度与复现条件；评论赞踩数和回复数只反映互动，不进入准确性权重。编辑、删除、跨帖转发还会制造版本和独立性误判。

### YouTube

可得到的元数据与边界：

- 视频级：video ID、标题、频道、公开 URL、标准元数据中的上传时间、时长、描述。
- 描述级：作者写入的正文和 chapters；chapter 名称与时间戳可作为定位锚点，但通常不是完整命题。
- 字幕级：必须逐视频记录 `AVAILABLE_HUMAN`、`AVAILABLE_AUTO`、`UNAVAILABLE` 或 `UNKNOWN`，并记录语言。只有公开页面确实提供字幕时才可做时间段摘要。
- 评论级：每条评论是与视频正文分离的 COMMUNITY 单元，不能因为评论位于视频下方就归因给频道作者。
- 画面或口播没有字幕时，本任务不下载视频、不做音频转写，也不从缩略图/OCR 猜玩法结论。

本次视频描述的 `00:21` chapter 直接并列 “Stackosaurus Tap Dancer Kog'Maw”，因此可以抽取“章节这样命名”的 COMMUNITY 观察；不能进一步断言组合胜率、伤害或适用 patch。视频未明确 patch，所以为 `UNKNOWN`。

公开元数据时间为 `2026-01-27T13:15:36-08:00`，换算 UTC 为 `2026-01-27T21:15:36Z`；上海本地页面显示 `2026-01-28` 是时区呈现，不是日期冲突。存储应保留原始带偏移时间和 UTC 规范值。

### note 创作者文章（额外社区渠道）

可得到的元数据与边界：

- canonical URL、作者 slug/显示名、原文标题、文章正文、标题层级、日期和语言。
- `?hl=en` 页面会显示平台 AI 自动翻译提示；canonical 仍是日文原文 URL。翻译文本只能作为阅读辅助，结构化来源语言保持 `ja`，并记录翻译方法。
- 文章可能被作者原地更新；页面没有可靠修订历史时，应以 `retrieved_at_utc` 和规范化 claim hash 形成新 revision，不覆盖旧观察。

本次页面的 `datetime` 属性与不同渲染面在小时上不一致，但都落在 `2026-01-09`。因此样例只发布到“日”精度，并保存 `CONFLICTING_HOUR_SAME_DAY` 警告。标题和正文均明确 Patch 26.1，所以 patch 可标为 `EXPLICIT`。

## 可程序化最低质量门槛

入库顺序应为 fail-closed：

```text
1. URL 是无需登录即可访问的 https 直达页，且平台在允许列表；
2. 有稳定 native_id、canonical_url、标题、绝对发布日期和 retrieved_at_utc；
3. 内容单元边界明确：post/body/comment/description/chapter/transcript_segment/article_section；
4. 同一内容单元或明确父级上下文直接出现 ARAM Mayhem；
5. 至少识别 1 个 champion，且至少有 1 个 augment/augment family/item/build direction；
6. 有可复核锚点：段落/标题/chapter timestamp/comment permalink 之一；
7. 只保存短改写摘要；不保存长正文或完整字幕；
8. patch 字段必须存在：显式证据才赋值，否则 UNKNOWN/null；
9. COMMUNITY 与 INFERENCE 分离；
10. 通过去重后才计作独立来源。
```

硬拒绝条件包括：只有搜索摘要而没有稳定原 URL；只有相对日期；内容需登录/加入群组；只有截图或视频画面且没有可定位文本；无法确认 ARAM Mayhem；只有 champion 没有 augment/build 关系；长篇复制；把 engagement 当准确性；通过日期猜 patch；把多名用户的话合并成一个作者结论。

研究样例可以记录访问障碍，但生产 claim 只有满足上述字段才能 `quality_gate.passes=true`。本次 Reddit 样例的绝对日期来自公开索引且正文来自同一公开 URL 的文本面，因此作为“可用性样例”通过；这不代表允许对 Reddit 做批量抓取。

## 低置信度规则

`confidence.score` 是排序用证据权重，不是准确率概率。

- COMMUNITY 总上限：`0.49`；INFERENCE 总上限：`0.29`。
- patch 为 `UNKNOWN` 时再封顶 `0.39`。
- 起始权重：文章/帖子正文 `0.30`，视频描述 `0.28`，chapter-only `0.24`，字幕时间段 `0.28`，普通评论 `0.20`。
- 可加分：绝对日期 `+0.04`、精确锚点 `+0.04`、champion 精确识别 `+0.03`、augment/build 实体识别 `+0.03`、patch 显式 `+0.05`。
- 可扣分：机器翻译 `-0.05`、内容/时间元数据冲突 `-0.03`、实体只能模糊映射 `-0.04`、只有章节标题而无正文/字幕 `-0.04`。
- 点赞、点踩、播放量、评论数、订阅数、作者自报场次一律 `+0.00`。
- 多个社区来源相同，只能形成 `community_corroboration`，不能升级为 `OFFICIAL` 或机制事实；疑似转载/同作者跨平台内容必须共享 `independence_cluster_id`，避免重复加权。

## 去重策略

### URL 与内容单元规范化

- Reddit：规范为 `https://www.reddit.com/r/{subreddit}/comments/{post_id}/{slug}/`；去除跟踪参数。主帖 key 为 `reddit:{post_id}:post`，评论 key 为 `reddit:{post_id}:comment:{comment_id}`。
- YouTube：规范为 `https://www.youtube.com/watch?v={video_id}`；去除 `list`、`t`、`feature` 等参数。chapter 时间保留在 `content_anchor`，不制造新 source。评论另用 comment ID。
- note：规范为 `https://note.com/{author_slug}/n/{note_id}`；去除 `hl` 和跟踪参数。原文与平台翻译面属于同一 source，翻译状态作为元数据。

### 精确与近似去重

1. 先按 `source_unit_key` 去重；相同 key 的页面再次出现只新增 revision/last_seen。
2. 对 claim 构造 NFKC、大小写折叠后的签名：`mode + patch status/value + champion IDs + augment raw/resolved IDs + item/build order + predicate + polarity`，计算 SHA-256。
3. 相同签名合并为同一 claim revision，但保留所有 observed URLs 和首次/末次时间。
4. 近似文本只有在实体关系、极性、build 顺序一致且 token 5-gram Jaccard `>=0.90` 时进入 duplicate candidate；自动系统只聚类，不删除。
5. 跨平台或不同作者的相同主张不物理合并；放入同一 `duplicate_cluster_id`/`independence_cluster_id`，分别保留出处，同时只按一个独立证据簇计权。

将 patch、极性和 build 顺序纳入签名可避免把“推荐 Nashor”与“不要 Nashor”、或不同版本路线误去重。

## 冲突保留

采用 append-only observation，不使用 last-write-wins：

- `conflict_key = mode + patch_context + champion + relation_slot + predicate`。
- 同 key 上出现相反极性、不同第一件装备、互斥 augment 选择或不同结果描述时，创建 `conflict_group_id`，保留每条 COMMUNITY 的 URL、作者、日期、锚点和权重。
- 已知 patch 与 `UNKNOWN` patch 不直接判定为同版本冲突；标记 `TEMPORALLY_UNRESOLVED`。
- 作者编辑产生新 revision，旧 revision 不删除；若页面仅显示 edited 而没有旧文本，则只记录“已编辑”，不重建旧内容。
- 只有更高等级的官方变更说明、可复现实验或合格遥测才能给冲突增加 resolution；社区原记录仍保留，只可标注 `superseded_for_patch`。
- 本次 Reddit 讨论在 Nashor Renata 路线上已经出现赞成与反对回复，说明讨论串绝不能压成单一事实。由于回复只暴露相对日期，本次没有把这些回复作为独立合格样例入库。

## 版权友好的摘要策略

- 默认 `verbatim_excerpt=null`；保存公开 URL、标题、作者、日期、内容单元、实体词、锚点和不超过 220 个中文字符的原创改写摘要。
- 只有消歧确有必要时才保存不超过 20 个来源语言词的短引文，并记录引文用途；同一来源不累计成长摘录。
- 不保存完整帖子、评论串、文章、字幕、视频、音频、缩略图或截图；去重使用 claim 签名，不要求落盘原文。
- YouTube 只保存描述/chapter 或已公开字幕的短语义摘要及时间段，不下载视频、不自行转写。
- note 的平台 AI 翻译不作为新作者文本；只保存翻译方式和中文改写，不复制长译文。
- 删除内容时保留最小 provenance 和历史 claim 摘要即可，不重新分发原文。

这是工程上的最小化策略，不是对各平台条款或版权状态的法律判断；生产采集仍应单独核对平台条款、robots 和授权范围。

## 三个真实样例

### S-REDDIT-001

- URL：[Reddit 原帖](https://www.reddit.com/r/arammayhem/comments/1ujrpi0/alt_builds_for_champs_in_aram_mayhem/)
- 日期：`2026-06-30`（日精度；公开索引绝对日期）
- 内容单元：主帖正文第 1 个 build 条目
- COMMUNITY 摘要：作者把 Renata Glasc 的替代路线描述为 Nashor/AP 普攻构筑，并列 Marksmage、Wooglet 与攻速/AP 类强化。
- Patch：`UNKNOWN`
- 权重：`0.39 LOW`；没有使用赞数或评论数。
- INFERENCE 边界：`Wooglet` 到 `ARAM_Quest_WoogletsWitchcap` 的映射另存推断，不写回 COMMUNITY。

### S-YOUTUBE-001

- URL：[YouTube 视频](https://www.youtube.com/watch?v=fhxUMg-tZdk)
- 日期：`2026-01-27T13:15:36-08:00`（UTC：`2026-01-27T21:15:36Z`）
- 内容单元：视频描述 chapter `00:21`
- COMMUNITY 摘要：章节索引在 `00:21` 将 Kog'Maw 与 Stackosaurus、Tap Dancer 并列。
- 字幕：`UNAVAILABLE`；描述/chapter 不是字幕。
- Patch：`UNKNOWN`
- 权重：`0.34 LOW`；不从“Best”标题推导实际强度。

### S-NOTE-001

- URL：[note 原文](https://note.com/aram_build/n/n1f1c31baac1c)
- 日期：`2026-01-09`（日精度）
- 内容单元：“クリティカル200%”章节相关段落
- COMMUNITY 摘要：作者在 Patch 26.1 语境下建议 Vayne/Varus 抽到任一暴击类强化后转暴击构筑。
- Patch：`26.1 EXPLICIT`
- 权重：`0.41 LOW`；因跨语言摘要和小时元数据冲突降权。

## 最终审计清单

- 真实样例：3；Reddit、YouTube、额外社区渠道 note 各 1。
- 每个样例均有公开 URL 和绝对日期。
- 无显式 patch 的两个样例均为 `UNKNOWN/null`；没有按发布日期猜版本。
- COMMUNITY 与 INFERENCE 使用不同集合和不同置信上限。
- 已定义可程序化最低质量门槛、拒绝码、URL/claim 去重、独立性聚类与冲突保留。
- 未下载视频，未绕 API/登录/CAPTCHA，未批量采集，未保存长篇受版权保护内容。
- engagement 字段不参与准确性判断。


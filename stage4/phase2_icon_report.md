# Phase2 Augment Icon Template 与识别报告

## 结论

Phase2 图标模板链路已闭环：从本机 LoL `16.16.805.442` 的静态
`default-assets2.wad` 中，按知识库白名单导入了 KIWI/KIWI_JADE 的
`245/245` 个候选模板，缺失 `0`。模板对应 `208` 条知识库图标路径，按原始
PNG SHA-256 去重后仅保存 `163` 个文件（合计 `329,498` bytes），没有复制
无关资产。

这不是实战准确率结论。审计输入中没有来源可证明为真实对局卡面 icon ROI 的
crop；静态模板自匹配、合成 resize/亮度扰动和 WIC/hash 对拍只算工程验证。

## 输入与来源事实

- 用户给定的 `F:\Program Files (x86)\英雄联盟` 不存在；同级实际安装目录是
  `F:\Program Files (x86)\英雄联盟(26)`。导入命令显式使用后者，没有在脚本
  中猜测或硬编码安装路径。
- `outputs/tmp/game_assets` 的既有解包内容只有结构化 JSON/hash/tool 文件，没有
  PNG/JPG/DDS/TGA 图像；因此不能从该目录直接生成模板。
- 本地静态图像来源：
  `LeagueClient/Plugins/rcp-be-lol-game-data/default-assets2.wad`。
- 客户端/游戏文件版本：`16.16.805.442`。
- WAD 大小：`3,519,598,188` bytes；SHA-256：
  `b7b38b1c563b12993ad7fee564b01cbb0b26d5413ae0f7136c560bdcf86e6fa7`。
- 知识库版本：`lcu-static-sha256-930846434976ffd1`；知识库 SHA-256：
  `aa8d5bf621241f749feac0cd3a7e1138c8f5253afd35694edeced1f6bb5839a7`。
- 本地 wadtools SHA-256：
  `91b44abf44a18fe40c51d308dd8a5ba818bf3c29bad9b71db2c58805b13cd651`。
- 本地 hashtable manifest SHA-256：
  `4b01214636708a4956e8ab2cb63204bc2cd548a9990bba462f533696c1a400c1`。
- 未联网下载、未反编译二进制、未读取进程/内存、未注入/Hook、未执行输入
  自动化，也未启动或操纵 LoL。WAD 操作仅为本地静态归档的精确路径白名单解包。

## 导出结果

- candidate template：`245`
- unique knowledge icon path：`208`
- content-addressed PNG：`163`
- mode 分布：`KIWI=220`，`KIWI_JADE=188`（同一模板可同时属于两个 mode）
- unresolved/missing：`0`
- 多 ID 共用同一知识库 icon path：`21` 组
- dHash 相同的多 ID 歧义组：`54` 组
- manifest `content_hash`：
  `4f47c954bf62f2c2d77ecf61a62c60ed87fa92dbafa50d1ec58e8fb182583d25`
- manifest 文件 SHA-256：
  `82e736c11b6191814df6e58a4d33529de61ea33856c37808ab5c5e87781eb56e`

manifest 为 canonical、排序、无时间戳 JSON，记录 patch/catalog/source/hash、每个
候选 ID 的 mode、源 icon path、PNG hash、尺寸、dHash 和共享 icon 的 candidate
IDs。PNG 文件以内容 SHA-256 命名；重复来源字节会去重，来源字节冲突会跳过并写入
`unresolved`，不会按路径顺序强选。

`scripts/import_augment_icons.py` 支持两种离线来源：已解包 `--asset-root` 和显式
本地 `--wad`。WAD 路径过滤器由知识库中的 208 条路径动态生成精确白名单；首轮
固定 UX 目录过滤曾漏掉 `ARAM_MagicMissile` 的 Maps 路径，修正后已达到
`245/245`，并由 `--check` 做字节级复验。

## Matcher 与融合契约

- ROI 使用纯整数双线性缩放到 `9x8` 灰度网格，再计算 64-bit dHash；整数端点
  映射与 Python 导入器一致。
- `IconHashTemplate` 带 mode 集合；匹配按 mode 过滤。全局无模板返回
  `Unavailable/template_unavailable`，指定 mode 无模板返回
  `Unavailable/template_unavailable_for_mode`。
- 结果返回 top1/top2 score、margin 和确定性排序的 top1/top2 candidate IDs。
- 超过 Hamming distance 上限返回 `Unknown/hash_distance_above_threshold`；top1
  并列返回 `Unknown/hash_top1_ambiguous`；margin 不足返回
  `Unknown/hash_margin_below_threshold`。任何歧义都不选 ID。
- OCR/icon 同 ID：`Confirmed/ocr_icon_agree`。
- 只有一方识别出 ID：保留 ID 但仅标为 `Tentative`，不得当作强确认。
- OCR/icon ID 冲突：强制 `Unknown`、清空最终 ID，并在 reason 中同时记录两方
  ID。
- 调用方仍需在后续 app 集成阶段从 manifest 构造 `IconHashTemplate`；本阶段按
  范围要求没有修改其他 vision/app 或接入运行时。

## 测试与结果

生成与确定性复验：

```powershell
py -3.11 -B scripts\import_augment_icons.py `
  --catalog data\knowledge\augments.zh-CN.json `
  --asset-root outputs\tmp\game_assets `
  --wad 'F:\Program Files (x86)\英雄联盟(26)\LeagueClient\Plugins\rcp-be-lol-game-data\default-assets2.wad' `
  --wadtools outputs\tmp\game_assets\wadtools-0.5.7\wadtools.exe `
  --hashtable-dir outputs\tmp\game_assets\hashes `
  --patch-version 16.16.805.442 `
  --output data\knowledge\augment_icons

# 同参数追加：
--check
```

结果：`245/245` templates，`208/208` source paths，`163` unique PNG，
`missing=0`；`--check` exit `0`，manifest 与 PNG 集逐字节一致。

Release 构建与全量回归：

```powershell
.\scripts\build.ps1 -Configuration Release `
  -BuildDirectory outputs\tmp\phase2_icon_build

ctest --test-dir outputs\tmp\phase2_icon_build `
  -C Release --output-on-failure
```

结果：build exit `0`；CTest `20/20` passed，`0` failed，耗时 `14.63s`。
`icon_matcher_test` 覆盖同图、轻微 resize/亮度、不同图拒绝、top2 margin 歧义、
同 hash 并列歧义、mode 过滤、指定 mode 无模板、OCR/icon 冲突 UNKNOWN、同 ID
确认、单源 Tentative 和全局无模板。

全部真实静态 PNG 的 Python manifest ↔ C++ WIC/dHash 对拍：

```powershell
$manifest = Get-Content data\knowledge\augment_icons\manifest.json -Raw |
  ConvertFrom-Json
$taskArguments = @()
foreach ($group in ($manifest.templates | Group-Object file | Sort-Object Name)) {
  $template = $group.Group[0]
  $taskArguments += Join-Path data\knowledge\augment_icons $template.file
  $taskArguments += $template.difference_hash
}
& outputs\tmp\phase2_icon_build\bin\icon_matcher_test.exe @taskArguments
```

结果：`icon matcher checks=698 failures=0`。其中 163 个唯一 PNG 均由既有 WIC
解码，并与 importer 写入 manifest 的 dHash 一致；这仍是模板自匹配工程测试，
不计为真实卡面准确率。

## 范围与已知限制

- 本任务的手工文本改动仅位于指定 matcher、importer、matcher test 和本报告；
  生成数据仅位于 `data/knowledge/augment_icons/**`。
- 工作区不是 Git worktree，不能用 `git diff` 做边界证明；执行期间
  `CMakeLists.txt` 和 collection/预处理目标发生了并发更新。本任务没有编辑或回退
  CMake/其他 vision/app 文件；最终统一快照重建后全量测试通过。
- 54 个 dHash 歧义组是数据事实，不是准确率。共享占位图、复用图或真实 dHash
  碰撞都会按 matcher 契约返回 UNKNOWN；后续若要评估阈值，必须使用有真值、可证明
  来自真实对局卡面 ROI 的独立数据集。

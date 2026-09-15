# Wave1-B2 已提取海克斯资源精确核验

生成时间（UTC）：`2026-08-25T09:50:20Z`

## 二元结论

**Phase 1 不需要 IDA（否，`ida_required=false`）。** 已审计的结构化 LCU JSON 直接提供 655 条海克斯强化的数值 ID、技术名、zh_CN 中文名、图标路径与 rarity，并且 default/zh_CN ID 集完全一致。描述字段在本次 5 个直接相关文件中确实不存在；这是一项明确的数据缺口，但没有证据表明其只能从编译代码恢复，因此不能据此启动 IDA。来源 WAD/容器路径同样因缺少提取日志而标记“未确认”，不得从目录名猜测。

## 范围与计数

- 关键文件：5；成功解析：5；解析错误：0。
- cherry-augments：default 655 条，zh_CN 655 条。
- augment-lists：default 3 个顶层记录，zh_CN 3 个顶层记录。
- skinaugments：default 93 条。
- 样例仅保留定位所需字段；原数据不含个人信息，长数组与嵌套 modifiers 已缩减。

## 逐文件核验

### zh_cn_cherry_augments

- 完整路径：`F:\Realworld\lol\outputs\tmp\game_assets\extract_lcu_zh\plugins\rcp-be-lol-game-data\global\zh_cn\v1\cherry-augments.json`
- 字节数：`141439`
- SHA-256：`9401b01fcb2b1b74bd00c7e4b8bf3932fbe8ec4e1ec58d15b57d3f656edc5d62`
- 顶层 JSON 类型：`array`；记录数量：`655`
- 字段集合：`["augmentNameId","augmentSmallIconPath","id","nameTRA","rarity","simpleNameTRA"]`
- 角色字段实际存在性：`{"id":{"actual_field":"id","exists":true,"records_with_field":655,"non_empty":655,"empty":0,"missing":0},"name":{"actual_field":"nameTRA","exists":true,"records_with_field":655,"non_empty":655,"empty":0,"missing":0},"description":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":655},"icon":{"actual_field":"augmentSmallIconPath","exists":true,"records_with_field":655,"non_empty":655,"empty":0,"missing":0},"rarity":{"actual_field":"rarity","exists":true,"records_with_field":655,"non_empty":655,"empty":0,"missing":0},"tier":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":655}}`
- ID 校验：`{"applicable":true,"field":"id","unique_count":641,"duplicate_id_count":1,"duplicate_ids":[-1],"duplicate_id_occurrences":{"-1":15},"duplicate_extra_record_count":14,"empty_id_count":0}`
- 补充名称字段：`{"technical_name":{"actual_field":"augmentNameId","exists":true,"records_with_field":655,"non_empty":655,"empty":0,"missing":0},"simple_name":{"actual_field":"simpleNameTRA","exists":true,"records_with_field":655,"non_empty":30,"empty":625,"missing":0}}`
- 本地化名称质量：`{"contains_cjk_count":654,"replacement_character_count":0,"empty_name_count":0}`
- 前 3 条精简样例：

```json
[
  {
    "id": 1205,
    "augmentNameId": "ARAM_ADAPt",
    "nameTRA": "物理转魔法",
    "augmentSmallIconPath": "/lol-game-data/assets/ASSETS/UX/Cherry/Augments/Icons/ADAPt_small.png",
    "rarity": "kSilver"
  },
  {
    "id": 1141,
    "augmentNameId": "ARAM_AllForYou",
    "nameTRA": "全心为你",
    "augmentSmallIconPath": "/lol-game-data/assets/ASSETS/UX/Cherry/Augments/Icons/AllForYou_small.png",
    "rarity": "kGold"
  },
  {
    "id": 1002,
    "augmentNameId": "ARAM_ApexInventor",
    "nameTRA": "尖端发明家",
    "augmentSmallIconPath": "/lol-game-data/assets/ASSETS/UX/Cherry/Augments/Icons/ApexInventor_small.png",
    "rarity": "kGold"
  }
]
```

### default_cherry_augments

- 完整路径：`F:\Realworld\lol\outputs\tmp\game_assets\extract_lcu_default2\plugins\rcp-be-lol-game-data\global\default\v1\cherry-augments.json`
- 字节数：`141748`
- SHA-256：`40cf8c14e54d8e74a3de1a2623accf6d015293ab5fade850d6b0a2de1bd9cb52`
- 顶层 JSON 类型：`array`；记录数量：`655`
- 字段集合：`["augmentNameId","augmentSmallIconPath","id","nameTRA","rarity","simpleNameTRA"]`
- 角色字段实际存在性：`{"id":{"actual_field":"id","exists":true,"records_with_field":655,"non_empty":655,"empty":0,"missing":0},"name":{"actual_field":"nameTRA","exists":true,"records_with_field":655,"non_empty":655,"empty":0,"missing":0},"description":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":655},"icon":{"actual_field":"augmentSmallIconPath","exists":true,"records_with_field":655,"non_empty":655,"empty":0,"missing":0},"rarity":{"actual_field":"rarity","exists":true,"records_with_field":655,"non_empty":655,"empty":0,"missing":0},"tier":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":655}}`
- ID 校验：`{"applicable":true,"field":"id","unique_count":641,"duplicate_id_count":1,"duplicate_ids":[-1],"duplicate_id_occurrences":{"-1":15},"duplicate_extra_record_count":14,"empty_id_count":0}`
- 补充名称字段：`{"technical_name":{"actual_field":"augmentNameId","exists":true,"records_with_field":655,"non_empty":655,"empty":0,"missing":0},"simple_name":{"actual_field":"simpleNameTRA","exists":true,"records_with_field":655,"non_empty":30,"empty":625,"missing":0}}`
- 本地化名称质量：`{"contains_cjk_count":0,"replacement_character_count":0,"empty_name_count":0}`
- 前 3 条精简样例：

```json
[
  {
    "id": 1205,
    "augmentNameId": "ARAM_ADAPt",
    "nameTRA": "ADAPt",
    "augmentSmallIconPath": "/lol-game-data/assets/ASSETS/UX/Cherry/Augments/Icons/ADAPt_small.png",
    "rarity": "kSilver"
  },
  {
    "id": 1141,
    "augmentNameId": "ARAM_AllForYou",
    "nameTRA": "All For You",
    "augmentSmallIconPath": "/lol-game-data/assets/ASSETS/UX/Cherry/Augments/Icons/AllForYou_small.png",
    "rarity": "kGold"
  },
  {
    "id": 1002,
    "augmentNameId": "ARAM_ApexInventor",
    "nameTRA": "Apex Inventor",
    "augmentSmallIconPath": "/lol-game-data/assets/ASSETS/UX/Cherry/Augments/Icons/ApexInventor_small.png",
    "rarity": "kGold"
  }
]
```

### zh_cn_augment_lists

- 完整路径：`F:\Realworld\lol\outputs\tmp\game_assets\extract_lcu_zh\plugins\rcp-be-lol-game-data\global\zh_cn\v1\augment-lists.json`
- 字节数：`22200`
- SHA-256：`8133803bbe047d8e0cddecd7cc335357b36cbcfa61603b5efb28d960e4e597b3`
- 顶层 JSON 类型：`array`；记录数量：`3`
- 字段集合：`["augmentList","modeName"]`
- 角色字段实际存在性：`{"id":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":3},"name":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":3},"description":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":3},"icon":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":3},"rarity":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":3},"tier":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":3}}`
- ID 校验：`{"applicable":false,"field":null,"unique_count":0,"duplicate_id_count":0,"duplicate_ids":[],"duplicate_id_occurrences":{},"duplicate_extra_record_count":0,"empty_id_count":0}`
- 列表指标：`{"list_lengths":[44,220,188],"total_references":452,"unique_references":266,"duplicate_references_per_record":[0,0,0]}`
- 前 3 条精简样例：

```json
[
  {
    "augmentList_count": 44,
    "augmentList_first_3": [
      "Maps/ModeSpecificData/Augments/ARAM_GetExcited",
      "Maps/ModeSpecificData/Augments/ARAM_ProteinShake",
      "Maps/ModeSpecificData/Augments/ARAM_Upgrade_Collector"
    ]
  },
  {
    "augmentList_count": 220,
    "augmentList_first_3": [
      "Maps/ModeSpecificData/Augments/ARAM_BigBrain",
      "Maps/ModeSpecificData/Augments/ARAM_AllForYou",
      "Maps/ModeSpecificData/Augments/ARAM_ApexInventor"
    ]
  },
  {
    "augmentList_count": 188,
    "augmentList_first_3": [
      "Maps/ModeSpecificData/Augments/ARAM_BigBrain",
      "Maps/ModeSpecificData/Augments/ARAM_AllForYou",
      "Maps/ModeSpecificData/Augments/ARAM_BluntForce"
    ]
  }
]
```

### default_augment_lists

- 完整路径：`F:\Realworld\lol\outputs\tmp\game_assets\extract_lcu_default2\plugins\rcp-be-lol-game-data\global\default\v1\augment-lists.json`
- 字节数：`22200`
- SHA-256：`8133803bbe047d8e0cddecd7cc335357b36cbcfa61603b5efb28d960e4e597b3`
- 顶层 JSON 类型：`array`；记录数量：`3`
- 字段集合：`["augmentList","modeName"]`
- 角色字段实际存在性：`{"id":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":3},"name":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":3},"description":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":3},"icon":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":3},"rarity":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":3},"tier":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":3}}`
- ID 校验：`{"applicable":false,"field":null,"unique_count":0,"duplicate_id_count":0,"duplicate_ids":[],"duplicate_id_occurrences":{},"duplicate_extra_record_count":0,"empty_id_count":0}`
- 列表指标：`{"list_lengths":[44,220,188],"total_references":452,"unique_references":266,"duplicate_references_per_record":[0,0,0]}`
- 前 3 条精简样例：

```json
[
  {
    "augmentList_count": 44,
    "augmentList_first_3": [
      "Maps/ModeSpecificData/Augments/ARAM_GetExcited",
      "Maps/ModeSpecificData/Augments/ARAM_ProteinShake",
      "Maps/ModeSpecificData/Augments/ARAM_Upgrade_Collector"
    ]
  },
  {
    "augmentList_count": 220,
    "augmentList_first_3": [
      "Maps/ModeSpecificData/Augments/ARAM_BigBrain",
      "Maps/ModeSpecificData/Augments/ARAM_AllForYou",
      "Maps/ModeSpecificData/Augments/ARAM_ApexInventor"
    ]
  },
  {
    "augmentList_count": 188,
    "augmentList_first_3": [
      "Maps/ModeSpecificData/Augments/ARAM_BigBrain",
      "Maps/ModeSpecificData/Augments/ARAM_AllForYou",
      "Maps/ModeSpecificData/Augments/ARAM_BluntForce"
    ]
  }
]
```

### default_skinaugments

- 完整路径：`F:\Realworld\lol\outputs\tmp\game_assets\extract_lcu_default1\plugins\rcp-be-lol-game-data\global\default\v1\skinaugments.json`
- 字节数：`20068`
- SHA-256：`e17759470d8174a439e76aabfd6f9d17c132fc2b2265b3b902fe97fff3c98744`
- 顶层 JSON 类型：`array`；记录数量：`93`
- 字段集合：`["capType","image","itemId","modifiers"]`
- 角色字段实际存在性：`{"id":{"actual_field":"itemId","exists":true,"records_with_field":93,"non_empty":93,"empty":0,"missing":0},"name":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":93},"description":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":93},"icon":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":93},"rarity":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":93},"tier":{"actual_field":null,"exists":false,"records_with_field":0,"non_empty":0,"empty":0,"missing":93}}`
- ID 校验：`{"applicable":true,"field":"itemId","unique_count":91,"duplicate_id_count":2,"duplicate_ids":[5,7],"duplicate_id_occurrences":{"5":2,"7":2},"duplicate_extra_record_count":2,"empty_id_count":0}`
- 补充资产字段：`{"image":{"actual_field":"image","exists":true,"records_with_field":87,"non_empty":87,"empty":0,"missing":6},"modifiers":{"actual_field":"modifiers","exists":true,"records_with_field":6,"non_empty":6,"empty":0,"missing":87}}`（`image` 是实际字段，不冒充 `icon`。）
- 前 3 条精简样例：

```json
[
  {
    "itemId": 5,
    "capType": "a07bf227-092f-47b5-9137-0b391fa98520",
    "image": "/lol-game-data/assets/ASSETS/Characters/Ahri/Skins/Skin85/UI/AhriLoadScreen_Augments_Border_Starter.png",
    "modifiers_count": 0,
    "modifier_keys": []
  },
  {
    "itemId": 2,
    "capType": "c941de7a-2152-40cd-b4d1-0731aaaf675a",
    "image": null,
    "modifiers_count": 3,
    "modifier_keys": [
      "EmoteModifier",
      "ObjectiveGraffitiModifier",
      "centeredLCOverlayPath",
      "modifierType",
      "socialCardLCOverlayPath",
      "tileLCOverlayPath",
      "uncenteredLCOverlayPath"
    ]
  },
  {
    "itemId": 6,
    "capType": "c941de7a-2152-40cd-b4d1-0731aaaf675a",
    "image": null,
    "modifiers_count": 1,
    "modifier_keys": [
      "ObjectiveGraffitiModifier"
    ]
  }
]
```

## default 与 zh_CN 比较

- cherry-augments ID 集：`{"default_count":641,"zh_cn_count":641,"common_count":641,"sets_equal":true,"multisets_equal":true,"default_only_count":0,"zh_cn_only_count":0,"default_only_ids":[],"zh_cn_only_ids":[],"stable_field_mismatch_counts":{"augmentNameId":0,"augmentSmallIconPath":0,"rarity":0},"same_localized_name_count":0}`
- augment-lists：`{"json_values_equal":true,"sha256_equal":true,"per_record_member_sets":[{"index":0,"default_count":44,"zh_cn_count":44,"default_only_count":0,"zh_cn_only_count":0,"default_only":[],"zh_cn_only":[]},{"index":1,"default_count":220,"zh_cn_count":220,"default_only_count":0,"zh_cn_only_count":0,"default_only":[],"zh_cn_only":[]},{"index":2,"default_count":188,"zh_cn_count":188,"default_only_count":0,"zh_cn_only_count":0,"default_only":[],"zh_cn_only":[]}]}`

## 来源容器证据

- 结论：**未确认**。
- 理由：审计范围内未发现提取命令日志或输入 WAD/容器路径记录；现有 manifest 仅记录哈希字典来源，TOML 仅含进度显示配置或为空，zip.sha256 仅校验工具压缩包。不得从提取目录名反推容器。
- 发现日志：`[]`
- 已核验证据文件：`[{"full_path":"F:\\Realworld\\lol\\outputs\\tmp\\game_assets\\hashes\\manifest.json","bytes":1832,"sha256":"4b01214636708a4956e8ab2cb63204bc2cd548a9990bba462f533696c1a400c1"},{"full_path":"F:\\Realworld\\lol\\outputs\\tmp\\game_assets\\wadtools.toml","bytes":22,"sha256":"87d1d0db5035017a25898dff7f3cbca13fc28b65e403ca361917e8c287f9d3c6"},{"full_path":"F:\\Realworld\\lol\\outputs\\tmp\\game_assets\\wadtools-0.5.7\\wadtools.toml","bytes":0,"sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},{"full_path":"F:\\Realworld\\lol\\outputs\\tmp\\game_assets\\wadtools-0.5.7-windows-x64.zip.sha256","bytes":66,"sha256":"b93d4fb6564d1af642bb780d3e0a5fe8afd50022368b1e873c16b97cacb3bb3a"}]`

## 质量校验

- JSON 解析：`{"passed":true,"errors":[]}`
- 重复 ID：`{"zh_cn_cherry_augments":1,"default_cherry_augments":1,"default_skinaugments":2}`
- 空名称：`{"zh_cn_cherry_augments":0,"default_cherry_augments":0}`
- 空图标：`{"zh_cn_cherry_augments":0,"default_cherry_augments":0}`
- 注意：cherry-augments 的 `simpleNameTRA` 在两种 locale 中均为空；主名称 `nameTRA` 不为空。skinaugments 只有 `image` 字段，未把它错误映射为 `icon`。

## 未决风险

- 本次文件没有 description 字段；报告只证明“已审计提取集内不存在”，不外推游戏安装目录或其他未审计容器。
- 来源 WAD/容器路径未被现有日志或配置记录，状态保持“未确认”。
- skinaugments 只存在 default 提取版本，本次范围内没有 zh_CN 对应文件可比较。

## 复核命令

```powershell
python outputs\tmp\game_assets_b2\verify_wave1_b2.py
Get-FileHash -Algorithm SHA256 outputs\wave1_game_assets.md,outputs\wave1_game_asset_candidates.json
```

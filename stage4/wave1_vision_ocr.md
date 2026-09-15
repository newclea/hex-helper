# Wave1-C 视觉样本、OCR 与三卡识别路线审计

审计时间：2026-08-25 17:19:06 +08:00  
工作目录：`F:\Realworld\lol`  
审计性质：只读素材盘点、环境验证与最小内存实验；未安装依赖，未编写正式识别器。

## 1. 结论

1. 工作区在审计开始时为空；普通文件、隐藏文件、`AGENTS.md`、图像、视频、Replay 与 OCR 模型均为 0。样本 SHA-256 集为空，尺寸清单无条目。
2. 没有真实海克斯选择截图，因此没有执行卡片候选区、标题 ROI 或真实 OCR 实验。当前不能声称三卡检测召回率、框选 IoU、标题 OCR 准确率、端到端三卡准确率、误接受率、跨分辨率鲁棒性或运行时延迟。
3. 当前 PATH 上的 Python 是 `D:\Env\Python311\python.exe`（3.11.6），不在 venv/Conda 中。`cv2`、Pillow、NumPy、ONNX Runtime 可导入；`pytesseract`、PaddleOCR、PaddlePaddle、EasyOCR 不可导入，且没有 `tesseract` 可执行文件。
4. OpenCV + NumPy + Pillow 的内存烟测通过，证明现环境能做图像解码、边缘/轮廓与 ROI 预处理。它不证明任何游戏截图识别能力。
5. 不应现在拍板 OCR 引擎。Phase 1 应先建立真实、按会话分组的 ImageReplay 黄金集，再对“几何场景门控 → 标题 ROI → 闭词表 OCR/视觉匹配 → 时序投票 → 低置信拒识”级联做同集对比。

## 2. 范围与约束执行

- 只扫描 `F:\Realworld\lol`，没有扫描用户无关目录或外部游戏目录。
- 输入盘点排除了本任务创建的 `F:\Realworld\lol\outputs`，避免审计产物污染样本计数。
- 仅创建/写入：
  - `outputs\wave1_vision_ocr.md`
  - `outputs\tmp\vision_audit\audit_manifest.json`
  - `outputs\tmp\vision_audit\imagereplay.schema.json`
- 没有修改 `src`、`scripts`、`result`、`stage*` 或其他 Agent 报告；这些路径在开工时均不存在。
- 终检时发现其他任务已并发写入 `outputs` 下的独立路径；本任务未修改这些文件，并继续将整个 `outputs` 排除在输入样本盘点之外。该并发变化不改变“开工快照非输出文件为 0、真实视觉样本为 0”的结论。
- 没有读取 LoL 进程内存，没有注入、Hook、抓协议、控制输入或触碰 Vanguard。
- `rg --files -g 'AGENTS.md'` 返回退出码 1 且无输出；对 `Get-ChildItem -Force` 与 `rg --files --hidden` 的复核也显示开工时工作区条目/文件均为 0。因此没有额外仓库级指令可读取。

## 3. 样本证据

### 3.1 搜索口径

- 图像：`.png .jpg .jpeg .bmp .webp .tif .tiff .gif`
- 视频：`.mp4 .m4v .avi .mkv .mov .webm .wmv .flv .mpeg .mpg .ts .m2ts .vob .ogv`
- Replay：`.replay .rofl .lrf .lolreplay`，并补查文件名含 `replay` 的普通文件。
- OCR/推理模型补查：`.onnx .pdmodel .pdparams .engine .tflite .pt .pth`

### 3.2 盘点结果

| 类别 | 数量 | 尺寸 | SHA-256 |
|---|---:|---|---|
| 工作区非输出普通文件 | 0 | — | — |
| 图像 | 0 | 无条目 | 空集合 `[]` |
| 视频 | 0 | 无条目 | 空集合 `[]` |
| Replay | 0 | 无条目 | 空集合 `[]` |
| OCR/推理模型 | 0 | — | — |

机器可读证据见 `outputs\tmp\vision_audit\audit_manifest.json`。

## 4. Python/OCR 环境证据

### 4.1 解释器

- `Get-Command python`：`D:\Env\Python311\python.exe`
- `python --version`：`Python 3.11.6`
- `sys.prefix == sys.base_prefix == D:\Env\Python311`；`VIRTUAL_ENV` 与 `CONDA_PREFIX` 均为空。
- `py -0p` 还注册了默认 3.12 路径 `D:\Env\Python312\python.exe`，但 `Test-Path` 为 `False`，`py -3.12 --version` 无法创建进程。它不是可用的当前环境。

### 4.2 导入与版本

| 模块 | import | 模块/发行版版本 | 关键证据 |
|---|---|---|---|
| `cv2` | 成功 | 4.13.0 / `opencv-python 4.13.0.92` | 来自 Python 3.11 `site-packages\cv2` |
| `PIL` | 成功 | 12.2.0 / `Pillow 12.2.0` | 来自 Python 3.11 `site-packages\PIL` |
| `numpy` | 成功 | 2.4.4 | 来自 Python 3.11 `site-packages\numpy` |
| `onnxruntime` | 成功 | 1.26.0 | providers：`AzureExecutionProvider`、`CPUExecutionProvider` |
| `pytesseract` | 失败 | 未安装 | `ModuleNotFoundError: No module named 'pytesseract'` |
| `paddleocr` | 失败 | 未安装 | `ModuleNotFoundError: No module named 'paddleocr'` |
| `paddle` | 失败 | 未安装 | `ModuleNotFoundError: No module named 'paddle'` |
| `easyocr` | 失败 | 未安装 | `ModuleNotFoundError: No module named 'easyocr'` |
| `tesseract` CLI | 不存在 | — | `Get-Command tesseract` 明确返回 `Found=false` |

结论边界：

- 当前能执行图像预处理和几何分析。
- ONNX Runtime 本身不是 OCR；工作区没有 `.onnx` OCR 模型，也没有可验证的字典、预处理契约或模型许可证。
- 当前无法执行 Tesseract 或 PaddleOCR 中文识别，更不能比较两者在真实海克斯标题上的效果。

### 4.3 最小非破坏性实验

仅在内存中生成 `480×240` 黑底图并画 3 个白色矩形，执行 BGR→灰度、Canny、外轮廓检测，再经 Pillow 做 PNG 内存往返：

- edge pixels：3336
- external contours：3
- Pillow round-trip：`480×240`

该实验验证 `cv2`/NumPy/Pillow 的基本互操作，没有写合成图片，也没有把合成矩形当作真实卡片样本。

## 5. 无真实截图时不能声称的指标

以下指标目前均为“未测量”，不是 0，也不是失败：

- 海克斯选择场景门控的召回率、误触发率与 PR/ROC 曲线。
- 三张卡候选区域的单卡召回、all-three 成功率、bbox IoU、左右顺序正确率。
- 标题 ROI 对真实中文字形的覆盖率、背景污染率及动画阶段稳定性。
- 任一 OCR 后端的中文标题 exact match、字符错误率（CER）、top-k、置信度校准和吞吐。
- 闭词表纠错的收益及错误强制匹配率。
- 端到端有序三卡 exact-screen accuracy、拒识覆盖率、错误接受率。
- 1080p/1440p/768p/超宽屏、窗口模式、DPI、UI 缩放、HDR、色盲模式、不同补丁下的鲁棒性。
- 视频/Replay 解码、时序投票稳定性、首次稳定结果延迟。
- 目标机器上的 CPU/GPU 延迟、内存峰值和持续运行资源占用。

因此，本轮没有选择“最佳 OCR”，也没有生成识别率数字。

## 6. ImageReplay 黄金样本格式

建议目录：

```text
golden/
  manifest.jsonl
  frames/
    <sha256>.png
  catalog/
    augments.<game_patch>.zh-CN.json
```

完整记录契约见 `outputs\tmp\vision_audit\imagereplay.schema.json`。关键设计如下：

1. 每个源帧以无损 PNG 保存，文件名采用源字节 SHA-256；manifest 同时记录哈希、字节数、宽高和色彩空间。ImageReplay 启动前必须复验，任何不一致直接判测试夹具损坏。
2. bbox 统一为源图像像素坐标的半开区间 `[x, y, width, height]`，禁止只存百分比坐标；运行时可派生归一化坐标。
3. `scene` 明确区分 `augment_choice_stable`、`augment_choice_transition`、`negative`、`invalid_capture`。稳定正样本必须标三卡及三标题 ROI；过渡帧默认期待拒识。
4. 每卡记录 left/center/right、card bbox、title bbox、补丁内稳定 `catalog_id`、原始标题、规范化标题、可见性和遮挡率。
5. 视频帧记录 `sequence_id`、`frame_index`、`pts_ms`；同一局/同一会话/近重复帧共享 `split_group_id`，训练、标定、测试不得跨组泄漏。
6. 记录 `game_patch`、`zh-CN`、窗口模式、UI 缩放、系统 DPI、HDR、色盲模式。未知值显式为 `null/unknown`，不得猜填。
7. `expected.accept=false` 用于过渡、严重遮挡、不支持布局与未知标题；测试必须验证拒识，而不是只测“总能给答案”。

ImageReplay 的执行契约：读取原始 PNG 字节 → 验哈希/尺寸 → 走与实时截图完全相同的 decode 和识别入口 → 输出结构化结果及 reason code → 比较 scene、bbox、顺序、标题和拒识。不得在测试专用路径中跳过预处理或直接读取标签。

## 7. 后续采样标准

### 7.1 采集原则

- 只采本地、授权测试会话的原始画面；优先无损 PNG。视频只能作为时序样本来源，抽帧后仍保存帧 PTS 与源序列 ID。
- 每个稳定选择界面采 1 个独立代表帧；相邻帧只能进入时序压力集，不能虚增独立样本量。
- 每个会话同时采集前置负样本、进入动画、稳定三卡、hover/selection overlay、退出动画及外观相似负场景。
- 同一标题应覆盖短/长标题、低频汉字、不同卡位、不同背景/等级视觉；标题目录必须绑定补丁版本。
- 强制记录：原生分辨率、窗口模式、UI scale、Windows DPI、HDR、色盲模式、补丁、语言、采集方式。
- 两人复核标题文本与 ROI；分歧仲裁后再进入 gold。原始帧不可二次缩放、锐化或覆盖标记。

### 7.2 必须覆盖的分层

- 分辨率/比例：至少目标产品承诺的 1366×768、1920×1080、2560×1440；若承诺超宽屏，再单列 21:9，不与 16:9 混算。
- 模式：全屏、无边框、窗口化；不支持的组合必须在入口显式拒识。
- 状态：稳定、淡入淡出、移动/模糊、hover、选中遮罩、部分遮挡、截图裁切、压缩视频帧。
- 负样本：商店、装备/武器库选择、结算、加载、主菜单、弹窗及任何三列相似 UI。
- 版本：每个受支持补丁独立保留目录与词表；补丁更新先跑回归再提升为支持状态。

### 7.3 初始数量门槛

这是采样/验收设计，不是本轮已达到的数字：

- 至少 300 个相互独立的稳定正样本组。若 0 次场景漏检，按 rule-of-three，漏检率的一侧 95% 上界约为 1%。
- 至少 600 个相互独立的负样本组。若 0 次误触发，误触发率的一侧 95% 上界约为 0.5%。
- 至少 600 个独立、非拒识的卡片标题结果。若 0 次错收，错误接受率的一侧 95% 上界约为 0.5%。
- 每个受支持分辨率/窗口模式组合至少 30 个稳定正样本和 30 个负样本；不足时只报告该组合的原始计数，不给稳定百分比结论。
- 目录中的每个目标标题至少出现 1 次；高频/高风险易混淆标题至少来自 3 个不同会话。未覆盖标题单列，不计入“全词表已验证”。
- 至少 20 条完整时序序列，包含出现前、过渡、稳定和退出阶段；相邻帧按序列整体切分。

若出现错误，不再使用 rule-of-three；报告原始分子/分母和 Clopper-Pearson 95% 区间。

## 8. 推荐三卡识别级联

### 8.1 场景与三卡几何门控

1. 验证输入尺寸、色彩通道、支持的分辨率/长宽比；不支持即拒识。
2. 在真实样本标定后设置屏幕级粗 ROI，使用灰度边缘/线段/连通区域生成矩形候选。
3. 以“三个尺寸近似、中心线近似水平、间距规律、互不重叠、左右顺序稳定”的组合作为主信号。颜色只做辅助，不作硬门槛，以降低 HDR、等级配色和色盲模式影响。
4. 门控失败、候选不是 3 张或处于动画状态时不调用 OCR，输出明确 reason code。

不要在无样本时硬编码卡片比例、绝对坐标、边缘阈值或颜色范围；这些全部从 calibration split 标定。

### 8.2 标题 ROI 与预处理

1. 每张卡的人工 title bbox 是标定基准；相对卡框位置只能由标定集统计中位数与分位数得到。
2. 生成有限的预处理分支：原色/灰度、CLAHE、亮/暗文字两种极性、2–4× 放大、少量 padding。分支数必须由消融实验裁剪，防止延迟和“碰巧识别”。
3. 在进入 OCR 前做清晰度、文本边缘密度和 ROI 越界检查；低质量直接拒识。

### 8.3 闭词表标题识别

1. 海克斯标题属于补丁绑定的闭词表，识别接口应返回原始文本、top-k、原始置信度与 backend ID。
2. 首轮在同一黄金集上对比经批准的本地中文 OCR backend；当前环境没有可运行 OCR backend，ONNX Runtime 也没有模型，故本轮不指定 PaddleOCR/Tesseract/ONNX 模型胜者。
3. 将 OCR top-k 与补丁词表做规范化匹配；只在编辑距离、字符混淆代价和 top1-top2 margin 同时过阈值时接受。禁止“总是取最近标题”，否则未知新标题会被高置信错配。
4. 固定字体/布局若成立，可增加标题图像模板或轻量视觉分类器作为独立信号。OCR 与视觉信号一致时接受；冲突时降级或拒识。模板必须来自合法本地 gold，不能来自网络截图。
5. 三卡按 x 中心从左到右排序；只有三个标题均通过各自阈值，才输出完整三卡结果。

### 8.4 时序稳定与拒识

- 在 250–1000 ms 窗口、2–5 个独立观察之间网格搜索；选择满足错误锁定上限时延迟最低的组合，而不是先拍脑袋固定“3 帧”。
- 只有场景几何稳定且各槽位标题一致才锁定；动画、OCR 冲突、词表未知、遮挡或布局异常均拒识。
- 输出 `accepted`、三个槽位、置信度、backend、阈值版本、补丁词表版本和 reason code，便于线上回放与审计。

## 9. 阈值标定方法

1. 按 `split_group_id` 切分 development/calibration/test；同一会话、同一视频和感知哈希近重复组不可跨 split。test 在阈值冻结前不可查看。
2. development 只用于特征和候选方案；calibration 用于阈值；test 只运行一次形成最终结论。
3. 几何特征建议记录：候选数、宽高/面积离散度、中心 y 离散度、左右间距比、重叠率、边缘密度、矩形度。对每个特征保留正负分布，不靠单张截图定值。
4. 场景阈值 `T_scene`：在 calibration 上选择满足负样本 FPR ≤ 0.5% 的最大召回点；若没有点满足，则架构不达标，不通过降低统计标准掩盖。
5. bbox/ROI 阈值：以人工框 IoU 和标题文字像素覆盖为准；不同分辨率只有在样本量足够时才单独标定，否则采用更保守的全局阈值并报告分层结果。
6. OCR 置信度不可跨 backend 直接比较。每个 backend 用 calibration 的正确/错误标签做 isotonic 或 logistic calibration，并单独选择 `T_accept` 与 top1-top2 `T_margin`，目标为错误接受率 ≤ 0.5%。
7. 词表编辑距离只作为特征，不是通行证；遇到词表外标题、并列最近候选或低 margin 必须拒识。
8. 时序窗口与票数在 calibration 网格搜索，以“错误锁定率达标后 P95 延迟最低”为目标。
9. 阈值、词表、模型、预处理和数据集都写版本号；冻结后在独立 test 报告点估计、分子/分母及 95% 区间。

## 10. Phase 1 可量化测试与建议门槛

以下是建议验收门槛，不是本轮结果：

| 链路 | 指标 | 建议门槛 |
|---|---|---:|
| 数据完整性 | PNG 哈希/尺寸/标签/schema 校验 | 100% 通过 |
| 场景门控 | stable-positive recall | ≥ 99.0% |
| 场景门控 | negative false-positive rate | ≤ 0.5% |
| 三卡定位 | 单卡 IoU ≥ 0.75 的 recall | ≥ 99.0% |
| 三卡定位 | 同屏三卡全部定位且顺序正确 | ≥ 98.0% |
| 标题识别 | per-card exact match（micro） | ≥ 97.0% |
| 标题识别 | 按标题 macro exact match | ≥ 95.0% |
| 端到端 | stable 屏幕有序三标题全对 | ≥ 92.0% |
| 安全拒识 | 非拒识结果中的错误接受率 | ≤ 0.5% |
| 覆盖率 | stable 正样本非拒识率 | ≥ 90.0% |
| 时序 | 从人工标注稳定时刻到锁定的 P95 | ≤ 500 ms |
| 稳定性 | 锁定后 2 s 内错误翻转 | 0 次/测试集 |

同时必须输出：按分辨率、窗口模式、标题长度、质量 flags、补丁与 backend 的分层结果；整体均值不能掩盖某个分层失效。性能测试记录目标 CPU/GPU、线程数、输入尺寸、P50/P95/P99 延迟与峰值 RSS；在产品给出硬件/帧率预算前，不把本机烟测延迟设为正式门槛。

## 11. 主要失败场景

- DPI/窗口缩放导致截图像素与逻辑坐标不一致：以实际 decoded width/height 为准，入口记录 DPI；不支持布局拒识。
- 淡入、位移、hover、选中光效破坏边缘或标题：以时序稳定门控和 transition 标签处理，不拿过渡帧强行 OCR。
- HDR、色盲模式、等级颜色变化：几何/边缘为主，颜色为辅；每种模式分层评估。
- 低分辨率、压缩、运动模糊使汉字笔画粘连：质量门控、多尺度有限分支；失败时拒识。
- 短标题、同字前缀、形近字造成闭词表歧义：使用 top-k margin、代价矩阵与独立视觉信号；禁止最近词强制命中。
- 新补丁新增/改名标题：词表绑定 patch；未知词返回 `unknown_catalog`，不映射到旧标题。
- 三列相似 UI 误触发：负样本必须覆盖商店、装备选择、结算和弹窗；场景门控先于 OCR。
- 相邻视频帧泄漏导致虚高：按 session/sequence/perceptual-duplicate group 切分，并报告独立组数。
- OCR backend 原始 confidence 不可比：按 backend 单独校准；升级模型后重新标定。
- 只在单一分辨率过拟合绝对坐标：按比例特征 + 分层测试；未覆盖分辨率明确不支持。

## 12. 证据命令

所有命令的工作目录均为 `F:\Realworld\lol`，且没有安装操作。

```powershell
Get-ChildItem -LiteralPath 'F:\Realworld\lol' -Force
rg --files --hidden .
rg --files -g 'AGENTS.md' .
```

```powershell
$extensions = @('.png','.jpg','.jpeg','.bmp','.webp','.tif','.tiff','.gif',
  '.mp4','.m4v','.avi','.mkv','.mov','.webm','.wmv','.flv','.mpeg','.mpg',
  '.ts','.m2ts','.vob','.ogv','.replay','.rofl','.lrf','.lolreplay')
Get-ChildItem -LiteralPath 'F:\Realworld\lol' -File -Recurse -Force |
  Where-Object { -not $_.FullName.StartsWith('F:\Realworld\lol\outputs') } |
  Where-Object { $extensions -contains $_.Extension.ToLowerInvariant() }
```

```powershell
Get-Command python,py
python --version
py -0p
Test-Path -LiteralPath 'D:\Env\Python312\python.exe'
py -3.12 --version
Get-Command tesseract -ErrorAction SilentlyContinue
```

Python 导入证据由 `python -B -c` 逐模块执行 `importlib.import_module`，同时用 `importlib.metadata.version` 读取发行版版本；`-B` 禁止写 `.pyc`。缓存相关环境变量指向 `outputs\tmp\vision_audit`，未触发模型下载。

最小 CV 烟测命令仅在内存中创建 NumPy 图像，调用 `cv2.cvtColor`、`cv2.Canny`、`cv2.findContours`，再用 `BytesIO` 完成 Pillow PNG 往返。

## 13. Phase 1 决策

当前决策为 **No-Go for OCR engine selection / Go for gold-set acquisition**：

- 允许下一步：按 schema 采集、复核、分组并冻结真实黄金集；用当前 OpenCV/Pillow/NumPy 先做结构探索。
- 暂不允许：声称任何识别率；将 ONNX Runtime 当作 OCR；在没有同集基准时选定 PaddleOCR/Tesseract；用网络截图、合成图或相邻帧替代真实独立样本。
- OCR 依赖或模型的引入应在黄金集准备后单独评审来源、许可证、离线可部署性、中文效果、包体和延迟；本审计没有安装任何依赖。

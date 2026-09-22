# Windows 联调交接（2026-09-22）

面向：在 Windows 上打开小猫 + LoL 继续联调 / 开发的同学。  
仓库：`https://github.com/newclea/hex-helper`  
当前联调主干：`github/main` @ `0e94286`（`fix: recover same-round hex refresh and speech playback`）

---

## 1. 仓库怎么用（先读这段）

本仓库在 GitHub 上有两条常用线，**不要混**：

| 分支 | 用途 | 怎么跑 |
|---|---|---|
| `main` | **发布包 / 联调主干**。Python overlay + 预编译 `lol_augment_assistant.exe`。没有完整 C++ 源码树（`src/` 等在 publish 时删掉了）。 | `git pull origin main` 后双击 `一键启动小猫.cmd` |
| `fix/death-force-ocr-engine` | **识别引擎源码分支**。含 CMake、`src/`、Windows CI workflow。 | 需要重编 exe 时才用 |

远端约定（本机可能同时有两个 remote）：

- `github` → `https://github.com/newclea/hex-helper.git`（**联调看这个**）
- 工蜂 `origin` 可忽略（本次联调不依赖）

Windows 起步：

```bat
git fetch github
git checkout main
git pull github main
一键启动小猫.cmd
```

排查专用入口：

- `一键启动小猫-Debug-海克斯刷新.cmd` → 只多打 `DEBUG[hex-refresh]`
- `一键启动小猫-Debug-语音.cmd` → 只多打 `DEBUG[speech]`
- `一键启动小猫-Debug-赛果.cmd` → 只多打 `DEBUG[game-result]`

配置文件（**不是仓库根目录的 overlay.json**）：

```
%LOCALAPPDATA%\LoLRecognitionOverlay\overlay.json
```

Agent / 语音开关都写这里。仓库根 `overlay.json` 只作样例，且不得提交真实 token。

---

## 2. 我们已经做了什么（已进 `main`）

提交：`0e94286`（2026-09-22 傍晚推上 GitHub `main`）。

### 2.1 同轮刷新海克斯：不再误报「校验未通过」

**现象（修前）**  
7/11/15 级刷新候选后，气泡先「候选已更新 / 三张名字已读到，结果尚未确认」，再变成「三张海克斯已读到，但本轮校验未通过」。

**原因**  
同一 `offer_round` 会话里已经接受过一组 offer，刷新出的新三张被会话拒收；旧 exe 报 `session_invalid_offer`，UI 当成校验失败。

**`main` 上的修复（Python，兼容旧 exe）**

- 刷新态 + 读到三张全新候选 + `session_invalid_offer` / `offer_round_conflict` → **不渲染「校验未通过」**
- 请求重启识别 worker，让新 offer 能重新入会话
- 新增 `hex-refresh` 诊断子模式

### 2.2 离线语音：单次失败不再永久静音

**现象（修前）**  
常能听到开场白，之后整局静音；日志里出现 `speech failed id=local-2 kind=champ_select`。

**原因**  
worker 把「单次请求播放失败」当成致命错误，直接 `_disable` 整条语音链路。

**修复**  
请求级失败只失败这一句；只有无 `request_id` 的 worker 级错误才永久关闭。

### 2.3 Agent 失败回退

推荐态会请求 Agent；失败时回退本地模板「推荐选择{名字}，当前玩法{玩法}。」并照常进语音队列。  
**不阻塞气泡 / OCR / 推荐。**

### 2.4 C++ 侧（尚未编进 `main` 的 exe）

分支 `fix/death-force-ocr-engine` @ `1e43732`：

- 新增 `SessionRuntimeError::OfferRoundConflict` → reason `offer_round_conflict`
- 与 Python 刷新重启逻辑配套

`main` 里跟踪的 exe 仍是更早一版（同轮刷新时仍可能报 `session_invalid_offer`）。**Python 已兼容旧 reason，联调可不重编。**  
只有要验证明确的 `offer_round_conflict` 路径时，才需要在引擎分支重编并把 exe 更新进 `main`。

---

## 3. 固定语音产品约定（已核对实现）

| # | 场景 | 文案 | 实现状态 |
|---|---|---|---|
| 1 | 打开小猫 | 召唤师你好，我是你的联盟专属陪玩悠米！快去开启一场紧张刺激的海克斯大乱斗吧。 | 本地模板；UI 首次 greeting 动画时触发 |
| 2 | 选英雄 | 根据当前英雄强度，推荐选择A、B、C三个英雄哦。 | 本地模板；短名；**当前代码要求正好 3 个名字才播**（见待办） |
| 3 | 海克斯推荐 | Agent 生成口播 | 已接；失败回退本地摘要 |
| 4 | 赛果 | 耶，赢啦！ / 惜败惜败，再开一局吧。 | 本地模板；依赖 LCU 赛果字段 |

赛果字段（恒祥 / LCU）：

- 路径：`GET /lol-end-of-game/v1/eog-stats-block`
- 阶段：`PreEndOfGame` / `WaitingForStats` / `EndOfGame`
- `myTeamStatus` → `WIN` / `LOSS`
- `gameId` → 与本局 `match_id` 对齐后播一次

---

## 4. 联调中已确认的问题（正要做 / 先别绕远）

### 4.1 【优先】选英雄语音：1～2 个也要播；开场白时机 + 推荐英雄未播

用户反馈：

1. 气泡只有 1～2 个推荐英雄时，语音也要播。
2. 「召唤师你好…」是在**进入选英雄界面之后**才听到，而不是刚打开小猫。
3. 选英雄推荐语音没有播。

代码结论（尚未改）：

- `speech_policy` 与 `app._speech_view` 都写死了 `len(names) == 3` 才注入/播报 → **少于 3 个直接静默**。这直接解释了「没有播报推荐选择英雄」。
- 开场白与预热文本相同；预热走**无 callback** 合成，不依赖 numpy。选人句是新文本，走**流式 callback**，需要 numpy。
- 当前 wheelhouse **故意不带 numpy**（`vendor/speech/wheels/cp311-win_amd64` 只有 5 个包）。因此常见现象是：**只有开场白能出声，后续句失败**，日志类似：
  - `Offline speech request failed: offline speech failed: No module named 'numpy'`
- 开场白「拖到选人界面才听到」：多半是 TTS 模型首次加载较慢（十几秒级），队列里的 startup 在 adapter ready 后才真正开播，时间上刚好叠在进选人；**不一定是触发点绑在 ChampSelect**。用 `一键启动小猫-Debug-语音.cmd` 看 `startup` / `champ_select` 的 `queued` / `started` 时间戳即可验证。

建议 Windows 上优先改：

1. 放开 1～2 个英雄也播（改 `speech_policy.py` + `app._speech_view`，同步单测）。
2. 修 TTS：要么把 numpy 打进 wheelhouse 并 bootstrap 安装；要么流式失败时回退到无 callback 合成（恢复仅开场白能播之外的句子）。

### 4.2 Agent：`network_error`（先不管口播内容时可搁置）

配置读对之后日志形态：

```
agent request started query_id=... 
error=network_error  status=None  elapsed_ms≈30
```

原因：endpoint 是北极星名  
`stream-server-online-openapi.turbotke.production.polaris:8080`  
游戏机 Windows 通常解析不了。需要可直连地址或本机转发。  
失败已回退本地推荐摘要，**不影响气泡**。

配置位置与合法 JSON 示例见 README「Agent 文案能力测试」。注意逗号；写错文件或 JSON 非法会变回 `not_configured`。

### 4.3 同轮刷新（应用 `main` 后应已好）

用 Debug-海克斯刷新入口打一局 7 级刷新，确认：

- 不再长期停留「校验未通过」
- 日志有 `DEBUG[hex-refresh]` 的 conflict / worker restart

若仍卡死，把 `reason=` 与 `ids=` 贴出。

---

## 5. Windows 联调清单（建议顺序）

1. `git pull github main`，确认 HEAD = `0e94286`（或更新）。
2. 确认 `%LOCALAPPDATA%\LoLRecognitionOverlay\overlay.json` 合法；语音开着。
3. 双击 `一键启动小猫-Debug-语音.cmd`，**先只开小猫**：
   - 是否几秒～十几秒后听到开场白（模型冷启动）。
   - 日志：`kind=startup` → `speech started` / `finished`，还是 `No module named 'numpy'`。
4. 再开 LoL，进海克斯大乱斗选人：
   - 气泡有几个推荐英雄；
   - 有没有 `kind=champ_select` 入队；没有则多半是「不足 3 个」门槛。
5. 进局三选一 / 刷新：用 Debug-海克斯刷新看气泡与 `hex-refresh` 日志。
6. Agent 先可忽略；要测再换可达 endpoint。
7. 赛果：用 Debug-赛果，确认 `gameResult` / `myTeamStatus`。

Python 单测（有环境时）：

```bat
set PYTHONPATH=scripts\product;scripts\recognition_overlay;scripts\phase4
py -3.11 -m unittest discover -s tests -p "test_*.py"
```

---

## 6. 正要做什么（建议排期）

| 优先级 | 事项 | 说明 |
|---|---|---|
| P0 | 选人语音支持 1～2 个英雄 | 产品已明确要求；改 policy + `_speech_view` + 测试 |
| P0 | 修好「除开场白外都播不出」 | numpy 进包 **或** 无 callback 回退；否则选人/赛果/本地推荐摘要都可能听不到 |
| P1 | 确认开场白触发时机 | Debug-语音对一下 queued/started；若产品要求「窗口一出现就听」，再考虑等 adapter ready 后再 publish 或加强 preload |
| P1 | 同轮刷新回归 | 7/11/15 刷新；一般不需重编 exe |
| P2 | Agent 可达 endpoint | 换公网/转发地址；与口播文案无关时可后置 |
| P2 | 把 `offer_round_conflict` exe 编进 `main` | 仅当要验证新 C++ reason；流程：引擎分支 CI 出 exe → 只更新 `outputs/tmp/build/bin/lol_augment_assistant.exe` |

---

## 7. 关键路径速查

| 路径 | 作用 |
|---|---|
| `scripts/product/speech_policy.py` | 固定句触发与文案 |
| `scripts/recognition_overlay/app.py` | `_speech_view` / greeting / Agent 挂载 |
| `scripts/product/offline_speech_worker.py` | TTS 合成与播放；流式 callback |
| `scripts/product/agent_companion.py` / `agent_text.py` | Agent 口播与回退 |
| `scripts/recognition_overlay/view_model.py` | OCR 气泡、「结果尚未确认」、刷新态 |
| `scripts/phase4/lcu_champ_select.py` | LCU 选人 / 赛果解析 |
| `vendor/speech/wheels/cp311-win_amd64/` | 离线语音 wheel（当前无 numpy） |
| `outputs/tmp/build/bin/lol_augment_assistant.exe` | 识别引擎二进制（`main` 跟踪） |

---

## 8. 一句话现状

**`main` 已修好同轮刷新误报与「说完一句就永久静音」；可在 Windows 上直接拉 `main` 联调。当前阻塞听感的是：选人语音仍要求 3 个名字，以及流式 TTS 缺 numpy（开场白靠预热能播、后续句易挂）。Agent 在游戏机上因 Polaris 地址连不上，可后置。**

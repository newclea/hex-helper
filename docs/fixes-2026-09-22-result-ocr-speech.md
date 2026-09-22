# 结算、存活误触发与语音链路修复

## 结算播报

已有 LCU `/lol-end-of-game/v1/eog-stats-block` 接口，但旧解析只读 `myTeamStatus`。
该字段可能为空，结算团队使用 `isPlayerTeam=true` 与布尔 `isWinningTeam` 表达本人的胜负。
现兼容这两种格式；本队不唯一、布尔类型不正确或字段相互矛盾时不猜测。
参考原始响应样本：https://gist.github.com/xadamxk/8b3c795d98773d0879eb1b02742bee0e 。

`Lobby/Matchmaking/ReadyCheck` 也读取结算，避免离开结算页面后错过延迟数据。
只有曾进入游戏、且结果 gameId 等于已记录的本局 gameId 才播报；启动时的旧结算不会播。
胜负语音可打断普通优先级消息。新增常规日志 `game result accepted` 记录 ID、结果和字段来源。

实机查询时为 InProgress，结算端点 HTTP 404，不能取得用户所述上一局的原始结算响应。
因此已修复可验证的解析遗漏与延迟处理，但没有声称现场观察到下一次胜利播报。

## 存活误触发

日志在 22:57:37 记录：level=9、isDead=false、confirmed=0、accepted=false、识别 0/3，
却出现原始/稳定 visible=true；22:57:39 排入识别语音，首次死亡在 22:58:07。
根因有两处：首次规则仅限制 level>=3，未限制上界与探测时长；原始 visible 又能反向打开 OCR。

现在初始存活探测为 3～6 级且探测窗口未过期（90 秒）；满足规则的死亡/复活扫描仍保留。
原始检测必须处于允许扫描窗口，或带完整且 accepted=true 的三卡识别证据，才能打开气泡。
已有完整有效候选继续允许同轮观察。识别气泡消失后取消队列和当前播放中的 ocr_progress；
结算事件会清除识别计时，避免胜负后仍发出“正在识别”。

复现证据：`outputs/ocr-investigation-2026-09-22/reproduction.json` 为修复前；
`regression-after-fix.json` 为修复后。两组 level=9 存活用例（已确认 0/1 个）均无气泡、无语音。

## 语音

生产模型由 INT8 切换为 Melo FP32、8 个 CPU 线程，保留同一音色；新模型约 162.5 MiB。
此前六次本机样本平均 RTF=0.369、约 13.3 字/秒；23 字约 1.83 秒合成，首块约 0.39～0.65 秒。
详细方法和音频见 `outputs/tts-benchmark-2026-09-22/REPORT.md`。游戏负载变化可能影响速度。
旧 INT8 已放入 benchmark 的 `models/baseline-model.int8.onnx`，供比较复现。

欢迎/胜利/失败按 FP32 模型重新生成完整 WAV 缓存。欢迎缓存有效时 worker 先 ready，
模型后台加载；动态请求等待模型，缓存请求直接播；缺失/损坏缓存仍回退合成。
被取消的流式请求不再继续计算后续分句。

真实模型与缓存、实时静音输出的集成验证（不测声卡）：
- worker ready：0.6127 秒（包括 native 依赖初始化）。
- 缓存欢迎/胜利/失败请求到 started：0.0010 / 0.0042 / 0.0098 秒。
- 模型已加载后的动态请求到首段：0.6437 秒。
- 全部请求 finished，无 error；结果为 `results/pipeline-probe.json`。

## 验证与生效

Python 3.11 的 257 项自动化测试通过，离线资源 SHA256 校验通过。
真实设备播放此前 INT8 缓存已验证；新 FP32 本轮只做静音输出集成，未打断正在进行的游戏。
代码与资源已落盘，当前进程未热更新；本局结束后重启小猫生效。

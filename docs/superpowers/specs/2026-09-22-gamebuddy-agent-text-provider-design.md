# GameBuddy Agent 文案生成能力设计

## 状态

- 日期：2026-09-22
- 状态：已确认设计，待用户复核文档
- 范围：在 Windows GameBuddy 客户端内增加可替换的 Agent 文案生成能力

## 背景

GameBuddy 已有稳定的本地语音链路：业务状态由 `CompanionSpeechPolicy` 转为
`SpeechMessage`，再由 `SpeechService` 调用离线 TTS 播放。后续部分场景需要先由
太极 Agent 生成适合播报的文案，再交给现有离线 TTS 播放。

具体由 Agent 介入的业务场景尚未确定。本阶段只建设独立、可测试的文本生成能力，
不把 Agent 接入任何 OCR、推荐或陪伴状态。

正式产品将通过 GameBuddy 网关访问太极 Agent。为了先验证太极网络、鉴权和返回协议，
本阶段允许 Windows 测试机直接调用太极 Agent，并在本机用户配置中保存测试 Token。

## 目标

1. 提供与业务场景无关的 Agent 文本生成接口。
2. 使用太极 AppCreate 非流式接口完成首个 Provider。
3. 支持本机配置、明确超时、结构化成功或失败结果以及安全日志。
4. 提供独立的手动连通性诊断入口。
5. 保证能力未被业务调用时，现有 UI、OCR、推荐和离线语音行为完全不变。
6. 为未来切换到 GameBuddy 网关保留稳定的上层接口。

## 非目标

- 本阶段不决定 Agent 介入哪些播报场景。
- 不让 Agent 修改本地海克斯识别结果或推荐结论。
- 不把 Agent 返回文本直接接入 `SpeechService`。
- 不实现多轮记忆、流式响应、多媒体上传或 Agent 会话管理。
- 不建设正式 GameBuddy 网关、用户登录、配额或计费能力。
- 不把真实 Token、用户凭证或其他密钥提交到仓库。

## 方案选择

采用可替换 Provider 方案。上层只依赖统一的文本生成接口，首个实现为
`TaijiDirectAgentProvider`。未来网关上线后增加 `GameBuddyGatewayAgentProvider`，
无需修改业务场景对文本生成能力的调用方式。

不采用以下方案：

- 不把太极 HTTP 请求直接写进 `CompanionSpeechPolicy`，避免网络故障影响既有语音策略。
- 不新增常驻 Agent 子进程；当前没有隔离原生库或复用大型本地模型的需求，子进程会增加生命周期复杂度。
- 不在首版实现流式协议；当前目标是验证稳定的请求和响应契约，流式播放属于后续场景优化。

## 组件边界

### Agent Provider 接口

Provider 接收文案生成请求并返回结构化结果。接口不引用 UI、OCR、推荐引擎或 TTS 类型。

请求至少包含：

- `prompt`：本次生成要求。
- `context`：可选的结构化业务信息。

返回结果至少包含：

- `ok`：是否得到可用文案。
- `text`：清理后的可播报文本，失败时为空。
- `query_id`：本次调用唯一标识，用于日志追踪。
- `error_code`：稳定的客户端错误分类，成功时为空。

Provider 预期提供同步 `generate()` 接口。调用方必须在工作线程中调用它，不能在 UI
线程内执行网络请求。后续接入具体业务场景时，再由场景编排层负责异步调度、过期判断和本地文案回退。

### 太极直连 Provider

首个 Provider 调用太极 AppCreate 接口：

- 方法：`POST`
- 路径：`/openapi/app_platform/app_create`
- 鉴权：`Authorization: Bearer <token>`
- `forward_service`：`hyaide-application-22835`
- `stream`：`false`
- `query_id`：客户端为每次请求生成唯一值
- `query`：生成要求
- `messages`：仅传本次 user 消息，不启用历史会话

太极返回满足以下全部条件时才视为成功：

1. HTTP 请求成功。
2. 响应是 JSON 对象。
3. `retcode` 严格等于 `0`。
4. `result` 是清理后仍非空的字符串。

Endpoint 必须来自本机配置。办公网测试可配置太极文档给出的办公网访问地址；代码不固定
测试域名或端口，以便环境变化时无需重新构建 EXE。

### 配置加载

测试配置保存在：

```text
%LOCALAPPDATA%\LoLRecognitionOverlay\overlay.json
```

配置结构：

```json
{
  "voice_enabled": true,
  "agent": {
    "provider": "taiji_direct",
    "endpoint": "http://example.invalid/openapi/app_platform/app_create",
    "forward_service": "hyaide-application-22835",
    "token": "仅存放在测试机本地的 Token",
    "timeout_seconds": 10
  }
}
```

约束如下：

- 仓库根目录的示例配置不得包含真实 Token。
- 缺少 `agent`、Provider 未启用或字段无效时，Agent 能力返回明确的未配置状态。
- Token 不允许出现在对象字符串表示、日志、异常文本或诊断输出中。
- 配置更新继续保留不相关字段，不改变现有 `voice_enabled` 和 `league_root` 行为。
- `timeout_seconds` 必须限制在合理区间，防止错误配置造成长期阻塞。

正式网关上线后，客户端配置不再包含太极 Token、内部地址或 `forward_service`。这些字段由
网关持有，客户端只保存网关地址和 GameBuddy 签发的用户凭证。

## 文本处理

Agent 返回文本在交给调用方之前进行最小化、确定性的清理：

- 去除首尾空白。
- 将连续空白和换行规整为适合单段播报的空格。
- 删除不适合 TTS 的 ASCII 控制字符。
- 限制最大字符数，超出部分按明确规则截断。
- 清理后为空则返回失败，不使用原始文本。

本层不改变事实内容、不补写推荐理由，也不尝试解析 Markdown 结构。具体 prompt、语气、
长度要求和业务事实约束由后续场景设计负责。

## 失败处理

Provider 不向 UI 或语音线程抛出网络和协议异常，而是返回稳定的 `error_code`。至少区分：

- `not_configured`
- `invalid_request`
- `timeout`
- `network_error`
- `authentication_error`
- `http_error`
- `invalid_response`
- `agent_error`
- `empty_result`

本阶段没有业务调用，因此失败只影响手动诊断。后续场景接入时必须定义本地固定文案回退，
并在 Agent 结果过期后丢弃结果，不能延迟或覆盖更新的游戏状态。

## 日志与安全

日志允许记录：

- Provider 名称。
- `query_id`。
- 请求耗时。
- HTTP 状态码。
- 太极 `retcode`。
- 客户端 `error_code`。

日志禁止记录：

- Authorization Header 或 Token。
- 完整请求 Header。
- 默认情况下的完整 prompt、context 和 Agent 返回正文。

HTTP 异常正文可能回显 Authorization 信息，因此不能直接把服务端原始错误正文写入日志。
诊断命令也只输出脱敏后的结构化状态和成功文本，不输出配置对象或请求 Header。

本地 Token 方案仅用于受控测试，不作为正式发布安全方案。若 Token 泄漏，应立即在太极侧撤销，
不能只依赖删除本地文件或 Git 提交。

## 手动诊断入口

提供单独的命令行诊断入口，读取与 EXE 相同的本机配置，发送一条显式传入的测试 prompt。
该入口用于确认：

1. Windows 测试机能够解析并访问配置的 Endpoint。
2. Token 和 `forward_service` 有效。
3. 太极返回符合约定的 JSON。
4. 客户端能生成 `query_id`、计算耗时并清理结果文本。

诊断失败时返回非零退出码，并显示稳定的错误分类。它不启动 Overlay、不播放语音，也不修改
配置。自动化测试不得调用真实太极服务。

## 线程与生命周期

Provider 自身不创建常驻线程，也不在应用启动时发起请求。配置解析和 Provider 构造不产生
网络副作用。`generate()` 的每次调用独立完成一次请求，不保留 Agent 会话。

未来接入业务场景时，应用层负责：

- 在后台线程发起生成。
- 为请求绑定业务状态版本或事件标识。
- 在结果返回前判断请求是否已经过期。
- 失败或过期时使用本地固定文案。
- 在退出时停止等待新的结果，不阻塞现有关闭流程。

## 测试策略

自动化测试使用可控的本地假 Transport，不访问网络，至少覆盖：

- 正确构造 URL、Header 和非流式请求体。
- 成功响应映射为结构化结果。
- 超时、网络错误、401/403 和其他 HTTP 错误。
- 非 JSON、错误 `retcode`、缺少或为空的 `result`。
- 文本空白、控制字符和最大长度清理。
- 配置缺失、类型错误及超时范围校验。
- Token 不出现在日志、异常和对象字符串表示中。
- 现有 Overlay、语音策略和离线 TTS 测试保持不变。

实现阶段完成后需要运行聚焦测试、全部单元测试、`compileall` 和 `git diff --check`，并检查
新增或修改代码单行不超过 120 个字符、单个方法不超过 80 行。

Windows 手动诊断只能证明太极直连接口可用，不能描述为正式网关验证，也不能替代后续真实
业务场景中的语音时延验收。

## 验收标准

1. 没有 Agent 配置时，GameBuddy 的行为与当前版本一致。
2. 使用假 Transport 能验证完整请求和响应处理，不需要真实 Token。
3. 在受控 Windows 办公网测试机配置有效 Token 后，诊断入口能返回非空文案。
4. 任一失败路径均给出稳定错误分类，且不泄漏 Token。
5. 当前没有任何 OCR、推荐、气泡或语音状态触发 Agent 请求。
6. 未来网关 Provider 可以复用相同请求和结果抽象，不要求修改业务调用接口。

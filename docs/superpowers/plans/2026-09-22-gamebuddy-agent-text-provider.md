# GameBuddy Agent Text Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an inactive-by-default, directly testable Taiji Agent text provider without connecting it to any GameBuddy speech scenario.

**Architecture:** Parse local-only Agent settings from the existing overlay configuration, then expose a small provider interface whose first implementation calls Taiji AppCreate through an injectable standard-library HTTP transport. A separate diagnostic command exercises the provider, while the Overlay, speech policy, and TTS code remain untouched.

**Tech Stack:** Python 3.11, `dataclasses`, `urllib.request`, `json`, `unittest`

**Spec:** `docs/superpowers/specs/2026-09-22-gamebuddy-agent-text-provider-design.md`

## Global Constraints

- Do not connect Agent output to OCR, recommendations, bubbles, `SpeechService`, or any current speech state.
- Do not commit a real Taiji Token or print it through logs, exceptions, diagnostics, or object representations.
- Use only the Python standard library; do not add runtime packages or online installation steps.
- Keep each new or modified method at 80 lines or fewer.
- Keep every new or modified code line at 120 characters or fewer.
- Do not refactor, rename, reformat, or comment code outside this feature's exact scope.
- Automated tests must use injected fake transports and must never call Taiji.
- Preserve existing `voice_enabled`, `league_root`, Overlay startup, and shutdown behavior.

## Review Focus

- A JSON boolean used as `timeout_seconds` must be rejected instead of being accepted as Python integer `True` or `False`.
- Endpoints with whitespace, unsupported schemes, or no host must leave the Provider unconfigured without raising.
- HTTP errors whose server body echoes an Authorization value must not expose that body or Token in logs or results.
- A successful JSON object with a missing, non-string, or whitespace-only `result` must return a stable failure.
- A `context` containing values that JSON cannot encode must return `invalid_request` without invoking the transport.

---

## File Map

- Create `scripts/product/agent_config.py`: immutable Agent settings and strict parsing of the nested config object.
- Modify `scripts/recognition_overlay/overlay_config.py`: load Agent settings from the existing primary/legacy files.
- Modify `tests/recognition_overlay/test_overlay_config.py`: pin precedence, validation, redaction, and compatibility.
- Create `scripts/product/agent_text.py`: Provider protocol, results, HTTP transport, Taiji request/response handling.
- Create `tests/product/test_agent_text.py`: isolated request, response, failure, sanitization, and logging tests.
- Create `scripts/recognition_overlay/agent_probe.py`: explicit manual connectivity command with safe output and exit codes.
- Create `tests/recognition_overlay/test_agent_probe.py`: diagnostic success and failure tests without network access.
- Modify `README.md`: document local test configuration and the manual probe command.

### Task 1: Parse local Agent configuration safely

**Files:**
- Create: `scripts/product/agent_config.py`
- Modify: `scripts/recognition_overlay/overlay_config.py`
- Modify: `tests/recognition_overlay/test_overlay_config.py`

**Interfaces:**
- Consumes: nested `agent` object read from primary and legacy `overlay.json` files.
- Produces: `AgentSettings`, `parse_agent_settings(value)`, and
  `load_agent_settings(primary, legacy) -> AgentSettings`.

- [ ] **Step 1: Write failing Agent configuration tests**

Add imports for `AgentSettings` and `load_agent_settings`, then add tests with these exact assertions:

```python
def test_agent_defaults_to_unconfigured(self) -> None:
    settings = load_agent_settings(self.primary, self.legacy)
    self.assertEqual(AgentSettings(), settings)
    self.assertFalse(settings.configured)

def test_agent_reads_valid_primary_config_and_redacts_token(self) -> None:
    self.write_json(self.primary, {
        "agent": {
            "provider": "taiji_direct",
            "endpoint": "http://agent.example/openapi/app_platform/app_create",
            "forward_service": "hyaide-application-22835",
            "token": "secret-token",
            "timeout_seconds": 12,
        },
    })

    settings = load_agent_settings(self.primary, self.legacy)

    self.assertTrue(settings.configured)
    self.assertEqual("secret-token", settings.token)
    self.assertEqual(12.0, settings.timeout_seconds)
    self.assertNotIn("secret-token", repr(settings))

def test_invalid_primary_agent_does_not_use_legacy_secret(self) -> None:
    self.write_json(self.primary, {"agent": {"provider": "taiji_direct"}})
    self.write_json(self.legacy, {
        "agent": {
            "provider": "taiji_direct",
            "endpoint": "http://legacy.example/app_create",
            "forward_service": "hyaide-application-1",
            "token": "legacy-secret",
        },
    })

    settings = load_agent_settings(self.primary, self.legacy)

    self.assertFalse(settings.configured)
    self.assertEqual("", settings.token)
```

Add table-driven subtests proving that `timeout_seconds` values `True`, `False`, `0`, `31`, strings,
and objects produce an unconfigured result. Add endpoint subtests for leading/trailing whitespace,
`ftp://host/path`, and `http:///missing-host`. Preserve the existing voice tests unchanged.

- [ ] **Step 2: Run the focused tests and verify the new imports fail**

Run:

```bash
PYTHONPATH=scripts/product:scripts/recognition_overlay \
python3 -m unittest tests.recognition_overlay.test_overlay_config -v
```

Expected: failure because `AgentSettings` and `load_agent_settings` do not exist.

- [ ] **Step 3: Implement immutable settings and strict parsing**

Create `scripts/product/agent_config.py` with these public definitions:

```python
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlsplit

DEFAULT_AGENT_TIMEOUT_SECONDS = 10.0
MIN_AGENT_TIMEOUT_SECONDS = 1.0
MAX_AGENT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class AgentSettings:
    provider: str = ""
    endpoint: str = ""
    forward_service: str = ""
    token: str = field(default="", repr=False)
    timeout_seconds: float = DEFAULT_AGENT_TIMEOUT_SECONDS

    @property
    def configured(self) -> bool:
        return (
            self.provider == "taiji_direct"
            and _valid_endpoint(self.endpoint)
            and bool(self.forward_service)
            and bool(self.token)
        )


def parse_agent_settings(value: object) -> AgentSettings:
    if not isinstance(value, Mapping):
        return AgentSettings()
    # Read only exact non-empty strings. Reject bool before accepting int/float.
    # Return AgentSettings() when any supplied field or timeout is invalid.
```

Implement `_valid_endpoint()` with `urlsplit()`: accept only exact `http` or `https` schemes, require
`netloc`, and reject strings that differ from `value.strip()`. Accept timeout values from `1.0` through
`30.0`, inclusive. Do not normalize or preserve a partially valid secret when the object is invalid.

Modify `overlay_config.py` without changing `load_voice_settings()`:

```python
from agent_config import AgentSettings, parse_agent_settings


def load_agent_settings(primary: Path, legacy: Path) -> AgentSettings:
    value = read_config_value("agent", (primary, legacy))
    return parse_agent_settings(value)
```

- [ ] **Step 4: Run configuration tests and line-length checks**

Run:

```bash
PYTHONPATH=scripts/product:scripts/recognition_overlay \
python3 -m unittest tests.recognition_overlay.test_overlay_config -v
awk 'length($0) > 120 { print FNR ":" length($0) ":" $0 }' \
scripts/product/agent_config.py scripts/recognition_overlay/overlay_config.py \
tests/recognition_overlay/test_overlay_config.py
```

Expected: all tests pass and `awk` prints nothing.

- [ ] **Step 5: Commit the configuration slice**

```bash
git add scripts/product/agent_config.py scripts/recognition_overlay/overlay_config.py \
  tests/recognition_overlay/test_overlay_config.py
git diff --cached --check
git commit -m "feat: add local agent configuration"
```

### Task 2: Implement the Taiji text Provider

**Files:**
- Create: `scripts/product/agent_text.py`
- Create: `tests/product/test_agent_text.py`

**Interfaces:**
- Consumes: `AgentSettings` from Task 1 and an optional injected `AgentHttpTransport`.
- Produces: `AgentResult`, `AgentTextProvider`, `TaijiDirectAgentProvider`, and
  `build_agent_provider(settings, transport=None)`.

- [ ] **Step 1: Write failing request construction and success tests**

Create a fake transport that records `endpoint`, `headers`, JSON bytes, and timeout, then returns an
`AgentHttpResponse`. Pin the public contract with tests equivalent to:

```python
def test_generate_builds_non_streaming_taiji_request(self) -> None:
    transport = FakeTransport(AgentHttpResponse(
        status_code=200,
        body=b'{"message":"","result":"  hello\\nworld  ","retcode":0}',
    ))
    provider = TaijiDirectAgentProvider(settings(), transport=transport)

    result = provider.generate("say hello", {"champion": "Ahri"})

    self.assertTrue(result.ok)
    self.assertEqual("hello world", result.text)
    request = transport.calls[0]
    self.assertEqual("Bearer secret-token", request.headers["Authorization"])
    self.assertEqual("application/json; charset=utf-8", request.headers["Content-Type"])
    payload = json.loads(request.body.decode("utf-8"))
    self.assertFalse(payload["stream"])
    self.assertEqual("hyaide-application-22835", payload["forward_service"])
    self.assertEqual("say hello", payload["query"])
    self.assertEqual("say hello", payload["messages"][0]["content"])
    self.assertEqual('{"champion":"Ahri"}', payload["context"])
    self.assertEqual(result.query_id, payload["query_id"])
```

Also assert that generated query IDs are non-empty and different across two calls.

- [ ] **Step 2: Write failing validation, response, and secrecy tests**

Add separate tests for:

- empty or whitespace-only prompt returns `invalid_request` without a transport call;
- non-JSON-serializable context returns `invalid_request` without a transport call;
- HTTP 401 and 403 return `authentication_error`;
- other non-2xx statuses return `http_error`;
- a transport timeout returns `timeout` and another transport failure returns `network_error`;
- invalid UTF-8, malformed JSON, and a JSON list return `invalid_response`;
- nonzero `retcode` returns `agent_error`;
- missing, non-string, or whitespace-only `result` returns `empty_result`;
- control characters and repeated whitespace collapse to one space;
- results longer than `MAX_AGENT_TEXT_CHARACTERS = 240` are truncated to exactly 240 characters;
- an HTTP response body containing `secret-token` never appears in `assertLogs()` output;
- `repr(provider)` and `repr(settings())` do not contain `secret-token`;
- `build_agent_provider(AgentSettings())` returns a Provider whose `generate()` result is
  `not_configured` and does not access the network.

- [ ] **Step 3: Run the Provider tests and verify they fail**

Run:

```bash
PYTHONPATH=scripts/product \
python3 -m unittest tests.product.test_agent_text -v
```

Expected: failure because `agent_text.py` does not exist.

- [ ] **Step 4: Implement the Provider contracts and safe transport**

Create these immutable public value types in `agent_text.py`:

```python
MAX_AGENT_TEXT_CHARACTERS = 240


@dataclass(frozen=True)
class AgentResult:
    ok: bool
    text: str
    query_id: str
    error_code: str | None


@dataclass(frozen=True)
class AgentHttpRequest:
    endpoint: str
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    timeout_seconds: float


@dataclass(frozen=True)
class AgentHttpResponse:
    status_code: int
    body: bytes = field(repr=False)


class AgentTransportFailure(Exception):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code
```

Define `AgentTextProvider` and `AgentHttpTransport` as `Protocol` classes. The default urllib transport
must create a `urllib.request.Request` with `method="POST"`. Convert `socket.timeout`, `TimeoutError`,
and timeout-shaped `urllib.error.URLError` values to `AgentTransportFailure("timeout")`; convert other
`URLError` and `OSError` values to `network_error`. For `HTTPError`, return only its status code and an
empty body so an error response cannot leak echoed credentials.

Implement `TaijiDirectAgentProvider.generate()` as small helpers for request creation, transport error
mapping, response parsing, and text cleaning. Use `uuid.uuid4().hex` for `query_id`. Serialize context
with compact UTF-8 JSON and pass it as the AppCreate `context` string only when context is present.

Use a module logger with metadata-only messages:

```python
LOGGER.info(
    "agent request finished provider=taiji_direct query_id=%s status=%s elapsed_ms=%d",
    query_id,
    response.status_code,
    elapsed_ms,
)
```

Do not pass exceptions, headers, request bodies, response bodies, prompt text, context, or generated text
as logging arguments. Set the Provider's Token attribute so object representation cannot reveal it; a
custom `__repr__` may identify only the Provider type and endpoint.

- [ ] **Step 5: Run Provider tests and source constraints**

Run:

```bash
PYTHONPATH=scripts/product \
python3 -m unittest tests.product.test_agent_text -v
awk 'length($0) > 120 { print FNR ":" length($0) ":" $0 }' \
scripts/product/agent_text.py tests/product/test_agent_text.py
```

Expected: all tests pass and `awk` prints nothing. Manually inspect each new method to confirm it is at
most 80 lines.

- [ ] **Step 6: Commit the Provider slice**

```bash
git add scripts/product/agent_text.py tests/product/test_agent_text.py
git diff --cached --check
git commit -m "feat: add Taiji agent text provider"
```

### Task 3: Add a safe manual connectivity probe and user documentation

**Files:**
- Create: `scripts/recognition_overlay/agent_probe.py`
- Create: `tests/recognition_overlay/test_agent_probe.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `load_agent_settings()`, `overlay_config_path()`, `legacy_overlay_config_path()`, and
  `build_agent_provider()` from Tasks 1 and 2.
- Produces: `agent_probe.main(argv=None) -> int` and a documented local test workflow.

- [ ] **Step 1: Write failing diagnostic command tests**

Test `main()` by injecting or patching the settings loader and Provider factory. Capture stdout and assert:

```python
def test_probe_prints_safe_success_json(self) -> None:
    provider = Mock()
    provider.generate.return_value = AgentResult(
        ok=True,
        text="连接成功",
        query_id="query-1",
        error_code=None,
    )

    exit_code, payload = run_probe(provider, ["--prompt", "只回复连接成功"])

    self.assertEqual(0, exit_code)
    self.assertEqual({
        "ok": True,
        "text": "连接成功",
        "query_id": "query-1",
        "error_code": None,
    }, payload)
```

Add a failure case returning exit code `1` with no text, an unconfigured case returning
`not_configured`, and a secrecy case proving a configured Token is absent from stdout and stderr.
Assert that the Provider receives the exact prompt and no implicit business context.

- [ ] **Step 2: Run diagnostic tests and verify the module is missing**

Run:

```bash
PYTHONPATH=scripts/product:scripts/recognition_overlay \
python3 -m unittest tests.recognition_overlay.test_agent_probe -v
```

Expected: failure because `agent_probe.py` does not exist.

- [ ] **Step 3: Implement the diagnostic entry point**

Use `argparse` with one required `--prompt` option. Load the same primary and legacy config paths as the
Overlay, build the Provider, call `generate(prompt)`, and print exactly one UTF-8 JSON object using
`ensure_ascii=False`. Return `0` only for a successful result and `1` otherwise.

Keep construction separately testable:

```python
def run_probe(prompt: str, provider: AgentTextProvider) -> AgentResult:
    return provider.generate(prompt)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_agent_settings(overlay_config_path(), legacy_overlay_config_path())
    result = run_probe(args.prompt, build_agent_provider(settings))
    print(json.dumps(asdict(result), ensure_ascii=False))
    return 0 if result.ok else 1
```

Do not add this command to normal Overlay startup and do not instantiate a Provider in `app.py`.

- [ ] **Step 4: Document the local-only test configuration and probe**

Add a focused `Agent 文案能力测试` section to `README.md`. It must state that the feature is not yet
connected to any speech scenario, that a real Token belongs only in
`%LOCALAPPDATA%\LoLRecognitionOverlay\overlay.json`, and that the repository sample must stay secret-free.

Document this Windows command from the repository root:

```bat
set PYTHONPATH=scripts\product;scripts\recognition_overlay
py -3.11 -B scripts\recognition_overlay\agent_probe.py --prompt "请只回复：连接成功"
```

Document success as JSON containing `"ok": true` and failure as a nonzero exit code with a stable
`error_code`. Explicitly say this probe does not play speech and does not prove future gateway behavior.

- [ ] **Step 5: Run focused tests and inspect scope**

Run:

```bash
PYTHONPATH=scripts/product:scripts/recognition_overlay \
python3 -m unittest tests.recognition_overlay.test_agent_probe \
  tests.recognition_overlay.test_overlay_config tests.product.test_agent_text -v
git diff --check
git diff -- scripts/recognition_overlay/app.py scripts/product/speech.py \
  scripts/product/speech_policy.py
```

Expected: focused tests pass, `git diff --check` passes, and the final `git diff` prints nothing.

- [ ] **Step 6: Commit the diagnostic slice**

```bash
git add scripts/recognition_overlay/agent_probe.py \
  tests/recognition_overlay/test_agent_probe.py README.md
git diff --cached --check
git commit -m "feat: add Agent connectivity probe"
```

### Task 4: Complete regression and release-quality verification

**Files:**
- Verify only; do not modify files solely to satisfy this task.

**Interfaces:**
- Consumes: all deliverables from Tasks 1 through 3.
- Produces: auditable test and repository-state evidence.

- [ ] **Step 1: Run the complete unit test suite**

```bash
PYTHONPATH=scripts/product:scripts/recognition_overlay \
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: all tests pass. Report the actual count rather than assuming the previous count of 182.

- [ ] **Step 2: Run compile and whitespace validation**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m compileall -q scripts tests
git diff --check
```

Expected: both commands exit `0` with no output.

- [ ] **Step 3: Check method and line limits for every changed Python file**

Run the 120-character check:

```bash
git diff bcd1c226b23a8c04f4a881f4ddaafdf4d72e849d --name-only -- '*.py' | \
while IFS= read -r file; do
  awk 'length($0) > 120 { print FILENAME ":" FNR ":" length($0) }' "$file"
done
```

Expected: no output. Inspect the AST or source ranges for every changed function and method; none may
exceed 80 lines.

- [ ] **Step 4: Audit secret absence and change scope**

```bash
git diff bcd1c226b23a8c04f4a881f4ddaafdf4d72e849d --stat
git diff bcd1c226b23a8c04f4a881f4ddaafdf4d72e849d -- \
  scripts/recognition_overlay/app.py scripts/product/speech.py \
  scripts/product/speech_policy.py
git grep -n "secret-token" -- ':!docs/superpowers/plans/*'
git status --short
```

Expected: only the planned files changed, protected speech files have no diff, the synthetic test Token
does not appear outside tests, and the worktree is clean after commits.

- [ ] **Step 5: Run the optional Windows live probe only when a user supplies a local Token**

Place the Token only in the test machine's `%LOCALAPPDATA%` configuration and run the documented command.
Do not print or copy the Token into chat, shell history, commits, or test output. Record only `ok`,
`query_id`, elapsed time, and error classification. If no Token is available, report the live probe as
not run rather than treating mocked tests as a live Taiji success.

- [ ] **Step 6: Review commits and defer pushing until the implementation is accepted**

```bash
git log --oneline bcd1c226b23a8c04f4a881f4ddaafdf4d72e849d..HEAD
git status --short --branch
```

Expected: the planned implementation commits are present and the worktree is clean. Push WOA and GitHub
only after implementation review, then verify local HEAD, `origin/feature/cat-ui-recommendation`, and
`github/main` resolve to the same commit.

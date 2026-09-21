# GameBuddy Voice Companion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent, priority-aware Windows speech for bubble summaries and future companion messages,
with a saved right-click toggle and failure isolation from the existing recognition flow.

**Architecture:** Convert local UI changes and future remote pushes into one immutable `SpeechMessage` model.
A pure queue owns deduplication, expiry, throttling, and priority; a service thread drives a pluggable adapter.
The first adapter keeps one PowerShell `System.Speech` worker alive and fails closed without affecting the Overlay.

**Tech Stack:** Python 3.11 standard library, PowerShell 5+, `.NET System.Speech`, JSON Lines, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-21-gamebuddy-windows-overlay-voice-design.md`

## Global Constraints

- Voice is enabled by default and the user's toggle persists in `%LOCALAPPDATA%\LoLRecognitionOverlay\overlay.json`.
- Prefer the configured voice, then an installed Chinese female voice, then the Windows default voice.
- Speak conversational summaries, never a verbatim long bubble, button label, page marker, or diagnostic detail.
- Only OCR work lasting more than 2 seconds produces one progress message.
- Low-priority companion messages have a minimum 60-second interval.
- High-priority recommendations and critical errors may interrupt low-priority speech.
- Muting cancels current playback, pauses dispatch, clears low-priority queued messages, and rejects new messages.
- First release implements local messages and only defines the remote adapter interface.
- TTS failure must not block startup, UI, OCR, recommendations, dragging, or shutdown.
- Every changed method is at most 80 lines and every changed line is at most 120 characters.
- Make no unrelated change or refactor.

## File Structure

- Create `scripts/recognition_overlay/overlay_config.py`: preserving config reads and atomic field updates.
- Modify `scripts/recognition_overlay/league_root.py`: preserve voice fields while saving the League path.
- Create `scripts/product/speech.py`: message model, priorities, pure queue, and adapter protocol.
- Create `scripts/product/speech_policy.py`: view-to-summary transitions and delayed OCR progress.
- Create `scripts/product/windows_speech.py`: persistent PowerShell adapter.
- Create `scripts/product/windows_speech_worker.ps1`: `System.Speech` JSON Lines worker.
- Modify `scripts/recognition_overlay/app.py`: lifecycle and local message publication.
- Modify `scripts/product/cat_overlay.py`: dynamic voice menu item and callback.
- Add focused tests under `tests/product/` and `tests/recognition_overlay/`.
- Modify `README.md` and `docs/windows-gamebuddy-acceptance.md`.

## Review Focus

1. Saving `league_root` after a voice toggle must preserve `voice_enabled` and `voice_name`.
2. A corrupt config, missing PowerShell, or missing `System.Speech` must leave the application usable.
3. Rapid repeated snapshots must not repeatedly speak the same recommendation or OCR progress.
4. Muting during playback must cancel promptly and prevent stale low-priority speech after re-enabling.
5. Application shutdown while the worker is speaking must terminate the worker without hanging or leaving a process.

---

### Task 1: Preserving Overlay configuration

**Files:**
- Create: `scripts/recognition_overlay/overlay_config.py`
- Modify: `scripts/recognition_overlay/league_root.py`
- Create: `tests/recognition_overlay/__init__.py`
- Create: `tests/recognition_overlay/test_overlay_config.py`
- Create: `tests/recognition_overlay/test_league_root.py`

**Interfaces:**
- Produces: `VoiceSettings(enabled: bool = True, voice_name: str | None = None)`.
- Produces: `read_config_value(key: str, paths: Sequence[Path]) -> object | None`.
- Produces: `load_voice_settings(primary: Path, legacy: Path) -> VoiceSettings`.
- Produces: `update_overlay_config(path: Path, changes: Mapping[str, object]) -> bool`.
- Changes: `persist_league_root` uses `update_overlay_config` instead of replacing the whole JSON object.

- [ ] **Step 1: Write failing config-preservation tests**

```python
def test_voice_defaults_to_enabled(self):
    settings = load_voice_settings(self.primary, self.legacy)
    self.assertEqual(VoiceSettings(enabled=True, voice_name=None), settings)

def test_update_preserves_unrelated_fields(self):
    self.primary.write_text(
        json.dumps({"league_root": "E:\\\\League", "voice_name": "Xiaoxiao"}),
        "utf-8",
    )
    self.assertTrue(update_overlay_config(self.primary, {"voice_enabled": False}))
    saved = json.loads(self.primary.read_text("utf-8"))
    self.assertEqual("E:\\League", saved["league_root"])
    self.assertEqual("Xiaoxiao", saved["voice_name"])
    self.assertFalse(saved["voice_enabled"])
```

- [ ] **Step 2: Run the focused test and verify the module is absent**

```bash
PYTHONPATH=scripts/recognition_overlay python3 -m unittest \
  tests.recognition_overlay.test_overlay_config -v
```

Expected: FAIL because `overlay_config` does not exist.

- [ ] **Step 3: Implement validated reads and atomic preserving writes**

```python
@dataclass(frozen=True)
class VoiceSettings:
    enabled: bool = True
    voice_name: str | None = None


def update_overlay_config(path: Path, changes: Mapping[str, object]) -> bool:
    current = _read_object(path)
    current.update(changes)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", "utf-8")
        temporary.replace(path)
        return True
    except OSError:
        temporary.unlink(missing_ok=True)
        LOGGER.exception("overlay config update failed path=%s", path)
        return False
```

`read_config_value` checks the primary file and then the legacy file for the requested key. Validate
`voice_enabled` as a Boolean and `voice_name` as a non-empty string; invalid values use the defaults.
Add a corrupt-primary test and assert a valid legacy value is still read.

- [ ] **Step 4: Protect the existing League-root flow**

Change `_read_config_root` to request only `league_root`. Change `persist_league_root` to call:

```python
update_overlay_config(overlay_config_path(), {"league_root": str(root)})
```

Test that saving a discovered League root leaves both voice fields unchanged.

- [ ] **Step 5: Run tests and commit**

```bash
PYTHONPATH=scripts/recognition_overlay python3 -m unittest \
  tests.recognition_overlay.test_overlay_config \
  tests.recognition_overlay.test_league_root -v
git add scripts/recognition_overlay/overlay_config.py scripts/recognition_overlay/league_root.py \
  tests/recognition_overlay
git commit -m "feat: preserve GameBuddy voice settings"
```

### Task 2: Speech message model and deterministic queue

**Files:**
- Create: `scripts/product/speech.py`
- Create: `tests/product/test_speech.py`

**Interfaces:**
- Produces: `SpeechPriority(IntEnum)` with `LOW=10`, `NORMAL=20`, and `HIGH=30`.
- Produces: immutable `SpeechMessage` with the seven fields from the approved spec.
- Produces: `SpeechAdapter` protocol with `start`, `speak`, `wait_finished`, `cancel`, and `close`.
- Produces: `SpeechQueue.publish(message, now) -> bool`.
- Produces: `SpeechQueue.next(now) -> SpeechMessage | None`.
- Produces: `SpeechQueue.mute() -> None` and `SpeechQueue.unmute() -> None`.

- [ ] **Step 1: Write failing queue tests**

```python
def message(key, priority=SpeechPriority.NORMAL, expires_at=100.0):
    return SpeechMessage(
        message_id=key,
        source="local",
        kind="recommendation",
        summary=key,
        priority=priority,
        dedupe_key=key,
        created_at=0.0,
        expires_at=expires_at,
    )


def test_duplicate_and_expired_messages_are_rejected(self):
    queue = SpeechQueue()
    self.assertTrue(queue.publish(message("same"), 1.0))
    self.assertFalse(queue.publish(message("same"), 2.0))
    self.assertFalse(queue.publish(message("expired", expires_at=1.0), 2.0))


def test_high_priority_precedes_low_priority(self):
    queue = SpeechQueue()
    queue.publish(message("low", SpeechPriority.LOW), 1.0)
    queue.publish(message("high", SpeechPriority.HIGH), 1.0)
    self.assertEqual("high", queue.next(1.0).dedupe_key)
```

- [ ] **Step 2: Run the test and verify the new module is absent**

```bash
PYTHONPATH=scripts/product python3 -m unittest tests.product.test_speech -v
```

Expected: FAIL because `speech` does not exist.

- [ ] **Step 3: Implement immutable messages and the heap-backed queue**

```python
class SpeechPriority(IntEnum):
    LOW = 10
    NORMAL = 20
    HIGH = 30


@dataclass(frozen=True)
class SpeechMessage:
    message_id: str
    source: str
    kind: str
    summary: str
    priority: SpeechPriority
    dedupe_key: str
    created_at: float
    expires_at: float
```

Use `(-priority, sequence, message)` heap entries so equal-priority order is stable. Keep dedupe keys through their
message expiry. `next` discards expired entries. Track the last low-priority dispatch and delay the next low message
until 60 seconds have elapsed.

- [ ] **Step 4: Add mute, throttle, and interruption tests**

Test that `mute` clears only low-priority entries and rejects all new entries while muted. Test that ordinary and high
entries already queued expire normally. Add `should_interrupt(current, incoming)` and assert that only an incoming high
message interrupts a currently playing low message.

- [ ] **Step 5: Run tests and commit**

```bash
PYTHONPATH=scripts/product python3 -m unittest tests.product.test_speech -v
git add scripts/product/speech.py tests/product/test_speech.py
git commit -m "feat: add GameBuddy speech message queue"
```

### Task 3: Persistent Windows speech adapter

**Files:**
- Create: `scripts/product/windows_speech.py`
- Create: `scripts/product/windows_speech_worker.ps1`
- Create: `tests/product/test_windows_speech.py`

**Interfaces:**
- Produces: `WindowsSpeechAdapter(command=None, voice_name=None, process_factory=subprocess.Popen)`.
- Implements: `start() -> bool`, `speak(text: str) -> bool`, and `wait_finished(timeout) -> bool`.
- Implements: `cancel() -> None` and `close() -> None`.
- Worker input: one JSON object per line with `command` equal to `speak`, `cancel`, or `close`.
- Worker output: one JSON object per line with `event` equal to `ready`, `started`, `finished`, or `error`.

- [ ] **Step 1: Write failing adapter lifecycle tests with a fake process**

```python
def test_start_reuses_one_process_for_multiple_messages(self):
    factory = Mock(return_value=FakeProcess([{"event": "ready"}]))
    adapter = WindowsSpeechAdapter(process_factory=factory)
    self.assertTrue(adapter.start())
    self.assertTrue(adapter.speak("第一条"))
    self.assertTrue(adapter.speak("第二条"))
    factory.assert_called_once()


def test_close_sends_close_and_terminates_on_timeout(self):
    process = FakeProcess([{"event": "ready"}], wait_timeout=True)
    adapter = WindowsSpeechAdapter(process_factory=Mock(return_value=process))
    adapter.start()
    adapter.close()
    self.assertIn({"command": "close"}, process.writes)
    self.assertTrue(process.terminated)
```

- [ ] **Step 2: Run the adapter tests and verify failure**

```bash
PYTHONPATH=scripts/product python3 -m unittest tests.product.test_windows_speech -v
```

Expected: FAIL because `windows_speech` does not exist.

- [ ] **Step 3: Implement the JSON Lines adapter**

Start exactly one hidden `powershell.exe` process with `-NoProfile`, `-NonInteractive`, and `-File` pointing to the
bundled worker. Encode stdin and stdout as UTF-8. Wait up to 3 seconds for `ready`; on timeout or malformed output,
close pipes, terminate the process, log once, and return `False`.

Serialize commands with `ensure_ascii=False` and guard writes with a lock.
Run one stdout-reader thread that validates events and sets a completion event for `finished` or `error`.
`wait_finished` waits on that event. `close` waits up to 2 seconds after the close command,
then terminates and waits once more. Public methods catch `OSError`, `BrokenPipeError`, and invalid state.

- [ ] **Step 4: Implement the PowerShell worker**

The worker loads `System.Speech`, creates one `SpeechSynthesizer`, selects the requested voice when present,
otherwise selects the first installed `zh-CN` female voice, and otherwise keeps the system default.

For each line, parse JSON and execute:

```powershell
switch ($message.command) {
  "speak"  { $null = $synth.SpeakAsync([string]$message.text) }
  "cancel" { $synth.SpeakAsyncCancelAll() }
  "close"  { $synth.SpeakAsyncCancelAll(); $synth.Dispose(); exit 0 }
}
```

Register `SpeakStarted` and `SpeakCompleted` handlers to emit JSON events and flush stdout after every event.
Never print diagnostics to stdout; write worker errors as `{ "event": "error", "message": "..." }`.

- [ ] **Step 5: Test missing executables, broken pipes, invalid events, and idempotent close**

Each case must return failure or no-op without raising into the caller.
Verify the fake process is terminated exactly once.
On Windows, query installed voices through the worker and assert the selection order is configured name,
then Chinese female, then system default.

- [ ] **Step 6: Run tests and commit**

```bash
PYTHONPATH=scripts/product python3 -m unittest tests.product.test_windows_speech -v
git add scripts/product/windows_speech.py scripts/product/windows_speech_worker.ps1 \
  tests/product/test_windows_speech.py
git commit -m "feat: add persistent Windows speech adapter"
```

### Task 4: Speech policy, service lifecycle, and right-click toggle

**Files:**
- Create: `scripts/product/speech_policy.py`
- Create: `tests/product/test_speech_policy.py`
- Modify: `scripts/product/speech.py`
- Modify: `tests/product/test_speech.py`
- Modify: `scripts/product/cat_overlay.py`
- Modify: `tests/product/test_cat_overlay.py`
- Modify: `scripts/recognition_overlay/app.py`
- Create: `tests/recognition_overlay/test_app_speech.py`

**Interfaces:**
- Produces: `CompanionSpeechPolicy.update(view, now) -> tuple[SpeechMessage, ...]`.
- Produces: `CompanionSpeechPolicy.tick(now) -> tuple[SpeechMessage, ...]` for delayed OCR progress.
- Produces: `SpeechService.publish(message)`, `set_enabled(enabled)`, and `close()`.
- Adds: `CatOverlayWindow(on_voice_toggle, voice_enabled)` and `set_voice_enabled(enabled)`.
- Reserves: `CompanionMessageSource.start(publish)` and `close()` protocol; no remote implementation.

- [ ] **Step 1: Write failing policy tests**

```python
def test_recommendation_uses_a_short_spoken_summary_once(self):
    policy = CompanionSpeechPolicy()
    view = {
        "state": "recommendation",
        "message_blocks": [
            {"label": "当前推荐", "value": "巨人杀手（CENTER）"},
            {"label": "当前玩法", "value": "胜率优先"},
        ],
    }
    first = policy.update(view, 1.0)
    second = policy.update(view, 1.1)
    self.assertEqual("推荐选择巨人杀手，当前玩法胜率优先。", first[0].summary)
    self.assertEqual((), second)


def test_ocr_progress_waits_two_seconds_and_fires_once(self):
    policy = CompanionSpeechPolicy()
    policy.update({"state": "ocr_reading"}, 10.0)
    self.assertEqual((), policy.tick(11.99))
    self.assertEqual("正在识别海克斯，请稍候。", policy.tick(12.0)[0].summary)
    self.assertEqual((), policy.tick(13.0))
```

- [ ] **Step 2: Implement state-to-summary policy**

Recommendation messages are high priority and use the `当前推荐` and `当前玩法` values without position tokens.
OCR errors and unavailable recommendations are high priority with a fixed conversational summary.
Visible non-error bubble transitions are normal priority and use a short state-specific sentence.
OCR progress is normal priority, scheduled only while an OCR state remains active for 2 seconds.

Use a key made from state plus semantically relevant values.
Identical UI polling snapshots must emit no duplicate messages.
Define `CompanionMessageSource` as a protocol only; do not open sockets or add endpoints.

- [ ] **Step 3: Implement a background speech service around the pure queue**

The service owns one daemon thread, one `Condition`, the queue, and the adapter. `publish` only enqueues and notifies.
The worker waits for the next eligible message, starts the adapter lazily, calls `speak`, and then `wait_finished`.
When a high message arrives during low playback, `publish` calls `cancel` before notifying the worker.
The cancelled wait must finish before the worker dispatches the high message.

`set_enabled(False)` calls queue mute and adapter cancel synchronously. `close` marks the service closed, notifies the
thread, joins for at most 2 seconds, and closes the adapter. Repeated `close` calls are no-ops.

- [ ] **Step 4: Write failing menu-toggle tests**

```python
def test_voice_menu_label_and_callback_follow_current_state(self):
    toggle = Mock()
    window = make_window(on_voice_toggle=toggle, voice_enabled=True)
    menu = window._create_context_menu(FakeTk)
    self.assertEqual(["关闭语音", "退出"], menu.labels)
    menu.commands[0]()
    toggle.assert_called_once_with(False)
    window.set_voice_enabled(False)
    self.assertEqual(["开启语音", "退出"], window._menu_labels())
```

- [ ] **Step 5: Add the dynamic voice item without changing exit behavior**

Rename the private menu builder to `_create_context_menu`. Rebuild or relabel the first command before each popup.
The voice item calls `on_voice_toggle(not self._voice_enabled)`.
The exit item still calls `_close` without confirmation.
Right-click outside the cat remains ignored, and Escape still dismisses the menu before closing the window.

- [ ] **Step 6: Integrate configuration, policy, and lifecycle in `RecognitionApp`**

Load `VoiceSettings` before creating `CatOverlayWindow`. Create `SpeechService(WindowsSpeechAdapter(...))`,
pass the initial enabled state and toggle callback to the window, and feed every published product view through
`CompanionSpeechPolicy.update`. Call `policy.tick` from the existing 250ms UI tick.

The toggle callback persists only `voice_enabled`, updates the service, and updates the menu label. In `stop`, close
speech before stopping the other workers. Guard the existing repeated `stop` path so all components still close once.

- [ ] **Step 7: Add integration tests for failure isolation and shutdown**

Use fake speech policy, service, and config paths.
Assert app construction succeeds when adapter `start` returns `False`.
Assert repeated `_publish` calls deduplicate, the 2-second OCR timer uses the injected clock, toggle persists the value,
and two calls to `stop` close speech only once.

- [ ] **Step 8: Run tests and commit**

```bash
PYTHONPATH=scripts/product:scripts/recognition_overlay python3 -m unittest \
  tests.product.test_speech \
  tests.product.test_speech_policy \
  tests.product.test_cat_overlay \
  tests.recognition_overlay.test_app_speech -v
git add scripts/product scripts/recognition_overlay/app.py tests/product tests/recognition_overlay
git commit -m "feat: connect GameBuddy voice companion"
```

### Task 5: Voice documentation, package checks, and Windows acceptance

**Files:**
- Modify: `README.md`
- Modify: `docs/windows-gamebuddy-acceptance.md`
- Create: a candidate ZIP outside the repository after automated checks pass.

**Interfaces:**
- Documents: voice defaults, right-click toggle, optional `voice_name`, fallback behavior, and diagnostics.

- [ ] **Step 1: Document voice behavior and configuration**

Add this supported configuration shape without replacing the existing League path:

```json
{
  "league_root": "E:\\WeGameApps\\英雄联盟（含经典模式）",
  "voice_enabled": true,
  "voice_name": "Microsoft Xiaoxiao Online (Natural) - Chinese (Mainland)"
}
```

Explain that `voice_name` is optional and missing TTS silently disables speech for that run.
State that the right-click choice persists.

- [ ] **Step 2: Add voice acceptance checks**

Add checks for recommendation summary, error summary, delayed OCR progress, and deduplication.
Also check high-over-low interruption, 60-second low-priority spacing, toggle persistence,
missing configured voice fallback, clean shutdown, and no orphan process.

- [ ] **Step 3: Build a slim candidate and verify it contains the worker**

Create a temporary staging directory and copy only the runnable package inputs:

```bash
candidate_stage=$(mktemp -d)
candidate_zip=/Users/adrianqiu/Documents/Codex/2026-09-21/bang/outputs/hex-helper-windows-voice-candidate.zip
mkdir -p "$candidate_stage/hex-helper/outputs/tmp/build/bin"
cp BUILD.txt README.md overlay.json 一键启动小猫.cmd 打开诊断日志.cmd "$candidate_stage/hex-helper/"
cp -R assets data scripts "$candidate_stage/hex-helper/"
cp outputs/tmp/build/bin/lol_augment_assistant.exe \
  "$candidate_stage/hex-helper/outputs/tmp/build/bin/"
(cd "$candidate_stage/hex-helper" && zip -qr "$candidate_zip" .)
unzip -l "$candidate_zip" | rg "scripts/product/windows_speech_worker.ps1"
```

Do not add the staging directory or ZIP to Git.

- [ ] **Step 4: Run all automated checks**

```bash
PYTHONPATH=scripts/product:scripts/recognition_overlay python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts
python3 -m json.tool overlay.json >/dev/null
git diff --check
```

Check every changed Python method is at most 80 lines and every changed line is at most 120 characters.

- [ ] **Step 5: Commit documentation and packaging metadata**

```bash
git add README.md docs/windows-gamebuddy-acceptance.md
git add scripts/product/windows_speech_worker.ps1
git commit -m "docs: add GameBuddy voice acceptance guide"
```

If the worker was already committed in Task 3, omit the second `git add` rather than creating a meaningless change.

- [ ] **Step 6: Perform Windows speech acceptance before release**

Run the full voice checklist on Windows with speech enabled, muted, and with an invalid configured voice name.
Confirm `一键启动小猫.cmd` still starts the application and that closing it leaves no PowerShell worker.
Until this passes, do not push a release candidate to either Git remote.

- [ ] **Step 7: Push the accepted commit to both remotes and verify equality**

After the user reports every Windows acceptance item as passed:

```bash
git push origin HEAD:feature/cat-ui-recommendation
git push github HEAD:main
local_head=$(git rev-parse HEAD)
test "$local_head" = "$(git ls-remote origin refs/heads/feature/cat-ui-recommendation | cut -f1)"
test "$local_head" = "$(git ls-remote github refs/heads/main | cut -f1)"
git status --short
```

Report the common commit ID, both remote branch names, candidate ZIP path, and whether the worktree is clean.

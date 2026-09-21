# GameBuddy Offline Speech and Death OCR Timing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Windows `System.Speech` with bundled offline Chinese TTS,
prevent startup OCR failure presentation, and gate death-triggered OCR by death level and confirmed selections.

**Architecture:** Keep the existing speech policy, queue, and service, but replace the PowerShell adapter with a
persistent Python worker using sherpa-onnx, MeloTTS Chinese, and sounddevice. Add a pure death eligibility function
and store one immutable eligibility decision per death edge in the recognition view model. Keep real detected offers
and post-selection confirmation scans ahead of the new death gate.

**Tech Stack:** Python 3.11, `unittest`, sherpa-onnx 1.13.8, MeloTTS Chinese INT8 ONNX,
sounddevice 0.5.3, PowerShell launcher, JSON Lines worker protocol.

**Spec:** `docs/superpowers/specs/2026-09-22-gamebuddy-offline-speech-ocr-timing-design.md`

## Global Constraints

- Target Windows x64 with CPython 3.11.
- Runtime speech synthesis and first-run dependency preparation must work with networking disabled.
- Use sherpa-onnx 1.13.8, sherpa-onnx-core 1.13.8, sounddevice 0.5.3,
  and MeloTTS revision `a0d5c6a264c0ef92d70d8661d8cc502d79627cd6`.
- Bundle only `model.int8.onnx`; do not add the 170 MB full-precision model.
- Keep the wheelhouse to the five pinned wheels listed in Task 3; NumPy is intentionally not bundled.
- Keep `SpeechMessage`, `SpeechQueue`, `SpeechService`, `CompanionSpeechPolicy`,
  and the right-click voice toggle semantics unchanged.
- Preserve the existing level-3 initial-offer rule and post-selection confirmation scan.
- Death eligibility uses `confirmed_count`, never `completed_stage`.
- A death event with no valid level is ineligible for that entire death.
- A real detected offer remains OCR-eligible regardless of death telemetry.
- No modified method may exceed 80 lines; no modified source line may exceed 120 characters.
- Do not refactor or reformat code outside this feature.

## Review Focus

- A dead payload with no level must not reuse a cached earlier level;
  Task 1 adds a regression test using a model whose cached level is 11.
- Repeated `isDead=true` payloads must not recompute eligibility after `confirmed_count` changes;
  Task 1 pins the death-edge behavior.
- A real three-card offer must keep OCR open even when the current death is ineligible; Task 1 tests the override order.
- A cancelled worker request may finish late and must not complete a newer request;
  Tasks 4 and 5 test request IDs and stale-event rejection.
- A partial local wheel/model bundle must never trigger a network fallback or a half-valid runtime;
  Task 3 tests checksum failure and launcher flags.

---

### Task 1: Death-Level OCR Eligibility and Per-Death State

**Files:**
- Modify: `scripts/recognition_overlay/hexcore_gate.py:1-105`
- Modify: `scripts/recognition_overlay/view_model.py:18-27, 241-258, 786-881, 957-980, 1828-1870`
- Create: `tests/recognition_overlay/test_hexcore_gate.py`
- Create: `tests/recognition_overlay/test_view_model_death_gate.py`

**Interfaces:**
- Consumes: existing `RecognitionViewModel.confirmed_count() -> int` and Live Client player fields `level` and `isDead`.
- Produces: `death_ocr_allowed(level: int | None, confirmed_count: int) -> bool`.
- Extends: `hexcore_ocr_open` with the keyword parameter `death_scan_allowed: bool`.
- Produces model fields `_latest_death_level: int | None` and `_death_ocr_allowed: bool` scoped to the current match.

- [ ] **Step 1: Write failing pure gate tests**

Create `tests/recognition_overlay/test_hexcore_gate.py` with a table covering every threshold and the gate ordering:

```python
from __future__ import annotations

import unittest

from hexcore_gate import death_ocr_allowed, hexcore_ocr_open


class DeathOcrEligibilityTests(unittest.TestCase):
    def test_thresholds_use_confirmed_count(self) -> None:
        cases = (
            (None, 0, False),
            (6, 0, False),
            (7, 1, True),
            (7, 2, False),
            (10, 1, True),
            (11, 2, True),
            (11, 3, False),
            (14, 2, True),
            (15, 3, True),
            (15, 4, False),
        )
        for level, count, expected in cases:
            with self.subTest(level=level, count=count):
                self.assertEqual(expected, death_ocr_allowed(level, count))

    def test_real_offer_overrides_ineligible_death(self) -> None:
        self.assertTrue(hexcore_ocr_open(
            completed=2,
            level=8,
            is_dead=True,
            round_closed=False,
            offer_visible=True,
            death_scan_allowed=False,
        ))

    def test_level_three_initial_rule_does_not_turn_low_level_death_into_probe(self) -> None:
        self.assertTrue(hexcore_ocr_open(
            completed=0,
            level=3,
            is_dead=False,
            round_closed=False,
            death_scan_allowed=False,
        ))
        self.assertFalse(hexcore_ocr_open(
            completed=0,
            level=5,
            is_dead=True,
            round_closed=False,
            death_scan_allowed=False,
        ))

    def test_respawn_window_requires_eligible_death(self) -> None:
        self.assertFalse(hexcore_ocr_open(
            completed=2,
            level=12,
            is_dead=False,
            round_closed=False,
            seconds_since_respawn=1.0,
            death_scan_allowed=False,
        ))
        self.assertTrue(hexcore_ocr_open(
            completed=2,
            level=12,
            is_dead=False,
            round_closed=False,
            seconds_since_respawn=1.0,
            death_scan_allowed=True,
        ))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the pure gate tests and verify failure**

Run:

```bash
PYTHONPATH=scripts/recognition_overlay python3 -m unittest \
  tests.recognition_overlay.test_hexcore_gate -v
```

Expected: import failure because `death_ocr_allowed` does not exist, followed by no product changes.

- [ ] **Step 3: Implement the pure eligibility and gate order**

Add the following focused function to `hexcore_gate.py`:

```python
def death_ocr_allowed(level: int | None, confirmed_count: int) -> bool:
    if type(level) is not int or type(confirmed_count) is not int or confirmed_count < 0:
        return False
    if level < 7:
        return False
    if level < 11:
        return confirmed_count < 2
    if level < 15:
        return confirmed_count < 3
    return confirmed_count < 4
```

Change `hexcore_ocr_open` to accept `death_scan_allowed: bool = False`. Preserve real-offer priority, allow the
initial level-3 rule only while `is_dead is not True`, and allow death/respawn scanning only when
`death_scan_allowed` is true. Do not use unknown `is_dead` as a fail-open death.

- [ ] **Step 4: Run the pure gate tests and verify pass**

Run the command from Step 2.

Expected: all four test methods pass.

- [ ] **Step 5: Write failing view-model death-edge tests**

Create `tests/recognition_overlay/test_view_model_death_gate.py`. Use `TemporaryDirectory`,
`AugmentCatalog({})`, and `HistoryStore(temp / "history.jsonl")` to construct a real view model.
Include these tests:

```python
def player(level, is_dead):
    payload = {"isDead": is_dead, "championName": "Annie"}
    if level is not None:
        payload["level"] = level
    return payload


def confirmed(stage):
    return {
        "stage": stage,
        "source": "hud_icon_template",
        "augment_id": f"augment-{stage}",
        "name": f"海克斯{stage}",
    }
```

Test `7/1 -> allowed`, `7/2 -> denied`, `11/2 -> allowed`, `11/3 -> denied`, `15/3 -> allowed`, and
`15/4 -> denied`. Also add these named regression tests:

- `test_missing_level_on_death_does_not_reuse_cached_level`: first send alive level 11, then dead with no level;
  assert `_latest_death_level is None` and `vision_allowed()` is false.
- `test_repeated_dead_payload_does_not_recompute_eligibility`: enter death at level 11 with two confirmed picks,
  then add a third confirmed pick and send another dead payload; assert the death sequence and stored eligibility
  remain unchanged.
- `test_new_match_clears_death_state`: enter an eligible death, call `_start_new_match()`, and assert level is `None`
  and eligibility is false.
- `test_real_offer_keeps_vision_allowed`: set a three-card `offer` and assert `vision_allowed()` is true even when
  the stored death eligibility is false.

- [ ] **Step 6: Run the view-model tests and verify failure**

Run:

```bash
PYTHONPATH=scripts/recognition_overlay python3 -m unittest \
  tests.recognition_overlay.test_view_model_death_gate -v
```

Expected: failures for missing `_latest_death_level` and `_death_ocr_allowed` behavior.

- [ ] **Step 7: Record one decision per death edge and pass it to the gate**

Import `death_ocr_allowed` into `view_model.py`. Initialize both fields in `__init__` and reset them in
`_start_new_match`. In `_apply_live_player`, calculate from the local parsed `level`, not cached `self.live_level`:

```python
elif self.live_is_dead is not True and is_dead is True:
    self._death_sequence += 1
    self._latest_death_level = level
    self._death_ocr_allowed = death_ocr_allowed(level, self.confirmed_count())
    LOGGER.info(
        "death OCR decision match_id=%s sequence=%s level=%s confirmed=%s allowed=%s",
        self.match_id,
        self._death_sequence,
        level,
        self.confirmed_count(),
        self._death_ocr_allowed,
    )
```

Only extend death/respawn probes when `_death_ocr_allowed` is true. Pass the stored boolean as
`death_scan_allowed` from `vision_allowed()`. Do not recalculate it for repeated dead payloads.

- [ ] **Step 8: Run focused and nearby recognition tests**

Run:

```bash
PYTHONPATH=scripts/recognition_overlay python3 -m unittest \
  tests.recognition_overlay.test_hexcore_gate \
  tests.recognition_overlay.test_view_model_death_gate \
  tests.recognition_overlay.test_league_root -v
```

Expected: all tests pass.

- [ ] **Step 9: Commit the death gate**

```bash
git add scripts/recognition_overlay/hexcore_gate.py \
  scripts/recognition_overlay/view_model.py \
  tests/recognition_overlay/test_hexcore_gate.py \
  tests/recognition_overlay/test_view_model_death_gate.py
git commit -m "fix: gate OCR by death level and confirmed picks"
```

### Task 2: Startup Greeting and Hidden OCR Failure Guard

**Files:**
- Modify: `scripts/product/cat_animation.py:31-53`
- Modify: `tests/product/test_cat_animation.py:73-111`
- Modify: `tests/product/test_controller.py`
- Modify: `tests/product/test_speech_policy.py`

**Interfaces:**
- Consumes: final product views with `state` and `bubble_visible`.
- Produces: `AnimationStateController.update(view, now, dragging=False)` that keeps greeting active for three seconds.
- Preserves: `CompanionSpeechPolicy.update(view, now)` returning no message for hidden bubbles.

- [ ] **Step 1: Change the animation regression test before production code**

Replace `test_failure_preempts_transition` with:

```python
def test_startup_greeting_hides_failure_until_cycle_finishes(self) -> None:
    controller = AnimationStateController()
    failure = {"state": "ocr_error", "bubble_visible": True}
    self.assertEqual("greeting", controller.update(failure, 0.0))
    self.assertEqual("greeting", controller.update(failure, 2.99))
    self.assertEqual("failure", controller.update(failure, 3.0))
```

Add a controller test that passes an out-of-game or in-game snapshot with `offer_visible=False` and stale
`ocr_feedback={"state": "ocr_error"}`. Assert `bubble_visible` is false, `options` is empty, and the returned state
is not `ocr_error`.

Add a speech-policy test:

```python
def test_hidden_ocr_error_is_not_spoken(self) -> None:
    policy = CompanionSpeechPolicy()
    messages = policy.update(
        {"state": "ocr_error", "bubble_visible": False},
        1.0,
    )
    self.assertEqual((), messages)
    self.assertEqual((), policy.tick(4.0))
```

- [ ] **Step 2: Run the tests and verify the greeting test fails**

Run:

```bash
PYTHONPATH=scripts/product:scripts/recognition_overlay python3 -m unittest \
  tests.product.test_cat_animation \
  tests.product.test_controller \
  tests.product.test_speech_policy -v
```

Expected: the new startup failure test receives `failure` before 3.0 seconds.

- [ ] **Step 3: Make greeting precedence explicit**

In `AnimationStateController.update`, retain dragging as the temporary top-level override, initialize greeting if
needed, return `greeting` while `now < _greeting_until`, and only then return `select_base_state(view)`.
The method must remain under 80 lines and must not change timeline or frame assets.

Do not add another product state. Keep the controller and speech policy assertions as regression guards for the
already intended `offer_visible` boundary.

- [ ] **Step 4: Run the focused tests and commit**

Run the command from Step 2. Expected: all tests pass.

```bash
git add scripts/product/cat_animation.py tests/product/test_cat_animation.py \
  tests/product/test_controller.py tests/product/test_speech_policy.py
git commit -m "fix: keep startup greeting ahead of OCR failures"
```

### Task 3: Verified Offline Runtime and Model Bundle

**Files:**
- Create: `scripts/product/offline_speech_assets.py`
- Create: `tests/product/test_offline_speech_assets.py`
- Modify: `scripts/run_recognition_overlay.ps1`
- Create: `tests/recognition_overlay/test_offline_speech_bootstrap.py`
- Create: `vendor/speech/wheels/cp311-win_amd64/*.whl`
- Create: `assets/speech/melo-tts-zh_en-int8/**`
- Create: `assets/speech/SHA256SUMS`
- Create: `third_party/speech/THIRD_PARTY_NOTICES.md`
- Create: `third_party/speech/licenses/**`

**Interfaces:**
- Produces: `OfflineSpeechPaths` and `resolve_offline_speech_paths(bundle_root: Path) -> OfflineSpeechPaths`.
- Produces: `verify_manifest(bundle_root: Path, manifest_path: Path) -> list[str]`, returning error strings and
  an empty tuple on success.
- Produces: a local CPython 3.11 wheelhouse and a complete INT8 MeloTTS model tree.

- [ ] **Step 1: Write failing asset resolution and checksum tests**

Create `tests/product/test_offline_speech_assets.py` using temporary directories. Add these tests:

- `test_resolve_requires_model_tokens_lexicon_fsts_and_dictionary` creates every required file and asserts the
  returned paths.
- `test_missing_required_model_file_raises_value_error` omits `model.int8.onnx` and checks the path in the error.
- `test_manifest_accepts_matching_sha256` creates the complete required artifact set and matching manifest lines.
- `test_manifest_rejects_missing_changed_and_parent_paths` checks a missing file, a changed digest, and `../escape`.
- `test_manifest_rejects_empty_manifest` checks that an empty manifest cannot validate a complete bundle.
- `test_manifest_rejects_unlisted_extra_model_and_wheel_files` checks that extra artifacts fail verification.

The test manifest line format is:

```text
2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824  payload.txt
```

- [ ] **Step 2: Run the asset tests and verify failure**

Run:

```bash
PYTHONPATH=scripts/product python3 -m unittest \
  tests.product.test_offline_speech_assets -v
```

Expected: import failure because `offline_speech_assets.py` does not exist.

- [ ] **Step 3: Implement strict path and manifest verification**

Create an immutable `OfflineSpeechPaths` dataclass containing `model`, `tokens`, `lexicon`, `data_dir`,
`rule_fsts`, and `wheel_dir`. Resolve all paths beneath the supplied bundle root and reject missing files,
directories, absolute manifest paths, and `..` components. Stream files into `hashlib.sha256()` in 1 MiB chunks.
Verification must compare manifest paths with the exact required model and five-wheel artifact set. Empty manifests,
missing entries, duplicate entries, and extra model or wheel files must fail even when all listed hashes match.

Required model files are:

```text
model.int8.onnx
tokens.txt
lexicon.txt
date.fst
new_heteronym.fst
number.fst
phone.fst
dict/README.md
dict/hmm_model.utf8
dict/idf.utf8
dict/jieba.dict.utf8
dict/pos_dict/char_state_tab.utf8
dict/pos_dict/prob_emit.utf8
dict/pos_dict/prob_start.utf8
dict/pos_dict/prob_trans.utf8
dict/stop_words.utf8
dict/user.dict.utf8
```

- [ ] **Step 4: Run the asset tests and verify pass**

Run the command from Step 2. Expected: all asset tests pass.

- [ ] **Step 5: Vendor the pinned Windows wheels and model files**

Download the exact CPython 3.11 Windows x64 wheels with pip's cross-platform flags:

```bash
python3 -m pip download --only-binary=:all: --platform win_amd64 \
  --python-version 311 --implementation cp --abi cp311 \
  --dest vendor/speech/wheels/cp311-win_amd64 \
  sherpa-onnx==1.13.8 sounddevice==0.5.3
```

Download only the required model files from revision
`a0d5c6a264c0ef92d70d8661d8cc502d79627cd6` into
`assets/speech/melo-tts-zh_en-int8`, preserving the `dict` tree. Verify that `model.int8.onnx` has SHA-256:

```text
f085f5079e05f039b800aeb542f5253c26a303211b0c6465d0d9387977855a63
```

The preceding digest line must be checked against the downloaded file before commit. If the downloaded file does
not match, stop and re-read the pinned model repository rather than updating the expected value.

The wheelhouse must contain exactly these five files and no NumPy wheel:

```text
cffi-2.1.1-cp311-cp311-win_amd64.whl
pycparser-3.0-py3-none-any.whl
sherpa_onnx-1.13.8-cp311-cp311-win_amd64.whl
sherpa_onnx_core-1.13.8-py3-none-win_amd64.whl
sounddevice-0.5.3-py3-none-win_amd64.whl
```

- [ ] **Step 6: Generate the complete manifest and third-party notices**

Generate `assets/speech/SHA256SUMS` for every model, dictionary, FST, token, lexicon, and wheel file using paths
relative to the repository root, sorted bytewise. Add the upstream Apache-2.0, MIT, MIT-0, and dependency licenses
under `third_party/speech/licenses`; list every artifact, version, upstream URL, license, and pinned revision in
`THIRD_PARTY_NOTICES.md`. Include cppjieba 5.0.5 for the bundled dictionary tree and use the CFFI wheel's declared
MIT-0 license rather than labeling it as plain MIT.

Run:

```bash
python3 - <<'PY'
from pathlib import Path
import hashlib

roots = (Path("assets/speech/melo-tts-zh_en-int8"), Path("vendor/speech/wheels/cp311-win_amd64"))
files = sorted(path for root in roots for path in root.rglob("*") if path.is_file())
for path in files:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"{digest}  {path.as_posix()}")
PY
```

Redirect the reviewed output to `assets/speech/SHA256SUMS` using the repository's normal artifact-generation
workflow, then verify it through `verify_manifest`.

- [ ] **Step 7: Write the failing launcher contract test**

Create `tests/recognition_overlay/test_offline_speech_bootstrap.py`. Read `scripts/run_recognition_overlay.ps1`
as text and assert it contains all of the following tokens:

```python
required = (
    "--no-index",
    "--disable-pip-version-check",
    "vendor\\speech\\wheels\\cp311-win_amd64",
    "offline_speech_assets.py",
    "PYTHONPATH",
    "--no-deps",
)
```

Also assert the script does not contain `https://`, `http://`, `Invoke-WebRequest`, or `Start-BitsTransfer`.
Assert that the wheel directory contains exactly the five pinned filenames from Step 5, all five pinned package
requirements appear in the install command, and neither the wheel set nor launcher contains NumPy.

- [ ] **Step 8: Run the launcher contract test and verify failure**

Run:

```bash
PYTHONPATH=scripts/product:scripts/recognition_overlay python3 -m unittest \
  tests.recognition_overlay.test_offline_speech_bootstrap -v
```

Expected: failure because the launcher does not prepare the offline runtime.

- [ ] **Step 9: Add fail-closed local dependency preparation**

Before launching `app.py`, make `run_recognition_overlay.ps1`:

1. Run `offline_speech_assets.py --verify` with the resolved Python command.
2. Derive a versioned runtime directory below `%LOCALAPPDATA%\LoLRecognitionOverlay\speech-runtime\1.13.8-py311`.
3. If its success stamp is absent, install the five pinned wheels with `pip install --no-index`,
   `--disable-pip-version-check`, `--find-links`, and `--target`.
4. Write the success stamp only after pip exits zero.
5. Prepend the runtime directory to `PYTHONPATH` for the application and worker.
6. On verification or installation failure, set `GAMEBUDDY_OFFLINE_SPEECH_DISABLED=1`, log a warning, and still
   launch the main application.

Use separate PowerShell functions for verification and preparation so every function remains under 80 lines.

- [ ] **Step 10: Run focused tests, inspect size, and commit**

Run:

```bash
PYTHONPATH=scripts/product:scripts/recognition_overlay python3 -m unittest \
  tests.product.test_offline_speech_assets \
  tests.recognition_overlay.test_offline_speech_bootstrap -v
du -sh assets/speech vendor/speech
find assets/speech vendor/speech -type f -size +99M -print
git diff --check
```

Expected: tests pass, total addition is within the approved 75–100 MB range, and the final `find` prints nothing.

```bash
git add scripts/product/offline_speech_assets.py scripts/run_recognition_overlay.ps1 \
  tests/product/test_offline_speech_assets.py \
  tests/recognition_overlay/test_offline_speech_bootstrap.py \
  assets/speech vendor/speech third_party/speech
git commit -m "build: bundle verified offline speech runtime"
```

### Task 4: Persistent MeloTTS Worker

**Files:**
- Create: `scripts/product/offline_speech_worker.py`
- Create: `tests/product/test_offline_speech_worker.py`

**Interfaces:**
- Consumes: verified `OfflineSpeechPaths` from Task 3.
- Consumes commands: `{"command":"speak","request_id":N,"text":"推荐选择珠光护手"}`,
  `{"command":"cancel","request_id":N}`, and `{"command":"close"}`.
- Produces events: `ready`, `started`, `finished`, `cancelled`, and `error`; request-scoped events include
  `request_id`.

- [ ] **Step 1: Write failing worker engine tests with fake TTS and raw audio**

Create `tests/product/test_offline_speech_worker.py` around an `OfflineSpeechEngine` that receives injected
`tts_factory` and `raw_output_stream_factory` callables. The fake raw stream must invoke the supplied callback with
byte buffers so tests do not import sounddevice or NumPy. Add these tests:

- `test_start_loads_model_once_and_emits_ready` calls start twice and asserts one factory call.
- `test_speak_streams_float32_chunks_and_emits_started_then_finished_for_request` returns samples, asserts the raw
  stream receives the exact `array('f', samples).tobytes()` bytes across multiple callback chunks, and checks
  request ID 1 on both events.
- `test_empty_text_emits_error_without_synthesis` asserts no TTS call.
- `test_cancel_aborts_raw_stream_and_emits_cancelled` asserts the active raw stream is aborted and preserves the
  request ID.
- `test_cancelled_synthesis_result_is_discarded` blocks fake generation, cancels request 1, starts request 2,
  releases request 1, and asserts request 1 cannot emit `started` or `finished` afterward.
- `test_stale_raw_stream_callback_cannot_finish_newer_request` starts request 2 before invoking request 1's final
  callback and asserts the old callback cannot complete request 2.
- `test_close_aborts_raw_stream_and_joins_generation_thread` asserts deterministic shutdown.
- `test_worker_source_does_not_require_numpy_or_sounddevice_play` asserts the worker uses `RawOutputStream` and
  does not import NumPy or call `sounddevice.play`.

- [ ] **Step 2: Run worker tests and verify failure**

Run:

```bash
PYTHONPATH=scripts/product python3 -m unittest \
  tests.product.test_offline_speech_worker -v
```

Expected: import failure because the worker module does not exist.

- [ ] **Step 3: Implement model construction and request-scoped playback**

Build sherpa-onnx configuration from `OfflineSpeechPaths`:

```python
vits = sherpa_onnx.OfflineTtsVitsModelConfig(
    model=str(paths.model),
    lexicon=str(paths.lexicon),
    tokens=str(paths.tokens),
    data_dir=str(paths.data_dir),
)
model = sherpa_onnx.OfflineTtsModelConfig(
    vits=vits,
    num_threads=2,
    debug=False,
    provider="cpu",
)
config = sherpa_onnx.OfflineTtsConfig(
    model=model,
    rule_fsts=",".join(str(path) for path in paths.rule_fsts),
)
tts = sherpa_onnx.OfflineTts(config)
```

Generate with speaker 0 and speed 1.0. Use a generation thread so the command loop can process cancellation while
inference is running. Tag every generation result with its request ID; discard results that are no longer current.

Do not call `sounddevice.play` or use `sounddevice.OutputStream`: those convenience APIs require NumPy, which is not
part of the verified five-wheel bundle. Convert the generated sample iterable to native float32 PCM bytes with
standard-library `array('f', samples).tobytes()`. Play those bytes through a non-blocking
`sounddevice.RawOutputStream` configured with one channel and `dtype="float32"`.

The raw callback must copy at most the requested frame byte count into `outdata`, zero-fill any unused tail, and
advance a byte cursor. When the cursor reaches the end, signal callback completion using sounddevice's documented
callback-stop mechanism. Keep the request ID beside the PCM buffer and check it under the engine state lock before
opening the stream, during every callback, and before emitting completion. Cancellation must invalidate the request
and abort the active raw stream. A callback or generation result from an invalidated request must not emit `started`
or `finished` for that request or affect a newer request.

Route callback completion back through a thread-safe event or queue; do not perform blocking joins or protocol
writes in the real-time audio callback. Emit `finished` only after playback completion is observed for the current
request.

Keep JSON parsing, engine state, model construction, and `main()` in separate methods or functions under 80 lines.
Write JSON with `ensure_ascii=False` and flush every event.

- [ ] **Step 4: Run worker tests and verify pass**

Run the command from Step 2. Expected: all eight tests pass without importing the real native runtime.

- [ ] **Step 5: Add a subprocess protocol smoke test**

Add `test_main_protocol_reports_error_for_invalid_command` using a fake engine factory and `StringIO` input/output.
Feed an unknown command followed by `close`; assert one `ready`, one `error`, valid UTF-8 JSON, and clean exit.

- [ ] **Step 6: Run tests and commit**

```bash
PYTHONPATH=scripts/product python3 -m unittest \
  tests.product.test_offline_speech_worker -v
git diff --check
git add scripts/product/offline_speech_worker.py \
  tests/product/test_offline_speech_worker.py
git commit -m "feat: add persistent offline MeloTTS worker"
```

### Task 5: Offline Adapter and Application Wiring

**Files:**
- Create: `scripts/product/offline_speech.py`
- Create: `tests/product/test_offline_speech.py`
- Modify: `scripts/recognition_overlay/app.py:64-70, 127-181`
- Modify: `tests/recognition_overlay/test_app_speech.py:1-105`
- Delete: `scripts/product/windows_speech.py`
- Delete: `scripts/product/windows_speech_worker.ps1`
- Delete: `tests/product/test_windows_speech.py`

**Interfaces:**
- Consumes: the existing `SpeechAdapter` protocol.
- Produces: `OfflineSpeechAdapter(command: Sequence[str] | None = None, process_factory=subprocess.Popen)` with
  `start`, `speak`, `wait_finished`, `cancel`, and `close`.
- Consumes worker events from Task 4 and ignores events whose request ID is not current.

- [ ] **Step 1: Write failing adapter protocol tests**

Port the fake process streams from `test_windows_speech.py` into `test_offline_speech.py`, but make all request events
carry `request_id`. Add or preserve these named behaviors:

- one worker process is reused for multiple messages;
- the default command uses `sys.executable`, `offline_speech_worker.py`, and the repository bundle root;
- Unicode JSON Lines retain Chinese text;
- `finished` returns true, `error` and `cancelled` return false;
- ready timeout terminates the worker;
- missing executable and broken pipe return false without raising;
- malformed and unknown events fail the current wait;
- a late `finished` for cancelled request 1 does not finish request 2;
- close is idempotent and terminates a worker that does not exit in time.

- [ ] **Step 2: Run adapter tests and verify failure**

Run:

```bash
PYTHONPATH=scripts/product python3 -m unittest \
  tests.product.test_offline_speech -v
```

Expected: import failure because `OfflineSpeechAdapter` does not exist.

- [ ] **Step 3: Implement the adapter with request-aware events**

Use a persistent subprocess with UTF-8 text pipes and no console window. Use a state lock, write lock, ready event,
completion event, monotonically increasing request ID, and reader thread. The valid event set is:

```python
VALID_EVENTS = frozenset({"ready", "started", "finished", "cancelled", "error"})
```

On `speak`, allocate a new request ID, clear completion, and send it with the text. On `cancel`, send the current ID.
Only `finished` for the current ID sets a successful completion. `cancelled` or `error` sets unsuccessful completion.
Ignore stale request events. Use a 30-second ready timeout for first model load and preserve the existing close timeout.

If `GAMEBUDDY_OFFLINE_SPEECH_DISABLED=1`, `start()` must return false without spawning the worker.

- [ ] **Step 4: Run adapter and existing speech-service tests**

Run:

```bash
PYTHONPATH=scripts/product python3 -m unittest \
  tests.product.test_offline_speech \
  tests.product.test_speech -v
```

Expected: all tests pass, including high-priority interruption and subsequent playback.

- [ ] **Step 5: Wire the new adapter into the application test first**

In `test_app_speech.py`, import or patch `OfflineSpeechAdapter` instead of `WindowsSpeechAdapter`. Assert construction
receives the repository bundle root when needed and no `voice_name` argument. Keep the adapter-start-failure test to
prove the main app continues.

Run:

```bash
PYTHONPATH=scripts/product:scripts/recognition_overlay python3 -m unittest \
  tests.recognition_overlay.test_app_speech -v
```

Expected: failure because `app.py` still constructs `WindowsSpeechAdapter`.

- [ ] **Step 6: Replace application wiring and remove System.Speech files**

Import and construct `OfflineSpeechAdapter`, passing paths derived from `bundle_dir()`. Continue reading
`voice_enabled`; do not read or pass `voice_name`. Do not change `SpeechService`, `_publish_speech`, toggle, or stop
ordering.

Delete the Windows adapter, PowerShell worker, and their superseded tests only after the new application and adapter
tests pass.

- [ ] **Step 7: Run focused tests and commit**

```bash
PYTHONPATH=scripts/product:scripts/recognition_overlay python3 -m unittest \
  tests.product.test_offline_speech \
  tests.product.test_offline_speech_worker \
  tests.product.test_speech \
  tests.product.test_speech_policy \
  tests.recognition_overlay.test_app_speech -v
rg -n "System\.Speech|WindowsSpeechAdapter|windows_speech_worker" scripts tests
git diff --check
```

Expected: tests pass and the final `rg` prints no matches.

```bash
git add scripts/product/offline_speech.py scripts/recognition_overlay/app.py \
  tests/product/test_offline_speech.py tests/recognition_overlay/test_app_speech.py
git add -u scripts/product tests/product
git commit -m "feat: replace Windows speech with offline MeloTTS"
```

### Task 6: Documentation, Full Regression, Windows Acceptance, and Delivery

**Files:**
- Modify: `README.md:1-70`
- Modify: `docs/windows-gamebuddy-acceptance.md`
- Modify: `docs/project-management/implementation-status.md`
- Modify: `docs/project-management/release-history.md`
- Modify: `docs/project-management/regression-checklist.md`

**Interfaces:**
- Consumes: all deliverables from Tasks 1–5.
- Produces: user-facing startup instructions, auditable offline acceptance evidence, and identical pushed commits on
  `origin` and `github`.

- [ ] **Step 1: Update user-facing and project documentation**

Replace README statements about `System.Speech`, installed Windows voices, `voice_name`, and PowerShell workers.
Document that speech is bundled, initialized on first launch from local files, fully offline, and may take longer on
the first spoken message. Keep `voice_enabled` documentation and state that legacy `voice_name` is ignored.

Add acceptance rows for:

- network disabled before first launch;
- all wheel installs sourced from the local wheelhouse;
- process inspection showing no speech PowerShell worker;
- four recommendation rounds, including identical consecutive summaries;
- death gates at 3, 7, 11, and 15;
- missing death level and duplicate dead frames;
- startup greeting with no failure pose, bubble, or speech;
- missing model and unavailable audio device degradation;
- clean worker shutdown.

Record the feature and exact pinned versions in implementation status and release history.

- [ ] **Step 2: Run all automated validation**

Run:

```bash
PYTHONPATH=scripts/product:scripts/recognition_overlay:scripts/phase4 \
  python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 -m compileall -q scripts tests
python3 -m json.tool assets/gamebuddy/animations.json >/dev/null
git diff --check
awk 'length($0) > 120 { print FNR ":" length($0) ":" $0; failed=1 } END { exit failed }' \
  scripts/product/offline_speech.py \
  scripts/product/offline_speech_worker.py \
  scripts/product/offline_speech_assets.py \
  scripts/recognition_overlay/hexcore_gate.py \
  scripts/recognition_overlay/view_model.py
```

Expected: all unit tests pass, compileall succeeds, JSON is valid, no whitespace errors occur, and no modified source
line exceeds 120 characters.

- [ ] **Step 3: Audit method lengths and repository scope**

Use a small AST inspection command to print any modified Python function longer than 80 lines. The command must
report none for the files listed in Step 2. Inspect `git diff --stat 70f9dff..HEAD` and `git status --short`; confirm
only the approved speech, startup state, death OCR, vendored resources, tests, and documentation changed.

- [ ] **Step 4: Perform Windows x64 offline acceptance**

On a Windows x64 machine with Python 3.11 and networking disabled before launch, execute all ten cases from the spec.
For every case record Windows version, audio device, action, expected result, observed result, pass/fail, and relevant
`overlay.log` excerpts in `docs/windows-gamebuddy-acceptance.md`.

Do not mark actual sound output, network isolation, or process cleanup as passed from mocks or macOS tests. If the
Windows machine is unavailable, mark those rows `未执行` and report the delivery as awaiting Windows acceptance.

- [ ] **Step 5: Commit documentation and acceptance evidence**

```bash
git add README.md docs/windows-gamebuddy-acceptance.md \
  docs/project-management/implementation-status.md \
  docs/project-management/release-history.md \
  docs/project-management/regression-checklist.md
git commit -m "docs: record offline speech acceptance"
```

- [ ] **Step 6: Review the complete branch and push both remotes**

Review:

```bash
git status --short
git log --oneline 70f9dff..HEAD
git diff --stat 70f9dff..HEAD
git remote -v
```

After the branch review has no blocking findings, push the same branch tip:

```bash
git push origin feature/cat-ui-recommendation
git push github feature/cat-ui-recommendation
git rev-parse HEAD
git ls-remote origin refs/heads/feature/cat-ui-recommendation
git ls-remote github refs/heads/feature/cat-ui-recommendation
```

Expected: both remote hashes equal the local `HEAD`. Report the hash, automated test count, actual Windows acceptance
status, offline bundle size, largest vendored file, and clean or intentionally documented working-tree status.

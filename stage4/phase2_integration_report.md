# Phase2 Live Collection Integration Report

## Outcome

Phase2 collection integration is compiled and test-green in the requested build
tree. The product supports headless Live WGC collection with manual F8 edge
sampling and raw-detector automatic sampling, current SampleCollector contracts,
title-ROI preprocessing, and mode-aware icon/OCR fail-closed fusion.

Executable:

`F:\Realworld\lol\outputs\tmp\build_phase2_integration_clean\bin\lol_augment_assistant.exe`

## Implemented contract

- CLI: `--collect-samples`, `--dataset-root`, `--sample-hotkey F8`,
  `--no-hotkey`, and `--collect-suspect-confidence`.
- Collection does not imply `--preview`; preview creation remains gated solely
  by an explicit `--preview` option.
- Live F8 polling has two product call sites and both are
  `GetAsyncKeyState(VK_F8)` high-bit edge reads. No input is sent.
- Manual collection uses the latest owned LoL WGC frame. If detector ROI is not
  available, the current same-resolution calibration is used, otherwise the
  supported three-card seed is computed. Failure is emitted as structured JSON
  and is non-fatal.
- Automatic collection is driven directly by the raw detector visible/suspect
  result, independently of stable detection and accepted offers. An OCR/icon
  conflict is forced to UNKNOWN and causes an automatic debug/sample attempt.
- Live WGC requests use `SampleKind::Real` and event provenance
  `real_window_capture`. Replay collection uses `SampleKind::Unknown` and event
  provenance `replay`; it cannot be labelled real.
- Every collection saved/duplicate/error attempt emits one structured JSON line.
  `session_end.collection` summarizes `saved`, `duplicates`, `errors`, `manual`,
  and `auto`.
- Current SampleCollector contract is used directly. A committed sample has the
  canonical five files `RAW.png`, `LEFT_CARD.png`, `CENTER_CARD.png`,
  `RIGHT_CARD.png`, and `metadata.json`. Collector tests verify parseable
  metadata, dataset `schema_version`, provenance, and source metadata.
- OCR receives `primary_ocr_rect`; full-card ROIs remain available for collector
  debug crops. Product OCR applies exactly one deterministic neutral
  `gray_1x` preprocessing variant and one OCR call. Preprocess failure performs
  one raw-title fallback. The selected variant/strategy or fallback diagnostic
  is appended to the recognition backend and persisted by session recognition
  raw JSON.
- The icon manifest is loaded at product startup and requires exactly 245
  templates. Matching is mode-aware and retains top1, top2, margin, candidate
  IDs, and reason. OCR/icon conflict clears the final augment ID and returns
  UNKNOWN. Static-template self-match is only an engineering contract test, not
  a real-game accuracy claim.
- 2560x1600/16:10 is no longer rejected solely by aspect ratio. This is not a
  claim that real 2560x1600 offer/card/title/icon ROI calibration is accurate.

## Build and test evidence

Requested configure/build command:

```powershell
.\scripts\build.ps1 -Configuration Release `
  -BuildDirectory outputs\tmp\build_phase2_integration_clean
```

Configure exit `0`; Release build exit `0`.

The first test run in this pre-existing build tree was 19/20 because it retained
a pre-contract `sample_collector.obj`. No Collector source was changed. The same
requested tree was rebuilt from clean target outputs:

```powershell
cmake --build outputs\tmp\build_phase2_integration_clean `
  --config Release --clean-first --parallel

ctest --test-dir outputs\tmp\build_phase2_integration_clean `
  -C Release --output-on-failure
```

Final result: `20/20 passed`, `0 failed`, total `12.60s`.

Coverage includes SampleCollector canonical bundle/schema/provenance checks,
CLI headless and F8 constraints, title preprocessor, icon matcher, product
title-ROI/variant tracking, mode-aware top1/top2/margin, conflict UNKNOWN,
benchmark contract, and the original Phase1 regressions.

## Headless/focus audit

Source audit results:

- forbidden product calls (`SetForegroundWindow`, `SetFocus`,
  `AttachThreadInput`, `SendInput`, `keybd_event`, `mouse_event`): `0`
- `GetAsyncKeyState` product calls: `2`, both `VK_F8`
- `ShowWindow` calls: `1`, only explicit preview code with
  `SW_SHOWNOACTIVATE`
- activating `ShowWindow` calls: `0`
- running `lol_augment_assistant` processes after audit: `0`
- emergency preview is stopped: `REMAINING_PREVIEWS=0`

## Live WGC evidence and remaining manual evidence

The changed window list identified the game as HWND `0x70E0A`. A hidden process
was started without `--preview`:

```powershell
& 'F:\Realworld\lol\outputs\tmp\build_phase2_final_contract2\bin\lol_augment_assistant.exe' `
  --hwnd 0x70E0A `
  --collect-samples `
  --dataset-root 'F:\Realworld\lol\data\dataset\augment_offers' `
  --sample-hotkey F8 `
  --collect-suspect-confidence 0.35 `
  --max-seconds 60
```

Log: `F:\Realworld\lol\outputs\phase2_live_collection_final.jsonl`.

Observed facts:

- `preview_requested=false`
- `collection_active=true`
- `icon_template_count=245`
- dataset bucket resolved to `data\dataset\augment_offers\real`
- source summary kind `windows_graphics_capture`, HWND `0x70E0A`
- received `2`, converted `2`, dropped `1`, clean timeout/session close
- processed frame was a real WGC frame at `1920x1080`; raw detector confidence
  was `0.254110` with `insufficient_luma`
- no F8 edge was observed during the run and the 0.35 suspect threshold was not
  reached: `saved=0`, `manual=0`, `auto=0`, errors `0`
- canonical real `sample_*` directories after this run: `0`

Therefore the Live WGC path and headless collector initialization are verified,
but a newly saved real manual F8 bundle was not produced in this run. Existing
emergency preview images are not described as raw WGC samples.

For the next game offer, use the final requested-tree executable:

```powershell
& 'F:\Realworld\lol\outputs\tmp\build_phase2_integration_clean\bin\lol_augment_assistant.exe' `
  --hwnd 0x70E0A `
  --collect-samples `
  --dataset-root 'F:\Realworld\lol\data\dataset\augment_offers' `
  --sample-hotkey F8 `
  --max-seconds 3600
```

If HWND `0x70E0A` is no longer valid, first run:

```powershell
& 'F:\Realworld\lol\outputs\tmp\build_phase2_integration_clean\bin\lol_augment_assistant.exe' --list-windows
```

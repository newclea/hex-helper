# Phase2 headless / foreground safety independent audit

- Audit date: 2026-08-26 (Asia/Shanghai)
- Scope: `F:\Realworld\lol` current source, the normal build artifact, the packaged artifact, PE imports, and non-activating CLI behavior
- Mode: read-only audit; no product source was changed
- Prohibited operations observed: no preview was run, no keyboard/mouse control was sent, no process memory was read, and no reverse engineering was performed

## Decision

**Overall release gate: FAIL.**

The current source passes the narrow headless/focus-safety review: `--collect-samples` does not imply preview; the only input polling API is `GetAsyncKeyState(VK_F8)`; the product source has no foreground/focus-changing or input-injection call; preview is explicit and is shown with `WS_EX_NOACTIVATE` plus `SW_SHOWNOACTIVATE`. A live WGC startup experiment also preserved the LoL game foreground HWND throughout.

The release cannot receive an overall PASS because:

1. the normal and packaged executables are not equivalent to the current source, so the source result cannot be attributed to the shipped binaries;
2. the released `result` executable rejects `--collect-samples`;
3. `--hwnd` and `--window-title` do not authenticate the target as the LoL game and can select `LeagueClientUx`;
4. the prior integration report incorrectly treated launcher HWND `0x70E0A` as the game. The later real-game evidence is HWND `0x7210E8` and must supersede that claim.

No evidenced P0 defect was found: the audit did not reproduce a foreground transition or find a direct focus/input-injection API. There are three P1 defects/evidence gaps below.

## Gate matrix

| Requirement | Result | Evidence |
|---|---:|---|
| Source default `--collect-samples` is headless | PASS | `ApplicationOptions.preview` defaults to `false`; only explicit `--preview` sets it; preview construction is inside `if (options.preview)` |
| No foreground/focus-changing API in product source | PASS | Complete product-source `rg` found only `ShowWindow(window_, SW_SHOWNOACTIVATE)` in the activation group |
| No input injection in product source | PASS | Complete product-source `rg` returned zero hits for `SendInput`, `keybd_event`, `mouse_event`, cursor/blocking/message injection and synthetic pointer APIs |
| `GetAsyncKeyState` reads F8 only | PASS (source) | Exactly two calls, both `GetAsyncKeyState(VK_F8)` |
| Preview requires explicit opt-in | PASS (source) | Default false, parser changes it only for `--preview`, runtime creation only under `if (options.preview)` |
| Preview uses non-activating display | PASS (source) | `WS_EX_NOACTIVATE` and `ShowWindow(..., SW_SHOWNOACTIVATE)` |
| WGC startup preserves LoL foreground | PASS for exercised startup path | While WGC started on launcher HWND `0x70E0A`, foreground remained game HWND `0x8F11C2` before/during/after; child `MainWindowHandle` remained `0` |
| Full real-game collection evidence uses the actual game HWND | PASS after correction | New run used `0x7210E8`, title `League of Legends (TM) Client`, and produced 599 received / 597 converted frames plus 6 saved samples |
| Title/HWND selector excludes launcher | FAIL | `--hwnd` checks only `IsWindow` and `IsWindowVisible`; title matching checks only visible, nonzero, unique substring; live `0x70E0A` maps to `LeagueClientUx` |
| Current source equals normal build artifact | FAIL | `main.cpp` is newer than the executable; current executable help omits collection flags and PE has no `GetAsyncKeyState` import |
| Packaged artifact supports Phase2 collection | FAIL | `result\bin\lol_augment_assistant.exe --hwnd 1 --collect-samples ...` exits 64 with `Unknown argument` |

## Source evidence

### Headless default and explicit preview

- `src/app/cli.h:49-53`: `collect_samples{false}` and `preview{false}` are independent fields.
- `src/app/cli.cpp:193-205`: only an explicit `--preview` sets `options.preview = true`; `--collect-samples` only sets `options.collect_samples = true`.
- `src/app/main.cpp:974-981`: `DebugPreviewWindow` is allocated and created only inside `if (options.preview)`.
- `config/default.json:22`: reference default is also `"preview": false` (the same file states `consumed_by_cli: false`, so CLI source remains authoritative).
- `scripts/run.ps1:31-54` and `result/scripts/run.ps1:31-54`: neither wrapper inserts `--preview`; both forward the effective arguments with the PowerShell call operator.
- `tests/integration/cli_test.cpp:111-121`: source contract explicitly checks that `--collect-samples` alone leaves preview false and only an additional `--preview` enables it.

Conclusion: in the current source, collection does not implicitly create or show a window.

### Foreground, focus and input API scan

The product scan covered 74 files under `src`, `include`, and `scripts` (65 C/C++ headers/sources, 4 PowerShell files and 5 Python files), plus `result/scripts` and `CMakeLists.txt`. Tests were scanned separately because some tests intentionally create visible windows and were not run.

Product-source results:

```text
FOCUS_ACTIVATION
src\output\debug_preview_window.cpp:131: ShowWindow(window_, SW_SHOWNOACTIVATE)

INPUT_INJECTION
NO_MATCHES

INPUT_OBSERVATION_HOOKS
src\app\main.cpp:824:  GetAsyncKeyState(VK_F8)
src\app\main.cpp:1338: GetAsyncKeyState(VK_F8)

PROCESS_LAUNCH
NO_MATCHES

DYNAMIC_API (GetProcAddress/LoadLibrary in product source)
NO_MATCHES
```

The searched activation set included `SetForegroundWindow`, `SetActiveWindow`, `SetFocus`, `BringWindowToTop`, `SwitchToThisWindow`, `AttachThreadInput`, `AllowSetForegroundWindow`, `LockSetForegroundWindow`, `FlashWindow*`, `SetWindowPos`, topmost styles and `ShowWindow*`.

The searched injection set included `SendInput`, `keybd_event`, `mouse_event`, `SetCursorPos`, `ClipCursor`, `BlockInput`, `SendMessage*`, `PostMessage*`, `PostThreadMessage`, `NtUser*`, touch injection and synthetic pointer injection. There were zero product hits.

`GetAsyncKeyState` is guarded by collection state at `src/app/main.cpp:819-843`; initialization enables polling only when collection is requested, the hotkey remains enabled, and the source is not replay (`src/app/main.cpp:846-855`). The two calls both use `VK_F8`; no other key-state API was found.

### Preview implementation

- `src/output/debug_preview_window.cpp:116`: `WS_EX_NOACTIVATE | WS_EX_APPWINDOW`.
- `src/output/debug_preview_window.cpp:124-127`: the sole product `CreateWindowExW` call uses that extended style.
- `src/output/debug_preview_window.cpp:131`: the sole product `ShowWindow` call uses `SW_SHOWNOACTIVATE`.
- `src/output/debug_preview_window.cpp:132`: `UpdateWindow` requests paint and does not activate the window.
- No topmost style, focus call, foreground call, input hook or injection call is present in the preview implementation.

The test tree contains visible-window code: `tests/capture/capture_minimal_test.cpp:94-100` calls `CreateWindowExW`, `ShowWindow(..., SW_SHOWNORMAL)` and `UpdateWindow`; `tests/output/output_test.cpp:310-354` contains a two-second preview smoke test. Therefore the full CTest suite was intentionally not run. Only the headless `cli_test.exe` was run.

### WGC capture startup

The live source passes a supplied HWND to `IGraphicsCaptureItemInterop::CreateForWindow` (`src/capture/windows_graphics_capture_source.cpp:107-115`) and starts the session with `session_.StartCapture()` (`:378-380`). Neither path contains an activation call.

## HWND correction: `0x7210E8` game vs `0x70E0A` launcher

This distinction is mandatory for interpreting the prior evidence.

### Correct real-game evidence: `0x7210E8`

`outputs/phase2_live_game_20260826_000313.jsonl:1` records:

- `preview_requested=false`;
- `collection_active=true`;
- collection requested with F8 configured;
- real WGC source and real dataset bucket.

`outputs/phase2_live_game_20260826_000313.jsonl:19` records:

- HWND/source ID `0x7210E8`;
- `received=599`, `converted=597`, `dropped=576`;
- clean `status=closed`, `capture_state=closed`, `close_ok=true`;
- `saved=6`, `duplicates=5`, `errors=0`, `auto=11`.

The saved metadata independently identifies the target. For example, `data/dataset/augment_offers/real/sample_1787673884705782_f595_p30896_n5_4d4a1700e84b73c6/metadata.json` records:

```json
"source":{"kind":"windows_graphics_capture","id":"hwnd:0x7210E8"},
"window":{"title":"League of Legends (TM) Client","id":"0x7210E8","process_id":48988},
"resolution":{"width":2560,"height":1600,"stride":10240}
```

`0x7210E8` is no longer an extant HWND at audit close, which is normal for HWND lifetime. Its contemporaneous session log and six sample metadata files are the evidence.

### Incorrect old evidence: `0x70E0A`

`outputs/phase2_integration_report.md:98-119` says `0x70E0A` was the game. That statement is invalid.

At audit close, two independent read-only checks mapped `0x70E0A` to:

```text
HWND:        0x70E0A
PID:         39116
ProcessName: LeagueClientUx
Title:       League of Legends
```

The product's own `--list-windows` returned the same HWND, title and PID. The old log `outputs/phase2_live_collection_final.jsonl` captured only two 1920x1080 frames from that HWND and saved zero samples; it is launcher-path evidence, not real-game evidence. It must not be used to claim a LoL game collection pass.

## Window-selection safety

`src/app/main.cpp:1253-1280` shows:

- direct `--hwnd` validates only non-null/current `IsWindow` and `IsWindowVisible`;
- title selection enumerates visible titled top-level windows and delegates to a unique case-insensitive substring match.

`src/app/cli.cpp:352-374` rejects zero matches and ambiguity, which is useful. It does not verify process executable/name, window class, owner, dimensions or game identity. Therefore:

- direct `--hwnd 0x70E0A` accepts `LeagueClientUx`;
- if only the launcher is visible, `--window-title "League of Legends"` can uniquely select it;
- while both observed LoL windows are visible, that same title query is rejected as ambiguous rather than silently picking one.

The audit reproduced the ambiguous case with both windows present. Exit code was 3 and candidates were the game title `League of Legends (TM) Client` and launcher title `League of Legends`; foreground remained unchanged.

## Runtime foreground evidence

All child processes were launched with `UseShellExecute=false`, `CreateNoWindow=true`, redirected stdout/stderr, and no `--preview`. `GetForegroundWindow` was sampled before, repeatedly during, and after each process. The child `MainWindowHandle` was also sampled.

| Test | Exit | Foreground before / during / after | Child main HWND | Result |
|---|---:|---|---|---|
| built `cli_test.exe` | 0 | `0x8F11C2` / only `0x8F11C2` / `0x8F11C2` | only `0x0` | stable |
| normal build `--help` | 0 | same | only `0x0` | stable |
| normal build `--list-windows` | 0 | same | only `0x0` | stable |
| normal build `--hwnd 1 --collect-samples` | 3 | same | only `0x0` | stable, invalid HWND early exit |
| packaged build `--help` | 0 | same | only `0x0` | stable |
| packaged build `--collect-samples` | 64 | same | only `0x0` | stable, option rejected |
| normal build title ambiguity | 3 | same | only `0x0` | stable |
| normal build WGC startup on launcher `0x70E0A` while game `0x8F11C2` was foreground | 3 | `0x8F11C2` / only `0x8F11C2` / `0x8F11C2` | only `0x0` | stable |

For the last test, WGC startup succeeded on the background launcher and processing was stopped before session/dataset writes by intentionally supplying the existing non-JSON `README.md` as the knowledge path. The resulting error was `Unable to load augment catalog: invalid integer at byte 0`. Because execution reached catalog loading after `source.Start()`, it exercises `CreateForWindow`/`StartCapture` while preserving the game foreground.

This is direct evidence against focus theft during WGC startup. The existing real-game run at `0x7210E8` did not record `GetForegroundWindow`, so it cannot independently prove the full-run foreground invariant; it proves headless collection (`preview_requested=false`) and successful real-game capture.

## PE imports and artifact provenance

### Hashes

| Artifact | Bytes | UTC timestamp | SHA-256 |
|---|---:|---|---|
| `outputs/tmp/build/bin/lol_augment_assistant.exe` | 750080 | 2026-08-25 15:18:00Z | `3365DA3A6B9EE9A504AC5B511F8C927CD711305502090A006819D0D5DEF05CC9` |
| `result/bin/lol_augment_assistant.exe` | 731648 | 2026-08-25 13:45:12Z | `9C045F1EC9E3F3E695AFB3DF1556737613972695DCB35A71F468E2518B70661E` |

`src/app/main.cpp` is newer (2026-08-25 15:48:18Z) than the normal executable. `tests/integration/cli_test.cpp` is newer (15:26:36Z) than `cli_test.exe` (15:17:56Z). The executed tests therefore cannot certify the current source revision.

### Import findings

`llvm-readobj --coff-imports` and `dumpbin /imports` were run on both executables. Direct imports from the focus/input risk set were zero for both. There were no delay-import markers.

Both executables import these USER32 functions:

```text
AdjustWindowRectEx, BeginPaint, CreateWindowExW, DefWindowProcW,
DestroyWindow, DispatchMessageW, DrawTextW, EndPaint, EnumWindows,
FillRect, GetClientRect, GetWindowLongPtrW, GetWindowTextLengthW,
GetWindowTextW, GetWindowThreadProcessId, InvalidateRect, IsWindow,
IsWindowVisible, LoadCursorW, PeekMessageW, RegisterClassExW,
SetWindowLongPtrW, ShowWindow, TranslateMessage, UpdateWindow
```

`ShowWindow` and `CreateWindowExW` are expected because preview code is linked into the executable; source control-flow evidence is needed to show that the calls are explicit/non-activating. Neither executable directly imports `GetAsyncKeyState`, despite the current source's two F8 calls. This is additional evidence that neither binary represents current `main.cpp`.

The binaries import generic `GetProcAddress`/`LoadLibraryExW` through the WinRT/runtime dependency surface, while the product source has no explicit dynamic-resolution call. Under the audit's no-reverse constraint, PE imports plus complete source scan are the available static evidence; no claim is made about disassembly.

### CLI/source divergence

- Current source help at `src/app/main.cpp:335-339` documents collection flags.
- Normal build `--help` omits all collection flags, although its parser accepts `--collect-samples`.
- Packaged build `--help` also omits them and its parser rejects `--collect-samples` with exit 64 / `Unknown argument`.
- Normal build has no direct `GetAsyncKeyState` import, so its compiled behavior cannot be used to validate the current source's F8-only implementation.

## Defects

### P0

None evidenced. No focus transition, foreground-changing API, or input-injection API was observed.

### P1-1 — Build/release artifacts do not represent the audited source

Impact: a source PASS cannot certify either executable. The released binary does not provide Phase2 collection; the normal build has internally inconsistent help/behavior and lacks the current source's F8 import.

Required closure: produce one clean release build after the current source timestamp, publish its SHA-256, verify help and parser behavior, rerun PE imports, and rerun the foreground invariant on that exact hash.

### P1-2 — HWND/title selection can target the launcher

Impact: the product can capture `LeagueClientUx` while reporting a valid visible WGC target. This caused the old report to mislabel launcher evidence as game evidence. It does not by itself prove focus theft; the audit's launcher WGC startup preserved game foreground.

Required closure: identify the target as the LoL game (at minimum process identity plus window-level checks) and reject `LeagueClientUx`; preserve ambiguity rejection.

### P1-3 — No current-artifact end-to-end foreground regression

Impact: source scanning and the startup experiment are strong evidence, but there is no automated assertion that samples `GetForegroundWindow` across a complete valid `--collect-samples` run of the exact release hash. The existing real-game `0x7210E8` log proves headless capture and sample saving, but it did not log foreground HWND.

Required closure: on the rebuilt release hash, record foreground HWND before startup, periodically during the full collection interval, and after shutdown; fail on any transition attributable to the product. Record target HWND/process identity separately.

## Reproduction commands

### Source scan

```powershell
$roots = @('src','include','scripts','result/scripts','CMakeLists.txt')
rg -n -i --glob '*.{c,cc,cpp,cxx,h,hpp,inl,ps1,cmake,txt}' `
  'SetForegroundWindow|SetActiveWindow|SetFocus\s*\(|BringWindowToTop|SwitchToThisWindow|AttachThreadInput|AllowSetForegroundWindow|LockSetForegroundWindow|FlashWindow(?:Ex)?|SetWindowPos|HWND_TOPMOST|WS_EX_TOPMOST|ShowWindow(?:Async)?\s*\(' $roots
rg -n -i --glob '*.{c,cc,cpp,cxx,h,hpp,inl,ps1,cmake,txt}' `
  'SendInput|keybd_event|mouse_event|SetCursorPos|ClipCursor|BlockInput|SendMessage(?:A|W)?\s*\(|PostMessage(?:A|W)?\s*\(|PostThreadMessage|NtUser|InjectTouchInput|InjectSyntheticPointerInput' $roots
rg -n -i --glob '*.{c,cc,cpp,cxx,h,hpp,inl}' `
  'GetAsyncKeyState|GetKeyState\s*\(|GetKeyboardState|RegisterHotKey|SetWindowsHookEx|WH_KEYBOARD|WH_MOUSE|RegisterRawInputDevices|GetRawInputData' src include
```

### PE imports

```powershell
llvm-readobj --coff-imports .\outputs\tmp\build\bin\lol_augment_assistant.exe
llvm-readobj --coff-imports .\result\bin\lol_augment_assistant.exe
dumpbin /nologo /imports .\outputs\tmp\build\bin\lol_augment_assistant.exe
dumpbin /nologo /imports .\result\bin\lol_augment_assistant.exe
```

### CLI divergence

```powershell
.\outputs\tmp\build\bin\lol_augment_assistant.exe --help
.\outputs\tmp\build\bin\lol_augment_assistant.exe --hwnd 1 --collect-samples --max-seconds 0.01
.\result\bin\lol_augment_assistant.exe --help
.\result\bin\lol_augment_assistant.exe --hwnd 1 --collect-samples --max-seconds 0.01
```

### Corrected HWND evidence

```powershell
rg -n '0x7210E8' .\outputs\phase2_live_game_20260826_000313.jsonl .\data\dataset\augment_offers\real
Get-Process LeagueClientUx | Select-Object Id,ProcessName,MainWindowTitle,MainWindowHandle
.\outputs\tmp\build\bin\lol_augment_assistant.exe --list-windows
```

HWND values are ephemeral. `0x7210E8` is historical contemporaneous evidence and must not be expected to remain valid; resolve and record process identity for each new run.

## Final statement

The observed evidence supports this narrow statement: **the current source is designed for headless collection and the exercised WGC startup did not steal LoL foreground focus.** It does not support an unconditional release PASS because the binaries are stale/different and target selection accepts the launcher. The old `0x70E0A` “game” claim is rejected; the correct real-game collection evidence is `0x7210E8`.

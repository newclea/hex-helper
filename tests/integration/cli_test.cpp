#include "cli.h"

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using lol_assistant::app::CaptureBackend;
using lol_assistant::app::Command;
using lol_assistant::app::ForceRecognitionBurst;
using lol_assistant::app::kForceRecognitionBurstFrames;
using lol_assistant::app::kLeagueGameWindowTitle;
using lol_assistant::app::kLeagueLauncherWindowTitle;
using lol_assistant::app::LcuContextMode;
using lol_assistant::app::LiveClientMode;
using lol_assistant::app::Mode;
using lol_assistant::app::ParseCommandLine;
using lol_assistant::app::PassiveHotkeyEdge;
using lol_assistant::app::RecognitionProgress;
using lol_assistant::app::RecognitionSessionStatus;
using lol_assistant::app::RecordRecognitionObservation;
using lol_assistant::app::ReplayKind;
using lol_assistant::app::ResolveRecognitionSessionStatus;
using lol_assistant::app::SelectedPosition;
using lol_assistant::app::SelectWindowByHandle;
using lol_assistant::app::SelectWindowByTitle;
using lol_assistant::app::SessionStopReason;
using lol_assistant::app::ShouldStopAfterRecognitionAttempt;
using lol_assistant::app::SourceKind;
using lol_assistant::app::WindowCandidate;

void Require(const bool condition, const std::string &message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

void TestCommands(const std::filesystem::path &working_directory) {
  const auto help = ParseCommandLine({L"--help"}, working_directory);
  Require(help.ok() && help.options->command == Command::Help,
          "--help must select the help command");

  const auto version = ParseCommandLine({L"--version"}, working_directory);
  Require(version.ok() && version.options->command == Command::Version,
          "--version must select the version command");

  const auto list = ParseCommandLine({L"--list-windows"}, working_directory);
  Require(list.ok() && list.options->command == Command::ListWindows,
          "--list-windows must select the window-list command");
  Require(!ParseCommandLine({L"--help", L"--once"}, working_directory).ok(),
          "command options must reject operational arguments");
  const auto probe =
      ParseCommandLine({L"--probe-live-client"}, working_directory);
  Require(probe.ok() && probe.options->command == Command::ProbeLiveClient,
          "--probe-live-client must select the standalone API probe");
  Require(!ParseCommandLine({L"--probe-live-client", L"--live-client", L"off"},
                            working_directory)
               .ok(),
          "probe command must reject operational arguments");
}

void TestStrictParsing(const std::filesystem::path &working_directory) {
  const auto parsed = ParseCommandLine({L"--hwnd",
                                        L"0x2A",
                                        L"--champion",
                                        L"Kai'Sa",
                                        L"--mode",
                                        L"KIWI_JADE",
                                        L"--knowledge",
                                        L"config/../knowledge.json",
                                        L"--workspace",
                                        L"outputs/runtime/../runtime/cli",
                                        L"--preview",
                                        L"--max-seconds",
                                        L"12.5",
                                        L"--once",
                                        L"--selected",
                                        L"center",
                                        L"--collect-samples",
                                        L"--dataset-root",
                                        L"data/dataset/../dataset/live",
                                        L"--sample-hotkey",
                                        L"F8",
                                        L"--collect-suspect-confidence",
                                        L"0.42",
                                        L"--force-recognition-hotkey",
                                        L"F9",
                                        L"--capture-backend",
                                        L"desktop",
                                        L"--live-client",
                                        L"off",
                                        L"--lcu-context",
                                        L"off",
                                        L"--completed-offers",
                                        L"2"},
                                       working_directory);
  Require(parsed.ok(), "valid full command line must parse");
  const auto &options = *parsed.options;
  Require(options.source_kind == SourceKind::WindowHandle &&
              options.window_handle == static_cast<std::uintptr_t>(42U),
          "hexadecimal HWND must parse exactly");
  Require(options.champion == L"Kai'Sa" && options.mode == Mode::KiwiJade,
          "manual champion/mode must be preserved");
  Require(options.preview && options.once &&
              options.selected == SelectedPosition::Center,
          "flag and selected options must parse");
  Require(options.max_seconds == 12.5,
          "bounded floating-point max-seconds must parse");
  Require(options.knowledge_path.is_absolute() &&
              options.workspace_path.is_absolute(),
          "knowledge/workspace paths must be absolute");
  Require(options.knowledge_path ==
              (working_directory / L"knowledge.json").lexically_normal(),
          "knowledge path must be lexically normalized");
  Require(options.workspace_path ==
              (working_directory / L"outputs/runtime/cli").lexically_normal(),
          "workspace path must be lexically normalized");
  Require(options.collect_samples && options.sample_hotkey_enabled &&
              options.force_recognition_hotkey_enabled &&
              options.collect_suspect_confidence == 0.42 &&
              options.capture_backend == CaptureBackend::Desktop &&
              options.capture_backend_explicit,
          "collection, F8, F9, backend, and suspect confidence must parse");
  Require(options.live_client_mode == LiveClientMode::Off &&
              options.live_client_mode_explicit,
          "explicit Live Client mode must parse");
  Require(options.lcu_context_mode == LcuContextMode::Off &&
              options.lcu_context_mode_explicit &&
              options.completed_offers == 2U,
          "explicit LCU context mode must parse");
  Require(options.dataset_root ==
              (working_directory / L"data/dataset/live").lexically_normal(),
          "dataset root must be absolute and lexically normalized");

  const auto decimal =
      ParseCommandLine({L"--hwnd", L"4096"}, working_directory);
  Require(decimal.ok() && decimal.options->window_handle == 4096U &&
              decimal.options->mode == Mode::Kiwi &&
              !decimal.options->collect_samples &&
              decimal.options->sample_hotkey_enabled &&
              decimal.options->force_recognition_hotkey_enabled &&
              decimal.options->capture_backend == CaptureBackend::Auto &&
              !decimal.options->capture_backend_explicit &&
              decimal.options->live_client_mode == LiveClientMode::Auto &&
              !decimal.options->live_client_mode_explicit &&
              decimal.options->lcu_context_mode == LcuContextMode::Auto &&
              !decimal.options->lcu_context_mode_explicit &&
              decimal.options->dataset_root ==
                  (working_directory / L"data/dataset/augment_offers")
                      .lexically_normal(),
          "decimal HWND and collection defaults must parse");

  const auto exact_title = ParseCommandLine(
      {L"--window-title", std::wstring{kLeagueGameWindowTitle}},
      working_directory);
  Require(exact_title.ok() &&
              exact_title.options->source_kind == SourceKind::WindowTitle &&
              exact_title.options->window_title == kLeagueGameWindowTitle,
          "the exact game window title must be preserved for live validation");

  const auto replay_collection =
      ParseCommandLine({L"--replay", working_directory.wstring(),
                        L"--collect-samples", L"--no-hotkey"},
                       working_directory);
  Require(
      replay_collection.ok() && replay_collection.options->collect_samples &&
          !replay_collection.options->sample_hotkey_enabled &&
          !replay_collection.options->force_recognition_hotkey_enabled &&
          replay_collection.options->live_client_mode == LiveClientMode::Off &&
          replay_collection.options->lcu_context_mode == LcuContextMode::Off &&
          !replay_collection.options->preview,
      "replay collection must be headless with live hotkeys disabled");

  const auto headless_collection = ParseCommandLine(
      {L"--hwnd", L"4096", L"--collect-samples"}, working_directory);
  Require(headless_collection.ok() &&
              headless_collection.options->collect_samples &&
              !headless_collection.options->preview,
          "--collect-samples alone must always remain headless");
  const auto explicit_preview =
      ParseCommandLine({L"--hwnd", L"4096", L"--collect-samples", L"--preview"},
                       working_directory);
  Require(explicit_preview.ok() && explicit_preview.options->preview,
          "collection preview must require an explicit --preview");

  const auto force_disabled =
      ParseCommandLine({L"--hwnd", L"4096", L"--no-force-recognition-hotkey"},
                       working_directory);
  Require(force_disabled.ok() &&
              !force_disabled.options->force_recognition_hotkey_enabled,
          "live F9 force recognition must be explicitly disableable");

  const auto overlay_live = ParseCommandLine(
      {L"--hwnd",
       L"0xb0b14",
       L"--capture-backend",
       L"auto",
       L"--lcu-context",
       L"off",
       L"--live-client",
       L"auto",
       L"--completed-offers",
       L"0",
       L"--mode",
       L"KIWI",
       L"--max-seconds",
       L"86400",
       L"--no-hotkey",
       L"--no-force-recognition-hotkey"},
      working_directory);
  Require(overlay_live.ok() && !overlay_live.options->collect_samples &&
              !overlay_live.options->sample_hotkey_enabled &&
              !overlay_live.options->force_recognition_hotkey_enabled &&
              overlay_live.options->source_kind == SourceKind::WindowHandle,
          "overlay live command must parse without --collect-samples");
}

[[nodiscard]] std::string ReadText(const std::filesystem::path &path) {
  std::ifstream input(path, std::ios::binary);
  Require(static_cast<bool>(input), "source guard must open product source");
  return {std::istreambuf_iterator<char>{input},
          std::istreambuf_iterator<char>{}};
}

void TestHeadlessInputApiSourceGuard() {
  const std::filesystem::path source_root{LOL_ASSISTANT_SOURCE_ROOT};
  const std::vector<std::string> forbidden{"SetForegroundWindow",
                                           "SetFocus(",
                                           "AttachThreadInput",
                                           "SendInput",
                                           "keybd_event",
                                           "mouse_event",
                                           "OpenProcess(",
                                           "ReadProcessMemory",
                                           "WriteProcessMemory",
                                           "EnumProcessModules",
                                           "CreateToolhelp32Snapshot",
                                           "RegisterHotKey",
                                           "SetWindowsHookEx",
                                           "PostMessage(",
                                           "SendMessage(",
                                           "SetActiveWindow",
                                           "BringWindowToTop",
                                           "SwitchToThisWindow",
                                           "PrintWindow"};
  std::size_t async_key_calls = 0U;
  std::size_t f8_async_key_calls = 0U;
  std::size_t f9_async_key_calls = 0U;
  std::size_t left_button_async_key_calls = 0U;
  std::size_t activating_show_window_calls = 0U;

  for (const auto &entry :
       std::filesystem::recursive_directory_iterator(source_root / L"src")) {
    if (!entry.is_regular_file() || (entry.path().extension() != L".cpp" &&
                                     entry.path().extension() != L".h")) {
      continue;
    }
    const std::string text = ReadText(entry.path());
    for (const auto &api : forbidden) {
      Require(text.find(api) == std::string::npos,
              "product source must not call forbidden API " + api);
    }

    std::size_t offset = 0U;
    while ((offset = text.find("GetAsyncKeyState", offset)) !=
           std::string::npos) {
      ++async_key_calls;
      const auto close = text.find(')', offset);
      Require(close != std::string::npos,
              "GetAsyncKeyState call must have a closing parenthesis");
      if (close != std::string::npos &&
          text.substr(offset, close - offset).find("VK_F8") !=
              std::string::npos) {
        ++f8_async_key_calls;
      } else if (close != std::string::npos &&
                 text.substr(offset, close - offset).find("VK_F9") !=
                     std::string::npos) {
        ++f9_async_key_calls;
      } else if (close != std::string::npos &&
                 text.substr(offset, close - offset).find("VK_LBUTTON") !=
                     std::string::npos) {
        ++left_button_async_key_calls;
      }
      offset += std::string{"GetAsyncKeyState"}.size();
    }

    offset = 0U;
    while ((offset = text.find("ShowWindow(", offset)) != std::string::npos) {
      const auto line_end = text.find('\n', offset);
      const auto call = text.substr(offset, line_end - offset);
      if (call.find("SW_SHOWNOACTIVATE") == std::string::npos) {
        ++activating_show_window_calls;
      }
      offset += std::string{"ShowWindow("}.size();
    }
  }

  Require(f8_async_key_calls > 0U && f9_async_key_calls > 0U &&
              left_button_async_key_calls > 0U &&
              async_key_calls == f8_async_key_calls + f9_async_key_calls +
                                     left_button_async_key_calls,
          "product source may read only VK_F8/VK_F9/VK_LBUTTON through "
          "GetAsyncKeyState");
  Require(activating_show_window_calls == 0U,
          "product source must not activate a window through ShowWindow");
}

void TestRejections(const std::filesystem::path &working_directory) {
  Require(!ParseCommandLine({}, working_directory).ok(),
          "run mode must require one source");
  Require(!ParseCommandLine({L"--wat"}, working_directory).ok(),
          "unknown option must be rejected");
  Require(!ParseCommandLine({L"--hwnd"}, working_directory).ok(),
          "missing option value must be rejected");
  Require(
      !ParseCommandLine({L"--hwnd", L"0x10000000000000000"}, working_directory)
           .ok(),
      "out-of-range HWND must be rejected");
  Require(!ParseCommandLine({L"--hwnd", L"12", L"--max-seconds", L"0"},
                            working_directory)
               .ok(),
          "zero max-seconds must be rejected");
  Require(!ParseCommandLine({L"--hwnd", L"12", L"--max-seconds", L"86400.1"},
                            working_directory)
               .ok(),
          "out-of-range max-seconds must be rejected");
  const auto cherry =
      ParseCommandLine({L"--hwnd", L"12", L"--mode", L"CHERRY"},
                       working_directory);
  Require(cherry.ok() && cherry.options->mode == Mode::Cherry,
          "CHERRY mode must parse");
  const auto automatic =
      ParseCommandLine({L"--hwnd", L"12", L"--mode", L"AUTO"},
                       working_directory);
  Require(automatic.ok() && automatic.options->mode == Mode::Auto,
          "AUTO mode must parse");
  Require(!ParseCommandLine({L"--hwnd", L"12", L"--mode", L"SR"},
                            working_directory)
               .ok(),
          "unsupported mode must be rejected");
  Require(!ParseCommandLine({L"--hwnd", L"12", L"--live-client", L"required"},
                            working_directory)
               .ok(),
          "unsupported Live Client mode must be rejected");
  Require(!ParseCommandLine({L"--replay", working_directory.wstring(),
                             L"--live-client", L"auto"},
                            working_directory)
               .ok(),
          "Live Client polling must be rejected for replay");
  Require(!ParseCommandLine({L"--hwnd", L"12", L"--lcu-context", L"required"},
                            working_directory)
               .ok(),
          "unsupported LCU context mode must be rejected");
  Require(!ParseCommandLine({L"--replay", working_directory.wstring(),
                             L"--lcu-context", L"auto"},
                            working_directory)
               .ok(),
          "LCU context polling must be rejected for replay");
  Require(!ParseCommandLine({L"--replay", working_directory.wstring(),
                             L"--lcu-context", L"off"},
                            working_directory)
               .ok(),
          "even disabled LCU context is a live-only replay option");
  Require(!ParseCommandLine({L"--hwnd", L"12", L"--completed-offers", L"5"},
                            working_directory)
               .ok(),
          "completed offer count above four must be rejected");
  Require(!ParseCommandLine({L"--replay", working_directory.wstring(),
                             L"--completed-offers", L"1"},
                            working_directory)
               .ok(),
          "completed offer seed must be rejected for replay");
  Require(!ParseCommandLine({L"--hwnd", L"12", L"--selected", L"middle"},
                            working_directory)
               .ok(),
          "unsupported selected value must be rejected");
  Require(!ParseCommandLine({L"--hwnd", L"12", L"--collect-samples",
                             L"--sample-hotkey", L"F7"},
                            working_directory)
               .ok(),
          "only the F8 sample hotkey must be accepted");
  Require(!ParseCommandLine({L"--hwnd", L"12", L"--collect-samples",
                             L"--sample-hotkey", L"f8"},
                            working_directory)
               .ok(),
          "the F8 spelling must be exact");
  Require(!ParseCommandLine({L"--hwnd", L"12", L"--collect-samples",
                             L"--sample-hotkey", L"F8", L"--no-hotkey"},
                            working_directory)
               .ok(),
          "sample-hotkey and no-hotkey must be mutually exclusive");
  Require(!ParseCommandLine(
               {L"--hwnd", L"12", L"--force-recognition-hotkey", L"F8"},
               working_directory)
               .ok(),
          "only the F9 force-recognition hotkey must be accepted");
  Require(!ParseCommandLine(
               {L"--hwnd", L"12", L"--force-recognition-hotkey", L"f9"},
               working_directory)
               .ok(),
          "the F9 spelling must be exact");
  Require(!ParseCommandLine({L"--hwnd", L"12", L"--force-recognition-hotkey",
                             L"F9", L"--no-force-recognition-hotkey"},
                            working_directory)
               .ok(),
          "force-recognition enable and disable must be mutually exclusive");
  Require(!ParseCommandLine({L"--replay", working_directory.wstring(),
                             L"--force-recognition-hotkey", L"F9"},
                            working_directory)
               .ok(),
          "explicit F9 must reject replay instead of becoming inert");
  Require(!ParseCommandLine({L"--replay", working_directory.wstring(),
                             L"--capture-backend", L"auto"},
                            working_directory)
               .ok(),
          "even an explicit default capture backend must reject replay");
  Require(!ParseCommandLine({L"--replay", working_directory.wstring(),
                             L"--capture-backend", L"desktop"},
                            working_directory)
               .ok(),
          "a live-only desktop backend must reject replay");
  Require(!ParseCommandLine({L"--hwnd", L"12", L"--capture-backend", L"gdi"},
                            working_directory)
               .ok(),
          "unknown capture backends must be rejected");
  Require(!ParseCommandLine(
               {L"--hwnd", L"12", L"--collect-suspect-confidence", L"0.5"},
               working_directory)
               .ok(),
          "collection tuning must not be silently inert");
  Require(!ParseCommandLine({L"--hwnd", L"12", L"--collect-samples",
                             L"--collect-suspect-confidence", L"1.01"},
                            working_directory)
               .ok(),
          "suspect confidence above one must be rejected");

  const auto missing_replay =
      working_directory / L"cli_test_definitely_missing.png";
  Require(!ParseCommandLine({L"--replay", missing_replay.wstring()},
                            working_directory)
               .ok(),
          "missing replay path must be rejected");

  const auto mutual = ParseCommandLine(
      {L"--replay", working_directory.wstring(), L"--hwnd", L"12"},
      working_directory);
  Require(!mutual.ok(), "source options must be mutually exclusive");

  const auto directory = ParseCommandLine(
      {L"--replay", working_directory.wstring()}, working_directory);
  Require(directory.ok() &&
              directory.options->replay_kind == ReplayKind::Directory,
          "existing replay directories must be detected automatically");
}

void TestPassiveHotkeyEdgesAndBurstLifecycle() {
  PassiveHotkeyEdge f8;
  Require(f8.Observe(true) && !f8.Observe(true),
          "F8 must emit one edge while held");
  Require(!f8.Observe(false) && f8.Observe(true),
          "F8 must re-arm only after release");

  PassiveHotkeyEdge f9;
  f9.Prime(true);
  Require(!f9.Observe(true) && !f9.Observe(false) && f9.Observe(true) &&
              !f9.Observe(true),
          "F9 startup hold and press edges must be deterministic");

  ForceRecognitionBurst burst;
  Require(!burst.active(), "force-recognition burst must start inactive");
  burst.Trigger();
  Require(burst.active() &&
              burst.remaining_frames() == kForceRecognitionBurstFrames,
          "F9 edge must start a bounded frame burst");
  burst.ObserveFrame(false);
  Require(burst.remaining_frames() == kForceRecognitionBurstFrames - 1U,
          "each incomplete frame must consume one burst slot");
  burst.Trigger();
  Require(burst.remaining_frames() == kForceRecognitionBurstFrames,
          "a new F9 edge must restart the bounded burst");
  burst.ObserveFrame(true);
  Require(!burst.active() && burst.remaining_frames() == 0U,
          "accepted or duplicate completion must end the burst immediately");
  burst.Trigger();
  for (std::uint32_t index = 0U; index < kForceRecognitionBurstFrames;
       ++index) {
    burst.ObserveFrame(false);
  }
  Require(!burst.active(), "frame budget exhaustion must end the burst");
}

void TestWindowSelection() {
  constexpr std::uintptr_t launcher_handle = 0x101U;
  constexpr std::uintptr_t game_handle = 0x202U;
  constexpr std::uintptr_t hidden_game_handle = 0x303U;
  constexpr std::uintptr_t unrelated_handle = 0x404U;
  const std::vector<WindowCandidate> windows{
      {launcher_handle, std::wstring{kLeagueLauncherWindowTitle}, true},
      {game_handle, std::wstring{kLeagueGameWindowTitle}, true},
      {hidden_game_handle, std::wstring{kLeagueGameWindowTitle}, false},
      {unrelated_handle, L"Unrelated", true}};

  const auto launcher_title =
      SelectWindowByTitle(kLeagueLauncherWindowTitle, windows);
  Require(!launcher_title.ok() && launcher_title.candidates.size() == 1U &&
              launcher_title.candidates.front().handle == launcher_handle &&
              launcher_title.error.find("Launcher") != std::string::npos,
          "the exact launcher title must be rejected with its candidate");

  const auto unsafe_substring =
      SelectWindowByTitle(L"League of Legends (TM)", windows);
  Require(
      !unsafe_substring.ok(),
      "a launcher-compatible title must never select the game by substring");
  const auto wrong_case =
      SelectWindowByTitle(L"league of legends (TM) Client", windows);
  Require(!wrong_case.ok() && wrong_case.candidates.empty(),
          "--window-title matching must be exact and case-sensitive");

  const auto game_title = SelectWindowByTitle(kLeagueGameWindowTitle, windows);
  Require(game_title.ok() && *game_title.handle == game_handle &&
              game_title.candidates.size() == 1U,
          "the unique visible exact game title must be accepted");

  const auto launcher_hwnd = SelectWindowByHandle(launcher_handle, windows);
  Require(!launcher_hwnd.ok() && launcher_hwnd.candidates.size() == 1U &&
              launcher_hwnd.error.find("launcher") != std::string::npos,
          "a direct launcher HWND must be rejected after title validation");
  const auto game_hwnd = SelectWindowByHandle(game_handle, windows);
  Require(game_hwnd.ok() && *game_hwnd.handle == game_handle,
          "a direct visible game HWND with the exact title must be accepted");

  const auto invalid = SelectWindowByHandle(0x505U, windows);
  Require(!invalid.ok() && invalid.candidates.empty() &&
              invalid.error.find("current visible") != std::string::npos,
          "an invalid HWND must fail with a clear current-window error");

  const auto ephemeral_initial = SelectWindowByHandle(game_handle, windows);
  const std::vector<WindowCandidate> after_close{
      {launcher_handle, std::wstring{kLeagueLauncherWindowTitle}, true},
      {unrelated_handle, L"Unrelated", true}};
  const auto ephemeral_confirmation =
      SelectWindowByHandle(game_handle, after_close);
  Require(ephemeral_initial.ok() && !ephemeral_confirmation.ok() &&
              ephemeral_confirmation.candidates.empty(),
          "a game HWND that disappears between snapshots must be rejected");

  auto ambiguous_windows = windows;
  constexpr std::uintptr_t second_game_handle = 0x606U;
  ambiguous_windows.push_back(
      {second_game_handle, std::wstring{kLeagueGameWindowTitle}, true});
  const auto ambiguous =
      SelectWindowByTitle(kLeagueGameWindowTitle, ambiguous_windows);
  Require(
      !ambiguous.ok() && ambiguous.candidates.size() == 2U &&
          ambiguous.candidates[0].handle == game_handle &&
          ambiguous.candidates[1].handle == second_game_handle,
      "multiple exact visible game titles must be rejected with candidates");
}

void TestRecognitionStopPolicy() {
  Require(lol_assistant::app::kStaticReplayPassCount == 5U,
          "single-image replay must provide five static analysis passes");

  RecognitionProgress progress;
  Require(!ShouldStopAfterRecognitionAttempt(true, progress),
          "--once must not stop before a recognition attempt");
  RecordRecognitionObservation(progress, true, false, false,
                               "awaiting_stability:2/3");
  Require(progress.stable_observation_seen &&
              !ShouldStopAfterRecognitionAttempt(true, progress),
          "a stable observation without OCR must not complete --once");
  RecordRecognitionObservation(progress, false, true, false,
                               "awaiting_ocr_consensus:1/2");
  Require(!ShouldStopAfterRecognitionAttempt(true, progress),
          "--once must not stop during an incomplete OCR consensus");

  RecognitionProgress unknown;
  RecordRecognitionObservation(unknown, true, true, false,
                               "recognition_unknown");
  Require(ShouldStopAfterRecognitionAttempt(true, unknown) &&
              !ShouldStopAfterRecognitionAttempt(false, unknown),
          "--once must stop after a completed unknown OCR attempt");
  Require(ResolveRecognitionSessionStatus(
              SessionStopReason::RecognitionAttemptCompleted, unknown) ==
              RecognitionSessionStatus::CompletedUnknown,
          "an unaccepted OCR attempt must complete as unknown");

  RecordRecognitionObservation(progress, false, true, true, "accepted_offer");
  Require(ShouldStopAfterRecognitionAttempt(true, progress),
          "--once must stop when OCR consensus accepts an offer");
  Require(ResolveRecognitionSessionStatus(
              SessionStopReason::RecognitionAttemptCompleted, progress) ==
              RecognitionSessionStatus::Completed,
          "an accepted offer must complete successfully");
}

void TestReplayCompletionStatusPolicy() {
  const RecognitionProgress empty;
  Require(ResolveRecognitionSessionStatus(SessionStopReason::InputExhausted,
                                          empty) ==
              RecognitionSessionStatus::CompletedNoStableObservation,
          "EOF without stable/OCR evidence must not report completed");
  Require(std::string{lol_assistant::app::ToString(
              RecognitionSessionStatus::CompletedNoStableObservation)} ==
              "completed_no_stable_observation",
          "no-stable status must have a stable JSON spelling");

  RecognitionProgress stable_only;
  RecordRecognitionObservation(stable_only, true, false, false,
                               "recognition_not_executed");
  Require(ResolveRecognitionSessionStatus(SessionStopReason::InputExhausted,
                                          stable_only) ==
              RecognitionSessionStatus::CompletedUnknown,
          "EOF after a stable but unrecognized observation must be unknown");
  Require(ResolveRecognitionSessionStatus(SessionStopReason::Timeout,
                                          stable_only) ==
              RecognitionSessionStatus::Timeout,
          "deadline expiry must remain a timeout");
}

} // namespace

int main() {
  try {
    const auto working_directory =
        std::filesystem::absolute(std::filesystem::current_path())
            .lexically_normal();
    TestCommands(working_directory);
    TestStrictParsing(working_directory);
    TestRejections(working_directory);
    TestWindowSelection();
    TestRecognitionStopPolicy();
    TestReplayCompletionStatusPolicy();
    TestPassiveHotkeyEdgesAndBurstLifecycle();
    TestHeadlessInputApiSourceGuard();
    std::cout << "cli_test passed\n";
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "cli_test failed: " << error.what() << '\n';
    return 1;
  }
}

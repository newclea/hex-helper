#include "cli.h"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cwctype>
#include <limits>
#include <string>
#include <system_error>
#include <unordered_set>

namespace lol_assistant::app {
namespace {

constexpr double kMaximumRunSeconds = 86'400.0;

[[nodiscard]] ParseResult Failure(std::string message) {
  return ParseResult{std::nullopt, std::move(message)};
}

[[nodiscard]] bool HasText(const std::wstring_view value) noexcept {
  return std::any_of(value.begin(), value.end(), [](const wchar_t character) {
    return std::iswspace(character) == 0;
  });
}

[[nodiscard]] std::filesystem::path
NormalizePath(const std::wstring_view value,
              const std::filesystem::path &current_directory) {
  std::filesystem::path path{value};
  const std::filesystem::path base =
      std::filesystem::absolute(current_directory).lexically_normal();
  if (!path.is_absolute()) {
    path = base / path;
  }
  return path.lexically_normal();
}

[[nodiscard]] std::wstring Lowercase(std::wstring value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](const wchar_t character) {
                   return static_cast<wchar_t>(std::towlower(character));
                 });
  return value;
}

[[nodiscard]] std::optional<std::string> Ascii(const std::wstring_view value) {
  std::string result;
  result.reserve(value.size());
  for (const wchar_t character : value) {
    if (character < 0 || character > 0x7F) {
      return std::nullopt;
    }
    result.push_back(static_cast<char>(character));
  }
  return result;
}

[[nodiscard]] std::optional<std::uintptr_t>
ParseWindowHandle(const std::wstring_view value) {
  const auto ascii = Ascii(value);
  if (!ascii.has_value() || ascii->empty() || ascii->front() == '+' ||
      ascii->front() == '-') {
    return std::nullopt;
  }

  std::string_view digits = *ascii;
  int base = 10;
  if (digits.size() > 2U && digits[0] == '0' &&
      (digits[1] == 'x' || digits[1] == 'X')) {
    base = 16;
    digits.remove_prefix(2U);
  }
  if (digits.empty()) {
    return std::nullopt;
  }

  std::uint64_t parsed = 0U;
  const auto result = std::from_chars(
      digits.data(), digits.data() + digits.size(), parsed, base);
  if (result.ec != std::errc{} || result.ptr != digits.data() + digits.size() ||
      parsed > static_cast<std::uint64_t>(
                   std::numeric_limits<std::uintptr_t>::max())) {
    return std::nullopt;
  }
  return static_cast<std::uintptr_t>(parsed);
}

[[nodiscard]] std::optional<double>
ParseRunSeconds(const std::wstring_view value) {
  const auto ascii = Ascii(value);
  if (!ascii.has_value() || ascii->empty()) {
    return std::nullopt;
  }
  double parsed = 0.0;
  const auto result =
      std::from_chars(ascii->data(), ascii->data() + ascii->size(), parsed,
                      std::chars_format::general);
  if (result.ec != std::errc{} || result.ptr != ascii->data() + ascii->size() ||
      !std::isfinite(parsed) || parsed <= 0.0 || parsed > kMaximumRunSeconds) {
    return std::nullopt;
  }
  return parsed;
}

[[nodiscard]] std::optional<std::uint32_t>
ParseCompletedOffers(const std::wstring_view value) {
  const auto ascii = Ascii(value);
  if (!ascii.has_value() || ascii->empty()) {
    return std::nullopt;
  }
  std::uint32_t parsed = 0U;
  const auto result =
      std::from_chars(ascii->data(), ascii->data() + ascii->size(), parsed, 10);
  if (result.ec != std::errc{} || result.ptr != ascii->data() + ascii->size() ||
      parsed > 4U) {
    return std::nullopt;
  }
  return parsed;
}

[[nodiscard]] std::optional<double>
ParseUnitConfidence(const std::wstring_view value) {
  const auto ascii = Ascii(value);
  if (!ascii.has_value() || ascii->empty()) {
    return std::nullopt;
  }
  double parsed = 0.0;
  const auto result =
      std::from_chars(ascii->data(), ascii->data() + ascii->size(), parsed,
                      std::chars_format::general);
  if (result.ec != std::errc{} || result.ptr != ascii->data() + ascii->size() ||
      !std::isfinite(parsed) || parsed < 0.0 || parsed > 1.0) {
    return std::nullopt;
  }
  return parsed;
}

} // namespace

ParseResult ParseCommandLine(const std::vector<std::wstring> &arguments,
                             const std::filesystem::path &current_directory) {
  try {
    ApplicationOptions options;
    options.knowledge_path =
        NormalizePath(L"data/knowledge/kiwi_augments.zh-CN.json", current_directory);
    options.workspace_path =
        NormalizePath(L"outputs/runtime", current_directory);
    options.dataset_root =
        NormalizePath(L"data/dataset/augment_offers", current_directory);

    if (arguments.size() == 1U) {
      if (arguments.front() == L"--help" || arguments.front() == L"-h") {
        options.command = Command::Help;
        return ParseResult{std::move(options), {}};
      }
      if (arguments.front() == L"--version") {
        options.command = Command::Version;
        return ParseResult{std::move(options), {}};
      }
      if (arguments.front() == L"--list-windows") {
        options.command = Command::ListWindows;
        return ParseResult{std::move(options), {}};
      }
      if (arguments.front() == L"--probe-live-client") {
        options.command = Command::ProbeLiveClient;
        return ParseResult{std::move(options), {}};
      }
    }

    std::unordered_set<std::wstring> seen;
    const auto mark_once = [&seen](const std::wstring &option) {
      return seen.insert(option).second;
    };
    const auto has_value = [&arguments](const std::size_t index) {
      return index + 1U < arguments.size() &&
             !arguments[index + 1U].starts_with(L"--");
    };

    for (std::size_t index = 0U; index < arguments.size(); ++index) {
      const std::wstring &argument = arguments[index];
      if (argument == L"--help" || argument == L"-h" ||
          argument == L"--version" || argument == L"--list-windows" ||
          argument == L"--probe-live-client") {
        return Failure(
            "Command options cannot be combined with other arguments");
      }

      if (argument == L"--preview" || argument == L"--once" ||
          argument == L"--collect-samples" || argument == L"--no-hotkey" ||
          argument == L"--no-force-recognition-hotkey") {
        if (!mark_once(argument)) {
          return Failure("Duplicate option");
        }
        if (argument == L"--preview") {
          options.preview = true;
        } else if (argument == L"--once") {
          options.once = true;
        } else if (argument == L"--collect-samples") {
          options.collect_samples = true;
        } else if (argument == L"--no-force-recognition-hotkey") {
          options.force_recognition_hotkey_enabled = false;
        } else {
          options.sample_hotkey_enabled = false;
        }
        continue;
      }

      if (argument != L"--replay" && argument != L"--hwnd" &&
          argument != L"--window-title" && argument != L"--champion" &&
          argument != L"--capture-backend" && argument != L"--live-client" &&
          argument != L"--lcu-context" && argument != L"--mode" &&
          argument != L"--knowledge" && argument != L"--workspace" &&
          argument != L"--max-seconds" && argument != L"--selected" &&
          argument != L"--dataset-root" && argument != L"--sample-hotkey" &&
          argument != L"--force-recognition-hotkey" &&
          argument != L"--completed-offers" &&
          argument != L"--collect-suspect-confidence") {
        return Failure("Unknown argument");
      }
      if (!mark_once(argument)) {
        return Failure("Duplicate option");
      }
      if (!has_value(index)) {
        return Failure("Missing value for option");
      }
      const std::wstring &value = arguments[++index];
      if (value.empty()) {
        return Failure("Option value must not be empty");
      }

      if (argument == L"--replay") {
        if (options.source_kind != SourceKind::None) {
          return Failure("Exactly one source option is allowed");
        }
        options.source_kind = SourceKind::Replay;
        options.replay_path = NormalizePath(value, current_directory);

        std::error_code error;
        if (std::filesystem::is_directory(options.replay_path, error) &&
            !error) {
          options.replay_kind = ReplayKind::Directory;
          continue;
        }
        error.clear();
        if (!std::filesystem::is_regular_file(options.replay_path, error) ||
            error) {
          return Failure(
              "Replay path does not exist or is not a regular file/directory");
        }
        const std::wstring extension =
            Lowercase(options.replay_path.extension().wstring());
        if (extension == L".png" || extension == L".jpg" ||
            extension == L".jpeg") {
          options.replay_kind = ReplayKind::Image;
        } else if (extension == L".jsonl" || extension == L".ndjson") {
          options.replay_kind = ReplayKind::ManifestJsonLines;
        } else {
          return Failure("Replay must be a PNG/JPEG image, image directory, or "
                         "JSONL manifest");
        }
      } else if (argument == L"--hwnd") {
        if (options.source_kind != SourceKind::None) {
          return Failure("Exactly one source option is allowed");
        }
        const auto handle = ParseWindowHandle(value);
        if (!handle.has_value()) {
          return Failure("HWND must be an in-range decimal or 0x-prefixed "
                         "hexadecimal integer");
        }
        options.source_kind = SourceKind::WindowHandle;
        options.window_handle = *handle;
      } else if (argument == L"--window-title") {
        if (options.source_kind != SourceKind::None) {
          return Failure("Exactly one source option is allowed");
        }
        if (!HasText(value)) {
          return Failure("Window title must contain visible text");
        }
        options.source_kind = SourceKind::WindowTitle;
        options.window_title = value;
      } else if (argument == L"--capture-backend") {
        options.capture_backend_explicit = true;
        if (value == L"auto") {
          options.capture_backend = CaptureBackend::Auto;
        } else if (value == L"wgc") {
          options.capture_backend = CaptureBackend::Wgc;
        } else if (value == L"desktop") {
          options.capture_backend = CaptureBackend::Desktop;
        } else {
          return Failure("capture-backend must be auto, wgc, or desktop");
        }
      } else if (argument == L"--live-client") {
        options.live_client_mode_explicit = true;
        if (value == L"auto") {
          options.live_client_mode = LiveClientMode::Auto;
        } else if (value == L"off") {
          options.live_client_mode = LiveClientMode::Off;
        } else {
          return Failure("live-client must be auto or off");
        }
      } else if (argument == L"--lcu-context") {
        options.lcu_context_mode_explicit = true;
        if (value == L"auto") {
          options.lcu_context_mode = LcuContextMode::Auto;
        } else if (value == L"off") {
          options.lcu_context_mode = LcuContextMode::Off;
        } else {
          return Failure("lcu-context must be auto or off");
        }
      } else if (argument == L"--champion") {
        if (!HasText(value)) {
          return Failure("Champion must contain visible text");
        }
        options.champion = value;
      } else if (argument == L"--mode") {
        if (value == L"KIWI") {
          options.mode = Mode::Kiwi;
        } else if (value == L"KIWI_JADE") {
          options.mode = Mode::KiwiJade;
        } else if (value == L"CHERRY") {
          options.mode = Mode::Cherry;
        } else if (value == L"AUTO") {
          options.mode = Mode::Auto;
        } else {
          return Failure("Mode must be KIWI, KIWI_JADE, CHERRY, or AUTO");
        }
      } else if (argument == L"--knowledge") {
        options.knowledge_path = NormalizePath(value, current_directory);
      } else if (argument == L"--workspace") {
        options.workspace_path = NormalizePath(value, current_directory);
      } else if (argument == L"--dataset-root") {
        options.dataset_root = NormalizePath(value, current_directory);
      } else if (argument == L"--sample-hotkey") {
        if (value != L"F8") {
          return Failure("sample-hotkey must be F8");
        }
        if (seen.contains(L"--no-hotkey")) {
          return Failure("sample-hotkey and no-hotkey are mutually exclusive");
        }
        options.sample_hotkey_enabled = true;
      } else if (argument == L"--force-recognition-hotkey") {
        if (value != L"F9") {
          return Failure("force-recognition-hotkey must be F9");
        }
        if (seen.contains(L"--no-force-recognition-hotkey")) {
          return Failure(
              "force-recognition-hotkey and no-force-recognition-hotkey are "
              "mutually exclusive");
        }
        options.force_recognition_hotkey_enabled = true;
      } else if (argument == L"--completed-offers") {
        const auto completed = ParseCompletedOffers(value);
        if (!completed.has_value()) {
          return Failure("completed-offers must be an integer from 0 to 4");
        }
        options.completed_offers = *completed;
      } else if (argument == L"--collect-suspect-confidence") {
        const auto confidence = ParseUnitConfidence(value);
        if (!confidence.has_value()) {
          return Failure(
              "collect-suspect-confidence must be between 0 and 1 inclusive");
        }
        options.collect_suspect_confidence = *confidence;
      } else if (argument == L"--max-seconds") {
        const auto seconds = ParseRunSeconds(value);
        if (!seconds.has_value()) {
          return Failure(
              "max-seconds must be greater than 0 and at most 86400");
        }
        options.max_seconds = *seconds;
      } else if (argument == L"--selected") {
        if (value == L"left") {
          options.selected = SelectedPosition::Left;
        } else if (value == L"center") {
          options.selected = SelectedPosition::Center;
        } else if (value == L"right") {
          options.selected = SelectedPosition::Right;
        } else {
          return Failure("selected must be left, center, or right");
        }
      }
    }

    if (options.source_kind == SourceKind::None) {
      return Failure(
          "Exactly one of --replay, --hwnd, or --window-title is required");
    }
    if (seen.contains(L"--sample-hotkey") && seen.contains(L"--no-hotkey")) {
      return Failure("sample-hotkey and no-hotkey are mutually exclusive");
    }
    if (seen.contains(L"--force-recognition-hotkey") &&
        seen.contains(L"--no-force-recognition-hotkey")) {
      return Failure(
          "force-recognition-hotkey and no-force-recognition-hotkey are "
          "mutually exclusive");
    }
    if (seen.contains(L"--force-recognition-hotkey") &&
        options.source_kind == SourceKind::Replay) {
      return Failure("force-recognition-hotkey requires a live source");
    }
    if (seen.contains(L"--capture-backend") &&
        options.source_kind == SourceKind::Replay) {
      return Failure("capture-backend requires a live source");
    }
    if (seen.contains(L"--live-client") &&
        options.source_kind == SourceKind::Replay) {
      return Failure("live-client requires a live source");
    }
    if (seen.contains(L"--lcu-context") &&
        options.source_kind == SourceKind::Replay) {
      return Failure("lcu-context requires a live source");
    }
    if (seen.contains(L"--completed-offers") &&
        options.source_kind == SourceKind::Replay) {
      return Failure("completed-offers requires a live source");
    }
    if (!options.collect_samples &&
        (seen.contains(L"--dataset-root") ||
         seen.contains(L"--sample-hotkey") ||
         seen.contains(L"--collect-suspect-confidence"))) {
      return Failure("Collection options require --collect-samples");
    }
    if (options.source_kind == SourceKind::Replay) {
      options.force_recognition_hotkey_enabled = false;
      options.live_client_mode = LiveClientMode::Off;
      options.lcu_context_mode = LcuContextMode::Off;
    }
    return ParseResult{std::move(options), {}};
  } catch (const std::exception &error) {
    return Failure(std::string{"Unable to normalize command line paths: "} +
                   error.what());
  }
}

bool PassiveHotkeyEdge::Observe(const bool down) noexcept {
  const bool pressed = down && !down_;
  down_ = down;
  return pressed;
}

void ForceRecognitionBurst::Trigger() noexcept {
  remaining_frames_ = kForceRecognitionBurstFrames;
}

void ForceRecognitionBurst::ObserveFrame(const bool completed) noexcept {
  if (!active()) {
    return;
  }
  if (completed) {
    remaining_frames_ = 0U;
    return;
  }
  --remaining_frames_;
}

WindowSelectionResult
SelectWindowByTitle(const std::wstring_view exact_title,
                    const std::vector<WindowCandidate> &windows) {
  WindowSelectionResult result;
  if (!HasText(exact_title)) {
    result.error = "Window title must contain visible text";
    return result;
  }

  for (const auto &window : windows) {
    if (window.visible && window.handle != 0U && window.title == exact_title) {
      result.candidates.push_back(window);
    }
  }

  if (exact_title == kLeagueLauncherWindowTitle) {
    result.error =
        "Launcher window is not a capture target; use the game window titled "
        "\"League of Legends (TM) Client\"";
    return result;
  }
  if (exact_title != kLeagueGameWindowTitle) {
    result.error =
        "Window title must exactly equal \"League of Legends (TM) Client\"";
    return result;
  }
  if (result.candidates.empty()) {
    result.error = "No visible game window has the exact title "
                   "\"League of Legends (TM) Client\"";
  } else if (result.candidates.size() != 1U) {
    result.error = "Multiple visible game windows have the exact target title";
  } else {
    result.handle = result.candidates.front().handle;
  }
  return result;
}

WindowSelectionResult
SelectWindowByHandle(const std::uintptr_t handle,
                     const std::vector<WindowCandidate> &windows) {
  WindowSelectionResult result;
  if (handle == 0U) {
    result.error = "HWND must not be null";
    return result;
  }

  const auto target = std::find_if(windows.begin(), windows.end(),
                                   [handle](const WindowCandidate &window) {
                                     return window.handle == handle;
                                   });
  if (target == windows.end()) {
    result.error =
        "HWND does not identify a current visible top-level window; the "
        "window may have closed";
    return result;
  }
  if (!target->visible) {
    result.error =
        "HWND identifies a window that is not visible or disappeared during "
        "selection";
    return result;
  }

  result.candidates.push_back(*target);
  if (target->title == kLeagueLauncherWindowTitle) {
    result.error = "HWND identifies the launcher, not the game window titled "
                   "\"League of Legends (TM) Client\"";
    return result;
  }
  if (target->title != kLeagueGameWindowTitle) {
    result.error =
        "HWND title must exactly equal \"League of Legends (TM) Client\"";
    return result;
  }

  result.handle = handle;
  return result;
}

void RecordRecognitionObservation(RecognitionProgress &progress,
                                  const bool stable_observation,
                                  const bool ocr_executed,
                                  const bool accepted_offer,
                                  const std::string_view reason) noexcept {
  progress.stable_observation_seen =
      progress.stable_observation_seen || stable_observation;
  progress.ocr_executed = progress.ocr_executed || ocr_executed;
  progress.accepted_offer = progress.accepted_offer || accepted_offer;
  const bool consensus_pending =
      reason.starts_with("awaiting_ocr_consensus:") ||
      reason == "ocr_consensus_changed";
  progress.recognition_attempt_completed =
      progress.recognition_attempt_completed || accepted_offer ||
      (ocr_executed && !consensus_pending);
}

bool ShouldStopAfterRecognitionAttempt(
    const bool once, const RecognitionProgress &progress) noexcept {
  return once &&
         (progress.recognition_attempt_completed || progress.accepted_offer);
}

RecognitionSessionStatus
ResolveRecognitionSessionStatus(const SessionStopReason stop_reason,
                                const RecognitionProgress &progress) noexcept {
  if (stop_reason == SessionStopReason::Timeout) {
    return RecognitionSessionStatus::Timeout;
  }
  if (progress.accepted_offer) {
    return RecognitionSessionStatus::Completed;
  }
  if (progress.ocr_executed || progress.stable_observation_seen) {
    return RecognitionSessionStatus::CompletedUnknown;
  }
  return RecognitionSessionStatus::CompletedNoStableObservation;
}

const char *ToString(const Mode mode) noexcept {
  switch (mode) {
  case Mode::Kiwi:
    return "KIWI";
  case Mode::KiwiJade:
    return "KIWI_JADE";
  case Mode::Cherry:
    return "CHERRY";
  case Mode::Auto:
    return "AUTO";
  default:
    return "UNKNOWN";
  }
}

const char *ToString(const ReplayKind replay_kind) noexcept {
  switch (replay_kind) {
  case ReplayKind::Image:
    return "image";
  case ReplayKind::Directory:
    return "directory";
  case ReplayKind::ManifestJsonLines:
    return "manifest_jsonl";
  default:
    return "unknown";
  }
}

const char *ToString(const CaptureBackend backend) noexcept {
  switch (backend) {
  case CaptureBackend::Auto:
    return "auto";
  case CaptureBackend::Wgc:
    return "wgc";
  case CaptureBackend::Desktop:
    return "desktop";
  default:
    return "auto";
  }
}

const char *ToString(const LiveClientMode mode) noexcept {
  switch (mode) {
  case LiveClientMode::Auto:
    return "auto";
  case LiveClientMode::Off:
    return "off";
  default:
    return "off";
  }
}

const char *ToString(const LcuContextMode mode) noexcept {
  switch (mode) {
  case LcuContextMode::Auto:
    return "auto";
  case LcuContextMode::Off:
    return "off";
  default:
    return "off";
  }
}

const char *ToString(const SelectedPosition selected) noexcept {
  switch (selected) {
  case SelectedPosition::Left:
    return "left";
  case SelectedPosition::Center:
    return "center";
  case SelectedPosition::Right:
    return "right";
  default:
    return "unknown";
  }
}

const char *ToString(const RecognitionSessionStatus status) noexcept {
  switch (status) {
  case RecognitionSessionStatus::Completed:
    return "completed";
  case RecognitionSessionStatus::CompletedUnknown:
    return "completed_unknown";
  case RecognitionSessionStatus::CompletedNoStableObservation:
    return "completed_no_stable_observation";
  case RecognitionSessionStatus::Timeout:
    return "timeout";
  default:
    return "timeout";
  }
}

} // namespace lol_assistant::app

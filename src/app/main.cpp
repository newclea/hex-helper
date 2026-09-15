#include <Windows.h>
#include <timeapi.h>
#pragma comment(lib, "winmm.lib")
#include <winrt/Windows.Data.Json.h>
#include <winrt/Windows.Foundation.Collections.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <cwctype>
#include <deque>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <limits>
#include <locale>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include "../output/json_support.h"
#include "../vision/selected_card_detector.h"
#include "augment_frame_processor.h"
#include "cli.h"
#include "health_json.h"
#include "live_capture_supervisor.h"
#include "live_control.h"
#include "lol_assistant/capture/desktop_duplication_source.h"
#include "lol_assistant/capture/window_info.h"
#include "lol_assistant/capture/windows_graphics_capture_source.h"
#include "lol_assistant/collection/sample_collector.h"
#include "lol_assistant/common/augment_observation.h"
#include "lol_assistant/common/frame.h"
#include "lol_assistant/detector/augment_screen_detector.h"
#include "lol_assistant/knowledge/augment_catalog.h"
#include "lol_assistant/lcu/lcu_context.h"
#include "lol_assistant/live_client/live_client_data.h"
#include "lol_assistant/output/debug_preview_window.h"
#include "lol_assistant/replay/image_replay_source.h"
#include "lol_assistant/vision/hover_click_selector.h"
#include "lol_assistant/vision/icon_matcher.h"
#include "lol_assistant/vision/ocr.h"
#include "lol_assistant/vision/text_matcher.h"
#include "mayhem_selection_scheduler.h"
#include "session_runtime.h"

namespace {

using lol_assistant::app::ApplicationOptions;
using lol_assistant::app::CaptureBackend;
using lol_assistant::app::LcuContextMode;
using lol_assistant::app::LiveClientMode;
using lol_assistant::app::ReplayKind;
using lol_assistant::app::SourceKind;
using lol_assistant::app::WindowCandidate;
using lol_assistant::capture::CaptureState;

constexpr int kSourceErrorExitCode = 3;
constexpr int kUsageErrorExitCode = 64;
constexpr auto kCaptureHealthInterval = std::chrono::milliseconds{1000};
constexpr auto kCaptureFreshTimeout = std::chrono::milliseconds{750};
constexpr auto kClickReplayFreshTimeout = std::chrono::seconds{10};
constexpr auto kDesktopStallRestartTimeout = std::chrono::seconds{3};
constexpr auto kLiveClientPollInterval = std::chrono::milliseconds{250};
constexpr auto kLiveClientUnchangedHeartbeat = std::chrono::seconds{30};
constexpr auto kSnapshotIdle = std::chrono::milliseconds{50};
constexpr auto kOfferOcrIdle = std::chrono::milliseconds{50};
constexpr auto kEnterRecognitionDelay = std::chrono::milliseconds{0};
constexpr std::array<std::string_view, lol_assistant::common::kAugmentCardCount>
    kOfferSlots{"LEFT", "CENTER", "RIGHT"};

struct FrameDiagnostic final {
  std::uint64_t id{0U};
  std::uint32_t width{0U};
  std::uint32_t height{0U};
  std::uint32_t stride{0U};
  std::size_t bytes{0U};
  bool valid{false};
};

[[nodiscard]] std::string WideToUtf8(const std::wstring_view input) {
  if (input.empty()) {
    return {};
  }
  if (input.size() >
      static_cast<std::size_t>(std::numeric_limits<int>::max())) {
    throw std::runtime_error("Wide string is too large to encode as UTF-8");
  }
  const int input_size = static_cast<int>(input.size());
  const int output_size =
      WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, input.data(),
                          input_size, nullptr, 0, nullptr, nullptr);
  if (output_size <= 0) {
    throw std::runtime_error("Unable to encode command-line text as UTF-8");
  }
  std::string output(static_cast<std::size_t>(output_size), '\0');
  if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, input.data(),
                          input_size, output.data(), output_size, nullptr,
                          nullptr) != output_size) {
    throw std::runtime_error("Unable to encode command-line text as UTF-8");
  }
  return output;
}

[[nodiscard]] std::string PathToUtf8(const std::filesystem::path &path) {
  return WideToUtf8(path.native());
}

[[nodiscard]] std::filesystem::path ExecutableDirectory() {
  std::vector<wchar_t> buffer(32768U, L'\0');
  const DWORD length = GetModuleFileNameW(nullptr, buffer.data(),
                                          static_cast<DWORD>(buffer.size()));
  if (length == 0U || length >= static_cast<DWORD>(buffer.size())) {
    throw std::runtime_error("Unable to resolve the executable path");
  }
  return std::filesystem::path{
      std::wstring_view{buffer.data(), static_cast<std::size_t>(length)}}
      .parent_path();
}

[[nodiscard]] std::optional<std::filesystem::path>
ResolveProductIconManifest() {
  constexpr std::wstring_view kRelativeManifest =
      L"data/knowledge/augment_icons/manifest.json";
  const std::array candidates{
      std::filesystem::absolute(ExecutableDirectory() / kRelativeManifest)
          .lexically_normal(),
      std::filesystem::absolute(ExecutableDirectory() / L".." /
                                kRelativeManifest)
          .lexically_normal(),
      std::filesystem::absolute(std::filesystem::current_path() /
                                kRelativeManifest)
          .lexically_normal()};
  for (const auto &candidate : candidates) {
    std::error_code error;
    if (std::filesystem::is_regular_file(candidate, error) && !error) {
      return candidate;
    }
  }
  return std::nullopt;
}

struct LoadedIconTemplates final {
  std::filesystem::path manifest_path{};
  std::vector<lol_assistant::vision::IconHashTemplate> templates{};
};

[[nodiscard]] LoadedIconTemplates LoadProductIconTemplates() {
  constexpr std::size_t kExpectedTemplateCount = 245U;
  const auto manifest_path = ResolveProductIconManifest();
  if (!manifest_path.has_value()) {
    return {};
  }
  std::ifstream input(*manifest_path, std::ios::binary);
  if (!input) {
    return {};
  }
  const std::string bytes{std::istreambuf_iterator<char>{input},
                          std::istreambuf_iterator<char>{}};
  const auto root =
      winrt::Windows::Data::Json::JsonObject::Parse(winrt::to_hstring(bytes));
  const auto summary = root.GetNamedObject(L"summary");
  const auto declared_count = static_cast<std::size_t>(
      summary.GetNamedNumber(L"imported_template_count"));
  const auto manifest_templates = root.GetNamedArray(L"templates");
  if (declared_count != kExpectedTemplateCount ||
      manifest_templates.Size() != kExpectedTemplateCount) {
    throw std::runtime_error(
        "Icon manifest must contain exactly 245 imported templates");
  }

  std::vector<lol_assistant::vision::IconHashTemplate> templates;
  templates.reserve(kExpectedTemplateCount);
  for (const auto &value : manifest_templates) {
    const auto object = value.GetObjectW();
    lol_assistant::vision::IconHashTemplate item;
    item.augment_id = winrt::to_string(object.GetNamedString(L"augment_id"));
    const std::string hash_text =
        winrt::to_string(object.GetNamedString(L"difference_hash"));
    if (hash_text.size() != 16U) {
      throw std::runtime_error("Icon manifest contains a malformed dHash");
    }
    const auto parsed =
        std::from_chars(hash_text.data(), hash_text.data() + hash_text.size(),
                        item.difference_hash, 16);
    if (parsed.ec != std::errc{} ||
        parsed.ptr != hash_text.data() + hash_text.size()) {
      throw std::runtime_error("Icon manifest contains a non-hex dHash");
    }
    for (const auto &mode : object.GetNamedArray(L"modes")) {
      item.modes.push_back(winrt::to_string(mode.GetString()));
    }
    templates.push_back(std::move(item));
  }
  return {*manifest_path, std::move(templates)};
}

void AppendJsonString(std::string &output, const std::string_view value) {
  constexpr char kHex[] = "0123456789abcdef";
  output.push_back('"');
  for (const unsigned char character : value) {
    switch (character) {
    case '"':
      output.append("\\\"");
      break;
    case '\\':
      output.append("\\\\");
      break;
    case '\b':
      output.append("\\b");
      break;
    case '\f':
      output.append("\\f");
      break;
    case '\n':
      output.append("\\n");
      break;
    case '\r':
      output.append("\\r");
      break;
    case '\t':
      output.append("\\t");
      break;
    default:
      if (character < 0x20U) {
        output.append("\\u00");
        output.push_back(kHex[(character >> 4U) & 0x0FU]);
        output.push_back(kHex[character & 0x0FU]);
      } else {
        output.push_back(static_cast<char>(character));
      }
      break;
    }
  }
  output.push_back('"');
}

void AppendJsonString(std::string &output, const std::wstring_view value) {
  AppendJsonString(output, WideToUtf8(value));
}

[[nodiscard]] std::string HandleText(const std::uintptr_t handle) {
  std::ostringstream stream;
  stream.imbue(std::locale::classic());
  stream << "0x" << std::hex << std::uppercase << handle;
  return stream.str();
}

[[nodiscard]] FrameDiagnostic
DescribeFrame(const lol_assistant::common::Frame &frame) noexcept {
  return FrameDiagnostic{frame.frame_id, frame.width,         frame.height,
                         frame.stride,   frame.buffer.size(), frame.IsValid()};
}

void AppendFrames(std::string &output,
                  const std::vector<FrameDiagnostic> &frames) {
  output.append("\"frames\":[");
  for (std::size_t index = 0U; index < frames.size(); ++index) {
    if (index != 0U) {
      output.push_back(',');
    }
    const auto &frame = frames[index];
    output.append("{\"id\":");
    output.append(std::to_string(frame.id));
    output.append(",\"width\":");
    output.append(std::to_string(frame.width));
    output.append(",\"height\":");
    output.append(std::to_string(frame.height));
    output.append(",\"stride\":");
    output.append(std::to_string(frame.stride));
    output.append(",\"bytes\":");
    output.append(std::to_string(frame.bytes));
    output.append(",\"valid\":");
    output.append(frame.valid ? "true}" : "false}");
  }
  output.push_back(']');
}

void AppendRunOptions(std::string &output, const ApplicationOptions &options) {
  output.append(",\"manual\":{\"champion\":");
  if (options.champion.empty()) {
    output.append("null");
  } else {
    AppendJsonString(output, options.champion);
  }
  output.append(",\"mode\":");
  AppendJsonString(output, lol_assistant::app::ToString(options.mode));
  output.append(",\"selected\":");
  if (options.selected.has_value()) {
    AppendJsonString(output, lol_assistant::app::ToString(*options.selected));
  } else {
    output.append("null");
  }
  output.append("},\"paths\":{\"knowledge\":");
  AppendJsonString(output, PathToUtf8(options.knowledge_path));
  output.append(",\"workspace\":");
  AppendJsonString(output, PathToUtf8(options.workspace_path));
  output.append(",\"dataset_root\":");
  AppendJsonString(output, PathToUtf8(options.dataset_root));
  output.append("},\"preview_requested\":");
  output.append(options.preview ? "true" : "false");
  output.append(",\"collection\":{\"requested\":");
  output.append(options.collect_samples ? "true" : "false");
  output.append(",\"sample_hotkey\":");
  if (options.collect_samples && options.sample_hotkey_enabled) {
    AppendJsonString(output, "F8");
  } else {
    output.append("null");
  }
  output.append(",\"suspect_confidence\":");
  if (options.collect_suspect_confidence.has_value()) {
    output.append(std::to_string(*options.collect_suspect_confidence));
  } else {
    output.append("null");
  }
  output.push_back('}');
  const bool force_recognition_enabled =
      options.force_recognition_hotkey_enabled &&
      options.source_kind != SourceKind::Replay;
  output.append(",\"force_recognition\":{\"enabled\":");
  output.append(force_recognition_enabled ? "true" : "false");
  output.append(",\"hotkey\":");
  if (force_recognition_enabled) {
    AppendJsonString(output, "F9");
  } else {
    output.append("null");
  }
  output.append(",\"burst_frames\":");
  output.append(
      std::to_string(lol_assistant::app::kForceRecognitionBurstFrames));
  output.append(",\"burst_milliseconds\":");
  output.append(
      std::to_string(lol_assistant::app::kForceRecognitionBurstMilliseconds));
  output.push_back('}');
  if (options.source_kind != SourceKind::Replay) {
    output.append(",\"capture_backend\":");
    AppendJsonString(output,
                     lol_assistant::app::ToString(options.capture_backend));
  }
  output.append(",\"live_client\":{\"mode\":");
  AppendJsonString(output,
                   lol_assistant::app::ToString(options.live_client_mode));
  output.append(",\"endpoint\":\"https://127.0.0.1:2999\"");
  output.append(",\"completed_offers\":");
  output.append(std::to_string(options.completed_offers));
  output.push_back('}');
  output.append(",\"lcu_context\":{\"mode\":");
  AppendJsonString(output,
                   lol_assistant::app::ToString(options.lcu_context_mode));
  output.push_back('}');
  output.append(",\"static_replay\":");
  output.append(options.source_kind == SourceKind::Replay &&
                        options.replay_kind == ReplayKind::Image
                    ? "true"
                    : "false");
}

[[nodiscard]] std::string SerializeMayhemSelectionEvent(
    const lol_assistant::app::MayhemSelectionEvent &event,
    const std::string_view session_id) {
  const auto &snapshot = event.snapshot;
  std::string output{
      "{\"type\":\"mayhem_selection_state\",\"schema_version\":1,"};
  output.append("\"session_id\":");
  AppendJsonString(output, session_id);
  output.append(",\"observed_at_utc\":");
  AppendJsonString(output,
                   lol_assistant::output::detail::FormatUtcTimestamp(
                       lol_assistant::common::UtcTimestamp::clock::now()));
  output.append(",\"status\":");
  AppendJsonString(output, lol_assistant::app::ToString(event.kind));
  output.append(",\"phase\":");
  AppendJsonString(output, lol_assistant::app::ToString(snapshot.phase));
  output.append(",\"reason\":");
  AppendJsonString(output, event.reason);
  output.append(",\"stage\":");
  output.append(std::to_string(event.stage));
  output.append(",\"thresholdLevel\":");
  if (event.threshold_level.has_value()) {
    output.append(std::to_string(*event.threshold_level));
  } else {
    output.append("null");
  }
  output.append(",\"completedOffers\":");
  output.append(std::to_string(snapshot.completed_offers));
  output.append(",\"pendingCount\":");
  output.append(std::to_string(snapshot.pending_count));
  output.append(",\"highFrequencyActive\":");
  output.append(snapshot.high_frequency_active ? "true" : "false");
  output.append(",\"player\":");
  if (!event.player.has_value()) {
    output.append("null");
  } else {
    const auto &player = *event.player;
    output.append("{\"championName\":");
    AppendJsonString(output, player.champion_name);
    output.append(",\"level\":");
    output.append(std::to_string(player.level));
    output.append(",\"isDead\":");
    output.append(player.is_dead ? "true" : "false");
    output.append(",\"respawnTimer\":");
    output.append(std::to_string(player.respawn_timer_seconds));
    output.append(",\"currentHealth\":");
    output.append(player.current_health.has_value()
                      ? std::to_string(*player.current_health)
                      : "null");
    output.append(",\"maxHealth\":");
    output.append(player.max_health.has_value()
                      ? std::to_string(*player.max_health)
                      : "null");
    output.append(",\"healthPercent\":");
    output.append(player.health_percent.has_value()
                      ? std::to_string(*player.health_percent)
                      : "null");
    output.push_back('}');
  }
  output.push_back('}');
  return output;
}

[[nodiscard]] std::string
ErrorJson(const std::string_view type, const std::string_view error,
          const std::vector<WindowCandidate> &candidates = {},
          const bool include_candidates = false) {
  std::string output{"{\"type\":"};
  AppendJsonString(output, type);
  output.append(",\"error\":");
  AppendJsonString(output, error);
  if (include_candidates || !candidates.empty()) {
    output.append(",\"candidates\":[");
    for (std::size_t index = 0U; index < candidates.size(); ++index) {
      if (index != 0U) {
        output.push_back(',');
      }
      output.append("{\"hwnd\":");
      AppendJsonString(output, HandleText(candidates[index].handle));
      output.append(",\"title\":");
      AppendJsonString(output, candidates[index].title);
      output.push_back('}');
    }
    output.push_back(']');
  }
  output.push_back('}');
  return output;
}

void PrintHelp() {
  std::cout
      << "lol_augment_assistant - augment recognition and live state "
         "pipeline\n\n"
      << "Usage:\n"
      << "  lol_augment_assistant --help | --version | --list-windows\n"
      << "  lol_augment_assistant --probe-live-client\n"
      << "  lol_augment_assistant SOURCE [options]\n\n"
      << "Source (exactly one):\n"
      << "  --replay <path>             PNG/JPEG, image directory, or JSONL "
         "manifest\n"
      << "  --hwnd <decimal|0xhex>      Visible game HWND; exact title is "
         "validated\n"
      << "  --window-title <exact>      Must be League of Legends (TM) "
         "Client\n\n"
      << "Options:\n"
      << "  --capture-backend <auto|wgc|desktop>\n"
      << "                              Live only; default: auto\n"
      << "  --live-client <auto|off>   Live Client Data polling; default: "
         "auto\n"
      << "  --lcu-context <auto|off>   Optional LCU gameflow context; default: "
         "auto\n"
      << "  --completed-offers <0..4> Resume Mayhem timing after confirmed "
         "offers\n"
      << "  --champion <name>           Manual champion context\n"
      << "  --mode <KIWI|KIWI_JADE|CHERRY|AUTO>\n"
      << "                              Default: KIWI; AUTO/CHERRY include "
         "Arena names\n"
      << "  --knowledge <path>          Augment catalog JSON path\n"
      << "  --workspace <path>          Runtime root; default: "
         "outputs/runtime\n"
      << "  --collect-samples          Save raw detector-suspect/manual "
         "samples\n"
      << "  --dataset-root <path>      Default: data/dataset/augment_offers\n"
      << "  --sample-hotkey F8         Only F8 is supported in Phase2\n"
      << "  --no-hotkey                Disable manual F8 sampling\n"
      << "  --force-recognition-hotkey F9\n"
      << "                              Passive live F9 burst (default "
         "enabled)\n"
      << "  --no-force-recognition-hotkey\n"
      << "                              Disable the live F9 recognition burst\n"
      << "  --collect-suspect-confidence <0..1>\n"
      << "  --preview                   Show the debug preview window\n"
      << "  --max-seconds <seconds>     Range: (0, 86400], default: 30\n"
      << "  --once                      Stop after the first source frame\n"
      << "  --selected <left|center|right>\n";
}

[[nodiscard]] int ListWindows() {
  const auto windows = lol_assistant::capture::EnumerateTopLevelWindows();
  std::string output{"{\"type\":\"window_list\",\"windows\":["};
  for (std::size_t index = 0U; index < windows.size(); ++index) {
    if (index != 0U) {
      output.push_back(',');
    }
    const auto handle = reinterpret_cast<std::uintptr_t>(windows[index].handle);
    output.append("{\"hwnd\":");
    AppendJsonString(output, HandleText(handle));
    output.append(",\"title\":");
    AppendJsonString(output, windows[index].title);
    output.append(",\"process_id\":");
    output.append(std::to_string(windows[index].process_id));
    output.push_back('}');
  }
  output.append("]}");
  std::cout << output << '\n';
  return 0;
}

[[nodiscard]] int ProbeLiveClient() {
  lol_assistant::live_client::LiveClientEvent event;
  event.sequence = 1U;
  bool apartment_initialized = false;
  try {
    winrt::init_apartment(winrt::apartment_type::multi_threaded);
    apartment_initialized = true;
    lol_assistant::live_client::LiveClientReader reader{std::make_unique<
        lol_assistant::live_client::WinHttpLiveClientTransport>()};
    event.snapshot = reader.Read();
  } catch (...) {
    event.snapshot.status =
        lol_assistant::live_client::LiveClientStatus::InvalidResponse;
    event.snapshot.reason = "probe_exception";
    event.snapshot.observed_at = std::chrono::system_clock::now();
  }
  if (apartment_initialized) {
    winrt::uninit_apartment();
  }
  std::cout << lol_assistant::live_client::SerializeLiveClientEventJson(event,
                                                                        "probe")
            << '\n';
  return event.snapshot.status ==
                 lol_assistant::live_client::LiveClientStatus::Ready
             ? 0
             : kSourceErrorExitCode;
}

[[nodiscard]] lol_assistant::replay::ReplayInputKind
ReplayInputKind(const ReplayKind kind) {
  switch (kind) {
  case ReplayKind::Image:
    return lol_assistant::replay::ReplayInputKind::SingleFile;
  case ReplayKind::Directory:
    return lol_assistant::replay::ReplayInputKind::Directory;
  case ReplayKind::ManifestJsonLines:
    return lol_assistant::replay::ReplayInputKind::ManifestJsonLines;
  default:
    throw std::invalid_argument("Unknown replay input kind");
  }
}

[[nodiscard]] std::optional<std::string>
ManualChampion(const ApplicationOptions &options) {
  if (options.champion.empty()) {
    return std::nullopt;
  }
  return WideToUtf8(options.champion);
}

void ApplyRunnerCaptureBackend(ApplicationOptions &options) {
  if (options.source_kind == SourceKind::Replay ||
      options.capture_backend_explicit) {
    return;
  }
  constexpr wchar_t kVariable[] = L"LOL_ASSISTANT_CAPTURE_BACKEND";
  std::array<wchar_t, 16U> value{};
  const DWORD length = GetEnvironmentVariableW(
      kVariable, value.data(), static_cast<DWORD>(value.size()));
  if (length == 0U) {
    return;
  }
  if (static_cast<std::size_t>(length) >= value.size()) {
    throw std::runtime_error("LOL_ASSISTANT_CAPTURE_BACKEND is too long");
  }
  const std::wstring_view backend{value.data(),
                                  static_cast<std::size_t>(length)};
  if (backend == L"auto") {
    options.capture_backend = CaptureBackend::Auto;
  } else if (backend == L"wgc") {
    options.capture_backend = CaptureBackend::Wgc;
  } else if (backend == L"desktop") {
    options.capture_backend = CaptureBackend::Desktop;
  } else {
    throw std::runtime_error(
        "LOL_ASSISTANT_CAPTURE_BACKEND must be auto, wgc, or desktop");
  }
}

[[nodiscard]] std::optional<std::size_t>
SelectedSlot(const ApplicationOptions &options) noexcept {
  if (!options.selected.has_value()) {
    return std::nullopt;
  }
  switch (*options.selected) {
  case lol_assistant::app::SelectedPosition::Left:
    return 0U;
  case lol_assistant::app::SelectedPosition::Center:
    return 1U;
  case lol_assistant::app::SelectedPosition::Right:
    return 2U;
  default:
    return std::nullopt;
  }
}

[[nodiscard]] lol_assistant::detector::AugmentScreenDetectorConfig
ProductDetectorConfig(const bool arena_tolerant = false) {
  using lol_assistant::common::NormalizedRoi;
  lol_assistant::detector::AugmentScreenDetectorConfig config;
  config.layout.offer_region = NormalizedRoi{0.10, 0.10, 0.80, 0.80};
  // Current Mayhem 3-pick-1: hug the gold card frame (red) for presence,
  // name plate (blue) for OCR. Measured on a 16:9 3840x2160 live shot.
  config.layout.card_regions = {
      NormalizedRoi{0.226, 0.175, 0.171, 0.478},
      NormalizedRoi{0.418, 0.175, 0.171, 0.478},
      NormalizedRoi{0.610, 0.175, 0.171, 0.478},
  };
  config.layout.title_region_within_card =
      NormalizedRoi{0.10, 0.36, 0.80, 0.22};
  config.layout.icon_region_within_card =
      NormalizedRoi{0.28, 0.08, 0.44, 0.30};
  if (arena_tolerant) {
    config.aspect_ratio_tolerance = 0.08;
    config.roi_search.maximum_vertical_offset_ratio = 0.040;
    // Mayhem cards are nearly black; the old 0.16 luma floor never fires OCR.
    config.minimum_card_luma = 0.018F;
    config.minimum_edge_density = 0.003F;
    config.minimum_surround_luma_contrast = 0.005F;
    config.minimum_bright_border_density = 0.06F;
    config.minimum_border_luma_contrast = 0.03F;
    config.maximum_luma_spread = 0.40F;
    config.maximum_edge_spread = 0.40F;
    config.maximum_bright_border_density_spread = 0.55F;
    config.minimum_three_column_consistency = 0.28F;
    config.visible_confidence_threshold = 0.28F;
  }
  return config;
}

void AppendDetectorResult(
    std::string &output,
    const lol_assistant::detector::DetectorResult &detector) {
  output.append("{\"frame_id\":");
  output.append(std::to_string(detector.frame_id));
  output.append(",\"visible\":");
  output.append(detector.visible ? "true" : "false");
  output.append(",\"confidence\":");
  output.append(std::to_string(detector.confidence));
  output.append(",\"reason\":");
  AppendJsonString(output, detector.reason);
  output.push_back('}');
}

void AppendPixelRoi(std::string &output,
                    const lol_assistant::detector::PixelRoi &roi) {
  output.append("{\"x\":");
  output.append(std::to_string(roi.x));
  output.append(",\"y\":");
  output.append(std::to_string(roi.y));
  output.append(",\"width\":");
  output.append(std::to_string(roi.width));
  output.append(",\"height\":");
  output.append(std::to_string(roi.height));
  output.push_back('}');
}

void AppendThreeCardRois(
    std::string &output,
    const std::optional<lol_assistant::detector::ThreeCardRois> &rois) {
  if (!rois.has_value()) {
    output.append("null");
    return;
  }
  output.append("{\"offer\":");
  AppendPixelRoi(output, rois->offer_region);
  output.append(",\"cards\":[");
  for (std::size_t index = 0U; index < rois->cards.size(); ++index) {
    if (index != 0U) {
      output.push_back(',');
    }
    AppendPixelRoi(output, rois->cards[index]);
  }
  output.append("]}");
}

[[nodiscard]] const char *CardRecognitionStateText(
    const lol_assistant::vision::CardRecognitionState state) noexcept {
  using lol_assistant::vision::CardRecognitionState;
  switch (state) {
  case CardRecognitionState::SkippedDetectorUnstable:
    return "SKIPPED_DETECTOR_UNSTABLE";
  case CardRecognitionState::BackendUnavailable:
    return "BACKEND_UNAVAILABLE";
  case CardRecognitionState::OcrFailed:
    return "OCR_FAILED";
  case CardRecognitionState::Unknown:
    return "UNKNOWN";
  case CardRecognitionState::Recognized:
    return "RECOGNIZED";
  default:
    return "UNKNOWN";
  }
}

[[nodiscard]] const char *
TextMatchKindText(const lol_assistant::vision::TextMatchKind kind) noexcept {
  using lol_assistant::vision::TextMatchKind;
  switch (kind) {
  case TextMatchKind::Exact:
    return "EXACT";
  case TextMatchKind::Normalized:
    return "NORMALIZED";
  case TextMatchKind::Fuzzy:
    return "FUZZY";
  case TextMatchKind::Unknown:
  default:
    return "UNKNOWN";
  }
}

void AppendRecognitionDebug(
    std::string &output,
    const std::optional<lol_assistant::vision::OfferRecognitionOutput>
        &recognition) {
  if (!recognition.has_value()) {
    output.append("null");
    return;
  }
  output.append("{\"reason\":");
  AppendJsonString(output, recognition->reason);
  output.append(",\"cards\":[");
  constexpr std::array<std::string_view, 3U> slots{"LEFT", "CENTER", "RIGHT"};
  for (std::size_t index = 0U; index < recognition->cards.size(); ++index) {
    if (index != 0U) {
      output.push_back(',');
    }
    const auto &card = recognition->cards[index];
    output.append("{\"slot\":");
    AppendJsonString(output, slots[index]);
    output.append(",\"state\":");
    AppendJsonString(output, CardRecognitionStateText(card.state));
    output.append(",\"raw_text\":");
    AppendJsonString(output, card.raw_text);
    output.append(",\"backend\":");
    AppendJsonString(output, card.backend);
    output.append(",\"normalized_text\":");
    AppendJsonString(output, card.normalized_text);
    output.append(",\"match_kind\":");
    AppendJsonString(output, TextMatchKindText(card.match_kind));
    output.append(",\"match_confidence\":");
    output.append(std::to_string(card.match_confidence));
    output.append(",\"match_top2_score\":");
    output.append(std::to_string(card.match_top2_score));
    output.append(",\"match_margin\":");
    output.append(std::to_string(card.match_margin));
    output.append(",\"final_confidence\":");
    output.append(std::to_string(card.final_confidence));
    output.append(",\"augment_id\":");
    if (card.augment_id.has_value()) {
      AppendJsonString(output, *card.augment_id);
    } else {
      output.append("null");
    }
    output.append(",\"display_name\":");
    if (card.display_name.has_value()) {
      AppendJsonString(output, *card.display_name);
    } else {
      output.append("null");
    }
    output.append(",\"reason\":");
    AppendJsonString(output, card.reason);
    output.append(",\"icon\":{\"augment_id\":");
    if (card.icon_match.augment_id.has_value()) {
      AppendJsonString(output, *card.icon_match.augment_id);
    } else {
      output.append("null");
    }
    output.append(",\"top1_score\":");
    if (card.icon_match.top1_score.has_value()) {
      output.append(std::to_string(*card.icon_match.top1_score));
    } else {
      output.append("null");
    }
    output.append(",\"margin\":");
    if (card.icon_match.margin.has_value()) {
      output.append(std::to_string(*card.icon_match.margin));
    } else {
      output.append("null");
    }
    output.append(",\"reason\":");
    AppendJsonString(output, card.icon_match.reason);
    output.append("}}");
  }
  output.append("]}");
}

struct CollectionCounters final {
  std::uint64_t saved{0U};
  std::uint64_t duplicates{0U};
  std::uint64_t errors{0U};
  std::uint64_t manual{0U};
  std::uint64_t automatic{0U};
};

struct LiveFrameContext final {
  std::string source{};
  std::uint64_t epoch{0U};
  std::string transport_content_hash{};
  std::chrono::steady_clock::time_point observed_at{};
};

struct CollectionContext final {
  bool requested{false};
  bool sample_hotkey_enabled{false};
  lol_assistant::app::PassiveHotkeyEdge f8_edge{};
  lol_assistant::collection::SampleKind sample_kind{
      lol_assistant::collection::SampleKind::Unknown};
  std::string provenance{"unknown"};
  lol_assistant::collection::WindowMetadata window{};
  std::optional<double> suspect_confidence{};
  std::unique_ptr<lol_assistant::collection::SampleCollector> collector{};
  std::optional<lol_assistant::common::Frame> latest_frame{};
  std::optional<lol_assistant::app::FrameProcessResult> latest_result{};
  std::optional<LiveFrameContext> latest_live_context{};
  std::optional<lol_assistant::detector::ThreeCardRois> current_rois{};
  std::optional<std::chrono::steady_clock::time_point> last_auto_attempt{};
  std::uint64_t manual_request_id{0U};
  CollectionCounters counters{};
};

struct ForceRecognitionContext final {
  bool enabled{false};
  std::unique_ptr<lol_assistant::app::ForceRecognitionController> controller{};
  std::optional<std::uint64_t> sample_pending_request_id{};
};

struct PendingHudSelection final {
  std::uint32_t stage{1U};
  std::array<std::string, lol_assistant::common::kAugmentCardCount>
      offer_augment_ids{};
  std::unique_ptr<lol_assistant::vision::SelectedCardDetector> detector{};
  lol_assistant::common::Frame empty_slot_baseline{};
  std::optional<std::string> stable_candidate_id{};
  std::optional<std::size_t> stable_offer_slot{};
  std::optional<std::uint64_t> last_frame_id{};
  std::uint32_t stable_frames{0U};
  float minimum_top1_score{1.0F};
  float minimum_top1_margin{1.0F};
};

struct Phase1Pipeline final {
  lol_assistant::vision::WindowsMediaOcrTitleRecognizer ocr{};
  std::unique_ptr<lol_assistant::app::Phase1SessionRuntime> runtime{};
  std::unique_ptr<lol_assistant::app::AugmentFrameProcessor> processor{};
  std::unique_ptr<lol_assistant::output::DebugPreviewWindow> preview{};
  std::optional<std::string> previous_reason{};
  lol_assistant::app::RecognitionProgress recognition_progress{};
  CollectionContext collection{};
  ForceRecognitionContext force_recognition{};
  std::unique_ptr<lol_assistant::app::BoundedSamplePolicy> sample_policy{};
  lol_assistant::app::FlushedJsonLineWriter *control_writer{nullptr};
  std::optional<std::string> active_live_source{};
  bool control_state_dirty{false};
  std::size_t icon_template_count{0U};
  std::filesystem::path icon_manifest_path{};
  std::vector<lol_assistant::vision::IconHashTemplate> hud_icon_templates{};
  std::optional<std::string> icon_mode{};
  std::deque<PendingHudSelection> pending_hud_selections{};
  lol_assistant::app::PassiveHotkeyEdge click_edge{};
  lol_assistant::vision::HoverClickSelector hover_click{
      [] {
        lol_assistant::vision::HoverClickConfig config;
        config.stable_frames = 1U;
        config.grace_frames = 1U;
        return config;
      }()};
  lol_assistant::vision::CardPickTracker pick_tracker{};
  std::optional<lol_assistant::detector::ThreeCardRois> pick_rois{};
  std::array<float, lol_assistant::common::kAugmentCardCount> card_luma{};
  std::array<float, lol_assistant::common::kAugmentCardCount> card_inner_luma{};
  std::array<float, lol_assistant::common::kAugmentCardCount>
      card_background_likeness{};
  bool card_luma_valid{false};
  bool card_presence_valid{false};
  std::array<std::string, lol_assistant::common::kAugmentCardCount>
      click_offer_ids{};
  std::array<std::string, lol_assistant::common::kAugmentCardCount>
      click_offer_names{};
  bool click_offer_valid{false};
  std::optional<std::size_t> ocr_pick_slot{};
  std::uint32_t ocr_pick_frames{0U};
  bool click_armed{false};
  bool snapshot_pending{false};
  std::string snapshot_cause{};
  bool enter_snapshot_done{false};
  bool stop_after_pick{false};
  std::uint64_t frames_processed{0U};
  std::uint32_t accepted_offer_count{0U};
};

struct StaticReplayDiagnostic final {
  std::uint64_t source_frame_id{0U};
  std::size_t pass_index{0U};
  std::size_t pass_count{0U};
};

[[nodiscard]] const char *CollectStatusText(
    const lol_assistant::collection::CollectStatus status) noexcept {
  using lol_assistant::collection::CollectStatus;
  switch (status) {
  case CollectStatus::Saved:
    return "saved";
  case CollectStatus::Duplicate:
    return "duplicate";
  case CollectStatus::InvalidRequest:
    return "invalid_request";
  case CollectStatus::IoError:
    return "io_error";
  case CollectStatus::SerializationError:
    return "serialization_error";
  default:
    return "unknown_error";
  }
}

void PrintCollectionEvent(
    const CollectionContext &context, const std::string_view status,
    const std::string_view trigger, const std::optional<std::uint64_t> frame_id,
    const std::string_view message,
    const std::filesystem::path &sample_directory = {},
    const std::optional<std::string_view> error_kind = std::nullopt) {
  std::string output{"{\"type\":\"sample_collection\",\"status\":"};
  AppendJsonString(output, status);
  output.append(",\"trigger\":");
  AppendJsonString(output, trigger);
  output.append(",\"provenance\":");
  AppendJsonString(output, context.provenance);
  output.append(",\"frame_id\":");
  if (frame_id.has_value()) {
    output.append(std::to_string(*frame_id));
  } else {
    output.append("null");
  }
  output.append(",\"sample_directory\":");
  if (sample_directory.empty()) {
    output.append("null");
  } else {
    AppendJsonString(output, PathToUtf8(sample_directory));
  }
  if (error_kind.has_value()) {
    output.append(",\"error_kind\":");
    AppendJsonString(output, *error_kind);
  }
  output.append(",\"message\":");
  AppendJsonString(output, message);
  output.push_back('}');
  std::cout << output << '\n';
}

void EmitForceRecognitionEvents(
    Phase1Pipeline &pipeline,
    const std::vector<lol_assistant::app::ForceRecognitionEvent> &events) {
  for (const auto &event : events) {
    if (event.kind == lol_assistant::app::ForceEventKind::Started) {
      pipeline.force_recognition.sample_pending_request_id = event.request_id;
    } else if (pipeline.force_recognition.sample_pending_request_id ==
               event.request_id) {
      pipeline.force_recognition.sample_pending_request_id.reset();
    }
    pipeline.control_state_dirty = true;
    if (pipeline.control_writer != nullptr) {
      const bool written = pipeline.control_writer->Write(
          lol_assistant::app::SerializeForceRecognitionEventJson(event));
      if (!written) {
        throw std::runtime_error(
            "Unable to flush force-recognition JSONL event");
      }
    }
  }
}

void PrintCollectionError(CollectionContext &context,
                          const std::string_view trigger,
                          const std::optional<std::uint64_t> frame_id,
                          const std::string_view error_kind,
                          const std::string_view message) noexcept {
  ++context.counters.errors;
  try {
    PrintCollectionEvent(context, "error", trigger, frame_id, message, {},
                         error_kind);
  } catch (...) {
    std::cout << "{\"type\":\"sample_collection\",\"status\":\"error\","
                 "\"trigger\":\"internal\",\"provenance\":\"unknown\","
                 "\"frame_id\":null,\"sample_directory\":null,"
                 "\"error_kind\":\"json_encoding_error\","
                 "\"message\":\"unable to encode collection error\"}\n";
  }
}

[[nodiscard]] lol_assistant::detector::PixelRoi
FullCropRoi(lol_assistant::detector::PixelRoi roi) noexcept {
  roi.primary_ocr_rect.reset();
  return roi;
}

[[nodiscard]] std::vector<std::string>
SplitOcrLines(const std::string_view text) {
  std::vector<std::string> lines;
  std::size_t start = 0U;
  while (start <= text.size() &&
         lines.size() < lol_assistant::vision::kMaximumOcrLineCandidates) {
    const std::size_t newline = text.find('\n', start);
    const std::size_t end =
        newline == std::string_view::npos ? text.size() : newline;
    std::string_view line = text.substr(start, end - start);
    if (!line.empty() &&
        line.size() <= lol_assistant::vision::kMaximumOcrLineCandidateBytes) {
      lines.emplace_back(line);
    }
    if (newline == std::string_view::npos) {
      break;
    }
    start = newline + 1U;
  }
  return lines;
}

[[nodiscard]] lol_assistant::collection::OcrSampleStatus ToOcrSampleStatus(
    const lol_assistant::vision::CardRecognitionOutput &card) noexcept {
  using lol_assistant::collection::OcrSampleStatus;
  using lol_assistant::vision::CardRecognitionState;
  switch (card.state) {
  case CardRecognitionState::SkippedDetectorUnstable:
    return OcrSampleStatus::NotRun;
  case CardRecognitionState::BackendUnavailable:
    return OcrSampleStatus::BackendUnavailable;
  case CardRecognitionState::OcrFailed:
    return OcrSampleStatus::Failed;
  case CardRecognitionState::Unknown:
    return OcrSampleStatus::Unknown;
  case CardRecognitionState::Recognized:
    return card.augment_id.has_value() ? OcrSampleStatus::Matched
                                       : OcrSampleStatus::Unknown;
  default:
    return OcrSampleStatus::Unknown;
  }
}

[[nodiscard]] std::optional<lol_assistant::detector::ThreeCardRois>
ResolveCollectionRois(CollectionContext &context,
                      const lol_assistant::common::Frame &frame,
                      const lol_assistant::app::FrameProcessResult *result,
                      std::string &reason) {
  if (result != nullptr && result->rois.has_value() &&
      result->rois->IsValid()) {
    context.current_rois = result->rois;
    reason = "detector_rois";
    return result->rois;
  }
  if (context.current_rois.has_value() && context.current_rois->IsValid() &&
      context.current_rois->resolution.width == frame.width &&
      context.current_rois->resolution.height == frame.height) {
    reason = "current_roi_calibration";
    return context.current_rois;
  }

  const auto computed = lol_assistant::detector::ComputeThreeCardRois(
      frame.width, frame.height, ProductDetectorConfig().layout,
      ProductDetectorConfig().aspect_ratio_tolerance);
  if (!computed.ok()) {
    reason = "seed_roi_failed:" + computed.reason;
    return std::nullopt;
  }
  context.current_rois = computed.value;
  reason = "seed_roi_computed";
  return computed.value;
}

[[nodiscard]] lol_assistant::collection::SampleCollectionRequest
BuildCollectionRequest(
    const CollectionContext &context,
    const lol_assistant::collection::CaptureReason capture_reason,
    std::string detail, const lol_assistant::detector::ThreeCardRois &rois,
    const lol_assistant::app::FrameProcessResult *result) {
  using lol_assistant::collection::OcrSampleStatus;
  lol_assistant::collection::SampleCollectionRequest request;
  request.sample_kind = context.sample_kind;
  request.capture_reason = capture_reason;
  request.capture_reason_detail = std::move(detail);
  request.window = context.window;
  request.ui_scale = rois.ui_scale;
  request.rois.offer = FullCropRoi(rois.offer_region);
  for (std::size_t index = 0U; index < rois.cards.size(); ++index) {
    request.rois.cards[index].card = FullCropRoi(rois.cards[index]);
    request.rois.cards[index].title = FullCropRoi(rois.title_rects[index]);
    request.rois.cards[index].icon = FullCropRoi(rois.icon_rects[index]);
  }
  if (result == nullptr) {
    return request;
  }
  request.detector = {result->raw_detector.visible,
                      result->raw_detector.confidence,
                      result->raw_detector.reason};
  if (!result->recognition.has_value()) {
    return request;
  }
  for (std::size_t index = 0U; index < result->recognition->cards.size();
       ++index) {
    const auto &source = result->recognition->cards[index];
    auto &destination = request.cards[index];
    destination.raw = source.raw_text;
    destination.lines = SplitOcrLines(source.raw_text);
    destination.status = ToOcrSampleStatus(source);
    destination.confidence = source.ocr_confidence;
    if (destination.status == OcrSampleStatus::Matched) {
      destination.matched_id = source.augment_id;
    }
  }
  return request;
}

void CollectFrame(Phase1Pipeline &pipeline,
                  const lol_assistant::common::Frame &frame,
                  const lol_assistant::app::FrameProcessResult *result,
                  const lol_assistant::collection::CaptureReason capture_reason,
                  std::string detail,
                  const std::string_view trigger_override = {}) noexcept {
  const bool manual =
      capture_reason == lol_assistant::collection::CaptureReason::ManualF8 ||
      trigger_override == "force_recognition";
  if (manual) {
    ++pipeline.collection.counters.manual;
  } else {
    ++pipeline.collection.counters.automatic;
  }
  const std::string_view trigger = trigger_override.empty()
                                       ? (manual ? "manual" : "auto")
                                       : trigger_override;
  try {
    if (pipeline.collection.collector == nullptr) {
      PrintCollectionError(pipeline.collection, trigger, frame.frame_id,
                           "collector_unavailable",
                           "sample collector is not available");
      return;
    }
    std::string roi_source;
    const auto rois =
        ResolveCollectionRois(pipeline.collection, frame, result, roi_source);
    if (!rois.has_value()) {
      PrintCollectionError(pipeline.collection, trigger, frame.frame_id,
                           "roi_unavailable", roi_source);
      return;
    }
    detail.append(";roi_source=");
    detail.append(roi_source);
    const auto request = BuildCollectionRequest(
        pipeline.collection, capture_reason, std::move(detail), *rois, result);
    const auto collected =
        pipeline.collection.collector->Collect(frame, request);
    if (collected.saved()) {
      ++pipeline.collection.counters.saved;
      PrintCollectionEvent(pipeline.collection, "saved", trigger,
                           frame.frame_id, collected.message,
                           collected.sample_directory);
    } else if (collected.duplicate()) {
      ++pipeline.collection.counters.duplicates;
      PrintCollectionEvent(pipeline.collection, "duplicate", trigger,
                           frame.frame_id, collected.message,
                           collected.sample_directory);
    } else {
      PrintCollectionError(pipeline.collection, trigger, frame.frame_id,
                           CollectStatusText(collected.status),
                           collected.message);
    }
  } catch (const std::exception &error) {
    PrintCollectionError(pipeline.collection, trigger, frame.frame_id,
                         "integration_exception", error.what());
  } catch (...) {
    PrintCollectionError(pipeline.collection, trigger, frame.frame_id,
                         "integration_exception",
                         "unknown collection integration exception");
  }
}

[[nodiscard]] bool HasOcrIconConflict(
    const lol_assistant::app::FrameProcessResult &result) noexcept {
  if (!result.recognition.has_value()) {
    return false;
  }
  return std::any_of(
      result.recognition->cards.begin(), result.recognition->cards.end(),
      [](const lol_assistant::vision::CardRecognitionOutput &card) {
        return card.state ==
                   lol_assistant::vision::CardRecognitionState::Unknown &&
               card.reason.starts_with("ocr_icon_conflict:");
      });
}

void HashByte(std::uint64_t &hash, const std::uint8_t value) noexcept {
  constexpr std::uint64_t kFnvPrime = 1'099'511'628'211ULL;
  hash ^= value;
  hash *= kFnvPrime;
}

[[nodiscard]] std::string HashText(const std::uint64_t hash) {
  std::ostringstream output;
  output << std::hex << std::setw(16) << std::setfill('0') << hash;
  return output.str();
}

void HashFrameGrid(const lol_assistant::common::Frame &frame,
                   const std::uint32_t left, const std::uint32_t top,
                   const std::uint32_t width, const std::uint32_t height,
                   const std::uint32_t columns, const std::uint32_t rows,
                   std::uint64_t &hash) noexcept {
  if (!frame.IsValid() || width == 0U || height == 0U || columns == 0U ||
      rows == 0U || left >= frame.width || top >= frame.height) {
    return;
  }
  const std::uint32_t bounded_width = std::min(width, frame.width - left);
  const std::uint32_t bounded_height = std::min(height, frame.height - top);
  for (std::uint32_t row = 0U; row < rows; ++row) {
    const std::uint32_t y =
        top + static_cast<std::uint32_t>(
                  (static_cast<std::uint64_t>(row) * (bounded_height - 1U)) /
                  std::max<std::uint32_t>(1U, rows - 1U));
    for (std::uint32_t column = 0U; column < columns; ++column) {
      const std::uint32_t x =
          left +
          static_cast<std::uint32_t>(
              (static_cast<std::uint64_t>(column) * (bounded_width - 1U)) /
              std::max<std::uint32_t>(1U, columns - 1U));
      const std::size_t offset =
          static_cast<std::size_t>(y) * frame.stride +
          static_cast<std::size_t>(x) *
              lol_assistant::common::Frame::kBytesPerPixel;
      HashByte(hash, frame.buffer[offset]);
      HashByte(hash, frame.buffer[offset + 1U]);
      HashByte(hash, frame.buffer[offset + 2U]);
    }
  }
}

[[nodiscard]] std::string
TransportContentHash(const lol_assistant::common::Frame &frame) {
  return lol_assistant::app::ComputeTransportContentHash(frame);
}

[[nodiscard]] std::string
SampleContentKey(const lol_assistant::common::Frame &frame,
                 const lol_assistant::app::FrameProcessResult *const result) {
  if (result == nullptr || !result->rois.has_value() ||
      !result->rois->IsValid()) {
    return TransportContentHash(frame);
  }
  std::uint64_t hash = 14'695'981'039'346'656'037ULL;
  for (std::size_t index = 0U; index < result->rois->title_rects.size();
       ++index) {
    HashByte(hash, static_cast<std::uint8_t>(index));
    const auto &roi = result->rois->title_rects[index];
    HashFrameGrid(frame, roi.x, roi.y, roi.width, roi.height, 24U, 8U, hash);
  }
  return HashText(hash);
}

[[nodiscard]] bool
IsCurrentLiveFrame(const Phase1Pipeline &pipeline,
                   const LiveFrameContext &context,
                   const std::chrono::steady_clock::time_point now) noexcept {
  return pipeline.active_live_source.has_value() &&
         *pipeline.active_live_source == context.source &&
         now >= context.observed_at &&
         now - context.observed_at <= kCaptureFreshTimeout;
}

[[nodiscard]] lol_assistant::app::SampleDecision
EvaluateSamplePolicy(Phase1Pipeline &pipeline,
                     const lol_assistant::common::Frame &frame,
                     const lol_assistant::app::FrameProcessResult *const result,
                     const LiveFrameContext *const live_context,
                     const lol_assistant::app::SampleTrigger trigger,
                     const std::string_view reason, const bool auto_eligible,
                     const std::uint64_t request_id = 0U) {
  if (pipeline.sample_policy == nullptr) {
    return lol_assistant::app::SampleDecision::Allow;
  }
  lol_assistant::app::SamplePolicyInput input;
  input.trigger = trigger;
  input.transport_fresh = live_context != nullptr &&
                          IsCurrentLiveFrame(pipeline, *live_context,
                                             std::chrono::steady_clock::now());
  if (live_context != nullptr) {
    input.source = live_context->source;
    input.epoch = live_context->epoch;
  }
  input.content_key = SampleContentKey(frame, result);
  input.reason.assign(reason);
  input.auto_eligible = auto_eligible;
  input.request_id = request_id;
  const auto decision = pipeline.sample_policy->Evaluate(input);
  pipeline.control_state_dirty = true;
  return decision;
}

void MaybeCollectAutomatically(
    Phase1Pipeline &pipeline, const lol_assistant::common::Frame &frame,
    const lol_assistant::app::FrameProcessResult &result,
    const LiveFrameContext *const live_context) noexcept {
  if (!pipeline.collection.requested) {
    return;
  }
  if (pipeline.sample_policy == nullptr) {
    const bool raw_suspect =
        result.raw_detector.visible ||
        (pipeline.collection.suspect_confidence.has_value() &&
         result.raw_detector.confidence >=
             static_cast<float>(*pipeline.collection.suspect_confidence));
    if (!raw_suspect) {
      pipeline.collection.last_auto_attempt.reset();
      return;
    }
    const auto now = std::chrono::steady_clock::now();
    if (pipeline.collection.last_auto_attempt.has_value() &&
        now - *pipeline.collection.last_auto_attempt <
            std::chrono::seconds{1}) {
      return;
    }
    pipeline.collection.last_auto_attempt = now;
    CollectFrame(pipeline, frame, &result,
                 lol_assistant::collection::CaptureReason::DetectorSuspect,
                 "raw_detector:" + result.raw_detector.reason);
    return;
  }

  const bool icon_conflict = HasOcrIconConflict(result);
  const bool configured_low_confidence =
      pipeline.collection.suspect_confidence.has_value() &&
      result.raw_detector.confidence >=
          static_cast<float>(*pipeline.collection.suspect_confidence) &&
      !result.accepted && !result.duplicate;
  const bool ocr_diagnostic =
      result.ocr_executed && !result.accepted && !result.duplicate;
  if (!icon_conflict && !configured_low_confidence && !ocr_diagnostic) {
    pipeline.collection.last_auto_attempt.reset();
    return;
  }
  std::string reason;
  if (icon_conflict) {
    reason = "ocr_icon_conflict";
  } else if (ocr_diagnostic) {
    reason = "processor:" + result.reason;
  } else {
    reason = "detector_confidence:" + result.raw_detector.reason;
  }
  const auto now = std::chrono::steady_clock::now();
  if (pipeline.collection.last_auto_attempt.has_value() &&
      now - *pipeline.collection.last_auto_attempt < std::chrono::seconds{1}) {
    return;
  }
  pipeline.collection.last_auto_attempt = now;
  const auto decision = EvaluateSamplePolicy(
      pipeline, frame, &result, live_context,
      lol_assistant::app::SampleTrigger::Auto, reason, true);
  if (decision == lol_assistant::app::SampleDecision::Allow) {
    CollectFrame(pipeline, frame, &result,
                 lol_assistant::collection::CaptureReason::DetectorSuspect,
                 reason);
  }
}

void PollSampleHotkey(Phase1Pipeline &pipeline) {
  if (!pipeline.collection.requested ||
      !pipeline.collection.sample_hotkey_enabled) {
    return;
  }
  const bool down = (GetAsyncKeyState(VK_F8) & 0x8000) != 0;
  if (!pipeline.collection.f8_edge.Observe(down)) {
    return;
  }
  if (!pipeline.collection.latest_frame.has_value()) {
    ++pipeline.collection.counters.manual;
    PrintCollectionError(pipeline.collection, "manual", std::nullopt,
                         "latest_frame_unavailable",
                         "F8 pressed before the first LoL frame arrived");
    return;
  }
  const auto *result = pipeline.collection.latest_result.has_value()
                           ? &*pipeline.collection.latest_result
                           : nullptr;
  ++pipeline.collection.manual_request_id;
  const auto *live_context = pipeline.collection.latest_live_context.has_value()
                                 ? &*pipeline.collection.latest_live_context
                                 : nullptr;
  const auto decision = EvaluateSamplePolicy(
      pipeline, *pipeline.collection.latest_frame, result, live_context,
      lol_assistant::app::SampleTrigger::ManualF8, "f8_press_edge", true,
      pipeline.collection.manual_request_id);
  if (decision != lol_assistant::app::SampleDecision::Allow) {
    PrintCollectionEvent(pipeline.collection, "suppressed", "manual",
                         pipeline.collection.latest_frame->frame_id,
                         lol_assistant::app::ToString(decision));
    return;
  }
  CollectFrame(pipeline, *pipeline.collection.latest_frame, result,
               lol_assistant::collection::CaptureReason::ManualF8,
               "f8_press_edge");
}

[[nodiscard]] bool
PollForceRecognitionHotkey(Phase1Pipeline &pipeline,
                           const std::string_view active_source,
                           const std::chrono::steady_clock::time_point now) {
  if (pipeline.force_recognition.controller == nullptr) {
    return false;
  }
  const auto events =
      pipeline.force_recognition.enabled
          ? pipeline.force_recognition.controller->Poll(
                (GetAsyncKeyState(VK_F9) & 0x8000) != 0, active_source, now)
          : pipeline.force_recognition.controller->Tick(now);
  EmitForceRecognitionEvents(pipeline, events);
  return !events.empty();
}

[[nodiscard]] bool
ForceRecognitionActive(const Phase1Pipeline &pipeline,
                       const std::chrono::steady_clock::time_point now) {
  return pipeline.force_recognition.controller != nullptr &&
         pipeline.force_recognition.controller->Snapshot(now).phase ==
             lol_assistant::app::ForcePhase::Active;
}

void InitializeCollection(
    const ApplicationOptions &options,
    const lol_assistant::collection::SampleKind sample_kind,
    std::string provenance, lol_assistant::collection::WindowMetadata window,
    Phase1Pipeline &pipeline) noexcept {
  pipeline.collection.requested = options.collect_samples;
  pipeline.collection.sample_hotkey_enabled =
      options.collect_samples && options.sample_hotkey_enabled &&
      options.source_kind != SourceKind::Replay;
  pipeline.collection.sample_kind = sample_kind;
  pipeline.collection.provenance = std::move(provenance);
  pipeline.collection.window = std::move(window);
  pipeline.collection.suspect_confidence = options.collect_suspect_confidence;
  if (!options.collect_samples) {
    return;
  }
  try {
    std::filesystem::path collector_root = options.dataset_root;
    std::wstring bucket = collector_root.filename().wstring();
    std::transform(bucket.begin(), bucket.end(), bucket.begin(),
                   [](const wchar_t character) {
                     return static_cast<wchar_t>(std::towlower(character));
                   });
    std::error_code dataset_error;
    const bool dataset_parent = std::filesystem::is_regular_file(
        collector_root / L"schema.json", dataset_error);
    if (!dataset_error && dataset_parent && bucket != L"real" &&
        bucket != L"synthetic" && bucket != L"unknown") {
      switch (sample_kind) {
      case lol_assistant::collection::SampleKind::Real:
        collector_root /= L"real";
        break;
      case lol_assistant::collection::SampleKind::Synthetic:
        collector_root /= L"synthetic";
        break;
      case lol_assistant::collection::SampleKind::Unknown:
        collector_root /= L"unknown";
        break;
      default:
        break;
      }
    }
    pipeline.collection.collector =
        std::make_unique<lol_assistant::collection::SampleCollector>(
            lol_assistant::collection::SampleCollectorConfig{
                std::move(collector_root)});
  } catch (const std::exception &error) {
    PrintCollectionError(pipeline.collection, "setup", std::nullopt,
                         "collector_initialization_failed", error.what());
  } catch (...) {
    PrintCollectionError(pipeline.collection, "setup", std::nullopt,
                         "collector_initialization_failed",
                         "unknown collector initialization exception");
  }
}

void PrintSessionStart(const Phase1Pipeline &pipeline,
                       const ApplicationOptions &options) {
  std::string output{"{\"type\":\"session_start\",\"session_id\":"};
  AppendJsonString(output, pipeline.runtime->SessionId());
  output.append(",\"output\":");
  AppendJsonString(output, PathToUtf8(pipeline.runtime->OutputDirectory()));
  output.append(",\"database\":");
  AppendJsonString(output, PathToUtf8(pipeline.runtime->DatabasePath()));
  output.append(",\"jsonl\":");
  AppendJsonString(output, PathToUtf8(pipeline.runtime->JsonlPath()));
  output.append(",\"collection_active\":");
  output.append(pipeline.collection.collector != nullptr ? "true" : "false");
  output.append(",\"icon_template_count\":");
  output.append(std::to_string(pipeline.icon_template_count));
  output.append(",\"icon_manifest\":");
  AppendJsonString(output, PathToUtf8(pipeline.icon_manifest_path));
  output.append(",\"collection_dataset_root\":");
  if (pipeline.collection.collector != nullptr) {
    AppendJsonString(output,
                     PathToUtf8(pipeline.collection.collector->dataset_root()));
  } else {
    output.append("null");
  }
  AppendRunOptions(output, options);
  output.push_back('}');
  std::cout << output << '\n';
}

void InitializePipeline(const ApplicationOptions &options,
                        std::string source_name, std::string source_id,
                        const lol_assistant::collection::SampleKind sample_kind,
                        std::string provenance,
                        lol_assistant::collection::WindowMetadata window,
                        Phase1Pipeline &pipeline) {
  pipeline.accepted_offer_count = options.completed_offers;
  auto catalog_result =
      lol_assistant::knowledge::LoadAugmentCatalog(options.knowledge_path);
  if (!catalog_result.ok()) {
    throw std::runtime_error("Unable to load augment catalog: " +
                             catalog_result.reason);
  }

  const std::string mode{lol_assistant::app::ToString(options.mode)};
  const bool hex_card_tolerant = true;
  const std::string icon_mode =
      options.mode == lol_assistant::app::Mode::KiwiJade ? "KIWI_JADE" : "KIWI";
  const auto manual_champion = ManualChampion(options);
  const auto create_status = lol_assistant::app::Phase1SessionRuntime::Create(
      options.workspace_path, manual_champion, mode,
      lol_assistant::app::SessionSourceMetadata{
          std::move(source_name),
          lol_assistant::vision::kWindowsMediaOcrZhCnBackend,
          catalog_result.catalog->catalog_version(), std::move(source_id)},
      pipeline.runtime);
  if (!create_status.IsSuccess() || pipeline.runtime == nullptr) {
    throw std::runtime_error("Unable to create Phase1 session runtime: " +
                             create_status.message);
  }
  pipeline.icon_template_count = 0U;
  pipeline.icon_manifest_path.clear();
  pipeline.hud_icon_templates.clear();
  pipeline.icon_mode = icon_mode;
  pipeline.force_recognition.enabled =
      options.force_recognition_hotkey_enabled &&
      options.source_kind != SourceKind::Replay;
  lol_assistant::app::ForceRecognitionConfig force_config;
  force_config.enabled = pipeline.force_recognition.enabled;
  force_config.fresh_frame_budget =
      lol_assistant::app::kForceRecognitionBurstFrames;
  force_config.deadline = std::chrono::milliseconds{
      lol_assistant::app::kForceRecognitionBurstMilliseconds};
  pipeline.force_recognition.controller =
      std::make_unique<lol_assistant::app::ForceRecognitionController>(
          force_config);
  if (options.source_kind != SourceKind::Replay) {
    pipeline.sample_policy =
        std::make_unique<lol_assistant::app::BoundedSamplePolicy>();
  }
  InitializeCollection(options, sample_kind, std::move(provenance),
                       std::move(window), pipeline);
  PrintSessionStart(pipeline, options);

  auto candidates = lol_assistant::vision::BuildTitleCandidatesWithArenaNames(
      *catalog_result.catalog, icon_mode);
  lol_assistant::detector::StableDetectorConfig confirmer{};
  if (hex_card_tolerant) {
    confirmer.required_consecutive_frames = 1U;
    confirmer.minimum_frame_confidence = 0.45F;
  }
  pipeline.processor =
      std::make_unique<lol_assistant::app::AugmentFrameProcessor>(
          pipeline.ocr, std::move(candidates), *pipeline.runtime,
          manual_champion, SelectedSlot(options),
          ProductDetectorConfig(hex_card_tolerant), confirmer, 0.85F,
          std::vector<lol_assistant::vision::IconHashTemplate>{}, icon_mode,
          options.completed_offers, false);

  if (options.preview) {
    pipeline.preview =
        std::make_unique<lol_assistant::output::DebugPreviewWindow>(
            "LoL Assistant Phase2 Debug Preview");
    if (!pipeline.preview->Create()) {
      throw std::runtime_error("Unable to create debug preview window");
    }
  }
}

[[nodiscard]] bool
ArmHudSelectionObservation(Phase1Pipeline &pipeline,
                           const lol_assistant::app::FrameProcessResult &result,
                           const lol_assistant::common::Frame &frame,
                           const std::uint32_t stage) {
  if (stage < 1U || stage > 4U || !result.recognition.has_value()) {
    return false;
  }
  if (std::any_of(pipeline.pending_hud_selections.begin(),
                  pipeline.pending_hud_selections.end(),
                  [stage](const PendingHudSelection &pending) {
                    return pending.stage == stage;
                  })) {
    return false;
  }

  PendingHudSelection pending;
  pending.stage = stage;
  std::vector<lol_assistant::vision::IconHashTemplate> scoped_templates;
  for (std::size_t index = 0U; index < result.recognition->cards.size();
       ++index) {
    const auto &card = result.recognition->cards[index];
    if (card.state != lol_assistant::vision::CardRecognitionState::Recognized ||
        !card.augment_id.has_value() || card.augment_id->empty()) {
      return false;
    }
    pending.offer_augment_ids[index] = *card.augment_id;
    bool found_template = false;
    for (const auto &icon_template : pipeline.hud_icon_templates) {
      if (icon_template.augment_id == *card.augment_id) {
        scoped_templates.push_back(icon_template);
        found_template = true;
      }
    }
    if (!found_template) {
      return false;
    }
  }

  pending.detector =
      std::make_unique<lol_assistant::vision::SelectedCardDetector>(
          std::move(scoped_templates),
          lol_assistant::vision::SelectedCardDetectorConfig{},
          pipeline.icon_mode);
  pending.empty_slot_baseline = frame;
  pipeline.pending_hud_selections.push_back(std::move(pending));
  return true;
}

void ResetHudSelectionConsensus(PendingHudSelection &pending) noexcept {
  pending.stable_candidate_id.reset();
  pending.stable_offer_slot.reset();
  pending.stable_frames = 0U;
  pending.minimum_top1_score = 1.0F;
  pending.minimum_top1_margin = 1.0F;
}

[[nodiscard]] lol_assistant::detector::PixelRoi ExpandRoiForGlow(
    const lol_assistant::detector::PixelRoi &roi, const std::uint32_t frame_width,
    const std::uint32_t frame_height) noexcept {
  const auto pad_x = std::max(4U, roi.width / 20U);
  const auto pad_y = std::max(4U, roi.height / 20U);
  lol_assistant::detector::PixelRoi expanded = roi;
  expanded.x = roi.x > pad_x ? roi.x - pad_x : 0U;
  expanded.y = roi.y > pad_y ? roi.y - pad_y : 0U;
  expanded.width =
      std::min(frame_width - expanded.x, roi.width + pad_x * 2U);
  expanded.height =
      std::min(frame_height - expanded.y, roi.height + pad_y * 2U);
  expanded.primary_ocr_rect.reset();
  return expanded;
}

[[nodiscard]] float MeanCardBorderLuma(
    const lol_assistant::common::Frame &frame,
    const lol_assistant::detector::PixelRoi &roi) {
  if (!frame.IsValid() || !roi.IsInside(frame.width, frame.height) ||
      roi.width < 8U || roi.height < 8U) {
    return 0.0F;
  }
  const std::uint32_t border_x = std::max(2U, roi.width / 10U);
  const std::uint32_t border_y = std::max(2U, roi.height / 10U);
  double sum = 0.0;
  std::uint64_t count = 0U;
  constexpr std::uint32_t kStep = 2U;
  for (std::uint32_t y = 0U; y < roi.height; y += kStep) {
    const std::uint8_t *row =
        frame.buffer.data() +
        (static_cast<std::size_t>(roi.y + y) * frame.stride) +
        (static_cast<std::size_t>(roi.x) *
         lol_assistant::common::Frame::kBytesPerPixel);
    for (std::uint32_t x = 0U; x < roi.width; x += kStep) {
      const bool on_border = x < border_x || x + border_x >= roi.width ||
                             y < border_y || y + border_y >= roi.height;
      if (on_border) {
        const float blue = static_cast<float>(row[0]);
        const float green = static_cast<float>(row[1]);
        const float red = static_cast<float>(row[2]);
        sum += 0.2126 * static_cast<double>(red) +
               0.7152 * static_cast<double>(green) +
               0.0722 * static_cast<double>(blue);
        ++count;
      }
      row += lol_assistant::common::Frame::kBytesPerPixel * kStep;
    }
  }
  if (count == 0U) {
    return 0.0F;
  }
  return static_cast<float>(sum / static_cast<double>(count));
}

[[nodiscard]] float MeanCardInnerLuma(
    const lol_assistant::common::Frame &frame,
    const lol_assistant::detector::PixelRoi &roi) {
  if (!frame.IsValid() || !roi.IsInside(frame.width, frame.height) ||
      roi.width < 8U || roi.height < 8U) {
    return 0.0F;
  }
  const std::uint32_t border_x = std::max(2U, roi.width / 10U);
  const std::uint32_t border_y = std::max(2U, roi.height / 10U);
  double sum = 0.0;
  std::uint64_t count = 0U;
  constexpr std::uint32_t kStep = 2U;
  for (std::uint32_t y = 0U; y < roi.height; y += kStep) {
    const std::uint8_t *row =
        frame.buffer.data() +
        (static_cast<std::size_t>(roi.y + y) * frame.stride) +
        (static_cast<std::size_t>(roi.x) *
         lol_assistant::common::Frame::kBytesPerPixel);
    for (std::uint32_t x = 0U; x < roi.width; x += kStep) {
      const bool on_border = x < border_x || x + border_x >= roi.width ||
                             y < border_y || y + border_y >= roi.height;
      if (!on_border) {
        const float blue = static_cast<float>(row[0]);
        const float green = static_cast<float>(row[1]);
        const float red = static_cast<float>(row[2]);
        sum += 0.2126 * static_cast<double>(red) +
               0.7152 * static_cast<double>(green) +
               0.0722 * static_cast<double>(blue);
        ++count;
      }
      row += lol_assistant::common::Frame::kBytesPerPixel * kStep;
    }
  }
  if (count == 0U) {
    return 0.0F;
  }
  return static_cast<float>(sum / static_cast<double>(count));
}

void RefreshClickOfferIds(Phase1Pipeline &pipeline,
                          const lol_assistant::app::FrameProcessResult &result) {
  if (!result.recognition.has_value()) {
    return;
  }
  auto ids = pipeline.click_offer_ids;
  auto names = pipeline.click_offer_names;
  bool any = false;
  for (std::size_t index = 0U; index < ids.size(); ++index) {
    const auto &card = result.recognition->cards[index];
    if (card.state == lol_assistant::vision::CardRecognitionState::Recognized &&
        card.augment_id.has_value() && !card.augment_id->empty() &&
        *card.augment_id != "UNKNOWN" && card.display_name.has_value() &&
        !card.display_name->empty()) {
      ids[index] = *card.augment_id;
      names[index] = *card.display_name;
      any = true;
    }
  }
  if (!any) {
    return;
  }
  bool complete = true;
  for (const auto &id : ids) {
    if (id.empty()) {
      complete = false;
      break;
    }
  }
  if (pipeline.click_offer_ids == ids && pipeline.click_offer_names == names &&
      pipeline.click_offer_valid == complete) {
    return;
  }
  const bool replacement =
      pipeline.click_offer_valid && complete && pipeline.click_offer_ids != ids;
  const bool first_complete = !pipeline.click_offer_valid && complete;
  pipeline.click_offer_ids = ids;
  pipeline.click_offer_names = names;
  pipeline.click_offer_valid = complete;
  if (first_complete) {
    pipeline.hover_click.ResetForNewOffer();
    pipeline.pick_tracker.ArmOffer();
    pipeline.ocr_pick_slot.reset();
    pipeline.ocr_pick_frames = 0U;
  }
  if (replacement) {
    pipeline.hover_click.ResetForNewOffer();
    pipeline.pick_tracker.Reset();
    pipeline.ocr_pick_slot.reset();
    pipeline.ocr_pick_frames = 0U;
  }
}

void EnsureSeedPickRois(Phase1Pipeline &pipeline,
                        const lol_assistant::common::Frame &frame) {
  if (pipeline.pick_rois.has_value() && pipeline.pick_rois->IsValid() &&
      pipeline.pick_rois->resolution.width == frame.width &&
      pipeline.pick_rois->resolution.height == frame.height) {
    return;
  }
  const auto computed = lol_assistant::detector::ComputeThreeCardRois(
      frame.width, frame.height, ProductDetectorConfig().layout,
      ProductDetectorConfig().aspect_ratio_tolerance);
  if (!computed.ok()) {
    pipeline.pick_rois.reset();
    return;
  }
  pipeline.pick_rois = computed.value;
}

constexpr std::uint32_t kPresenceGridWidth = 12U;
constexpr std::uint32_t kPresenceGridHeight = 16U;
constexpr std::size_t kPresenceGridValues =
    static_cast<std::size_t>(kPresenceGridWidth) * kPresenceGridHeight * 3U;

[[nodiscard]] bool DownsampleRoiBgr(
    const lol_assistant::common::Frame &frame,
    const lol_assistant::detector::PixelRoi &roi, float *dest) {
  if (dest == nullptr || !frame.IsValid() ||
      !roi.IsInside(frame.width, frame.height) || roi.width < 8U ||
      roi.height < 8U) {
    return false;
  }
  for (std::uint32_t gy = 0U; gy < kPresenceGridHeight; ++gy) {
    const std::uint32_t y =
        std::min(frame.height - 1U,
                 roi.y + (gy * roi.height + roi.height / 2U) /
                             kPresenceGridHeight);
    for (std::uint32_t gx = 0U; gx < kPresenceGridWidth; ++gx) {
      const std::uint32_t x =
          std::min(frame.width - 1U,
                   roi.x + (gx * roi.width + roi.width / 2U) /
                               kPresenceGridWidth);
      const std::uint8_t *pixel =
          frame.buffer.data() + (static_cast<std::size_t>(y) * frame.stride) +
          (static_cast<std::size_t>(x) *
           lol_assistant::common::Frame::kBytesPerPixel);
      const std::size_t offset =
          (static_cast<std::size_t>(gy) * kPresenceGridWidth + gx) * 3U;
      dest[offset] = static_cast<float>(pixel[0]);
      dest[offset + 1U] = static_cast<float>(pixel[1]);
      dest[offset + 2U] = static_cast<float>(pixel[2]);
    }
  }
  return true;
}

[[nodiscard]] float NormalizedCorrelation(const float *left, const float *right,
                                          const std::size_t count) noexcept {
  if (left == nullptr || right == nullptr || count == 0U) {
    return 0.0F;
  }
  double left_sum = 0.0;
  double right_sum = 0.0;
  for (std::size_t index = 0U; index < count; ++index) {
    left_sum += static_cast<double>(left[index]);
    right_sum += static_cast<double>(right[index]);
  }
  const double left_mean = left_sum / static_cast<double>(count);
  const double right_mean = right_sum / static_cast<double>(count);
  double dot = 0.0;
  double left_norm = 0.0;
  double right_norm = 0.0;
  for (std::size_t index = 0U; index < count; ++index) {
    const double a = static_cast<double>(left[index]) - left_mean;
    const double b = static_cast<double>(right[index]) - right_mean;
    dot += a * b;
    left_norm += a * a;
    right_norm += b * b;
  }
  const double denom = std::sqrt(left_norm * right_norm);
  if (denom < 1.0e-6) {
    return 0.0F;
  }
  return static_cast<float>(dot / denom);
}

[[nodiscard]] lol_assistant::detector::PixelRoi
AlleyBetween(const lol_assistant::detector::PixelRoi &left,
             const lol_assistant::detector::PixelRoi &right) noexcept {
  lol_assistant::detector::PixelRoi alley{};
  const std::uint32_t left_end = left.x + left.width;
  if (right.x <= left_end + 4U) {
    return alley;
  }
  alley.x = left_end + 2U;
  alley.width = right.x - left_end - 4U;
  const std::uint32_t top = std::max(left.y, right.y);
  const std::uint32_t bottom =
      std::min(left.y + left.height, right.y + right.height);
  if (bottom <= top + 8U) {
    return alley;
  }
  const std::uint32_t inset = (bottom - top) / 6U;
  alley.y = top + inset;
  alley.height = bottom - top - inset * 2U;
  return alley;
}

[[nodiscard]] bool MeasureAlleyBackground(
    const lol_assistant::common::Frame &frame,
    const lol_assistant::detector::ThreeCardRois &rois, float *dest) {
  if (dest == nullptr) {
    return false;
  }
  const auto left_gap = AlleyBetween(rois.cards[0], rois.cards[1]);
  const auto right_gap = AlleyBetween(rois.cards[1], rois.cards[2]);
  std::array<float, kPresenceGridValues> left{};
  std::array<float, kPresenceGridValues> right{};
  if (!DownsampleRoiBgr(frame, left_gap, left.data()) ||
      !DownsampleRoiBgr(frame, right_gap, right.data())) {
    return false;
  }
  for (std::size_t index = 0U; index < kPresenceGridValues; ++index) {
    dest[index] = 0.5F * (left[index] + right[index]);
  }
  return true;
}

void RefreshHoverGlow(Phase1Pipeline &pipeline,
                      const lol_assistant::common::Frame &frame) {
  EnsureSeedPickRois(pipeline, frame);
  if (!pipeline.pick_rois.has_value() || !pipeline.pick_rois->IsValid()) {
    pipeline.card_presence_valid = false;
    return;
  }
  std::array<float, kPresenceGridValues> background{};
  pipeline.card_presence_valid = false;
  if (!MeasureAlleyBackground(frame, *pipeline.pick_rois, background.data())) {
    return;
  }
  std::array<float, lol_assistant::common::kAugmentCardCount> likeness{};
  for (std::size_t index = 0U; index < likeness.size(); ++index) {
    std::array<float, kPresenceGridValues> card{};
    if (!DownsampleRoiBgr(frame, pipeline.pick_rois->cards[index],
                          card.data())) {
      return;
    }
    likeness[index] =
        NormalizedCorrelation(card.data(), background.data(), kPresenceGridValues);
  }
  pipeline.card_background_likeness = likeness;
  pipeline.card_presence_valid = true;
}

[[nodiscard]] std::optional<std::size_t> DetectOcrSoleRemaining(
    const Phase1Pipeline &pipeline,
    const std::optional<lol_assistant::vision::OfferRecognitionOutput>
        &recognition) {
  if (!pipeline.click_offer_valid || !recognition.has_value()) {
    return std::nullopt;
  }
  std::optional<std::size_t> remaining;
  std::uint32_t matched = 0U;
  std::uint32_t vacant = 0U;
  for (std::size_t index = 0U; index < pipeline.click_offer_ids.size();
       ++index) {
    const auto &card = recognition->cards[index];
    const bool matched_id =
        card.state == lol_assistant::vision::CardRecognitionState::Recognized &&
        card.augment_id.has_value() &&
        *card.augment_id == pipeline.click_offer_ids[index];
    if (matched_id) {
      ++matched;
      remaining = index;
    } else {
      ++vacant;
    }
  }
  if (matched == 1U && vacant == 2U) {
    return remaining;
  }
  return std::nullopt;
}

[[nodiscard]] bool MaybeEmitFlashSelection(
    Phase1Pipeline &pipeline, const lol_assistant::common::Frame &frame,
    const bool offer_visible,
    const std::optional<lol_assistant::vision::OfferRecognitionOutput>
        &recognition = std::nullopt) {
  static_cast<void>(offer_visible);
  if (pipeline.hover_click.emitted() || pipeline.stop_after_pick) {
    return false;
  }
  if (!pipeline.click_offer_valid) {
    return false;
  }
  std::optional<std::size_t> visual;
  if (pipeline.card_presence_valid) {
    visual = pipeline.pick_tracker.Observe(pipeline.card_background_likeness);
  }
  const auto ocr_slot = DetectOcrSoleRemaining(pipeline, recognition);
  std::uint32_t ocr_matched = 0U;
  if (recognition.has_value() && pipeline.click_offer_valid) {
    for (std::size_t index = 0U; index < pipeline.click_offer_ids.size();
         ++index) {
      const auto &card = recognition->cards[index];
      if (card.state ==
              lol_assistant::vision::CardRecognitionState::Recognized &&
          card.augment_id.has_value() &&
          *card.augment_id == pipeline.click_offer_ids[index]) {
        ++ocr_matched;
      }
    }
  }
  if (ocr_slot.has_value()) {
    if (pipeline.ocr_pick_slot == ocr_slot) {
      pipeline.ocr_pick_frames =
          std::min(pipeline.ocr_pick_frames + 1U, 8U);
    } else {
      pipeline.ocr_pick_slot = ocr_slot;
      pipeline.ocr_pick_frames = 1U;
    }
  } else if (ocr_matched >= 2U) {
    pipeline.ocr_pick_slot.reset();
    pipeline.ocr_pick_frames = 0U;
  }
  std::uint32_t present = 0U;
  std::uint32_t gone = 0U;
  if (pipeline.card_presence_valid) {
    for (const float value : pipeline.card_background_likeness) {
      if (lol_assistant::vision::CardLooksPresent(value)) {
        ++present;
      } else if (lol_assistant::vision::CardLooksGone(value)) {
        ++gone;
      }
    }
  }
  const bool all_gone = pipeline.card_presence_valid && present == 0U &&
                        gone >= 2U;
  std::optional<std::size_t> selected = visual;
  if (!selected.has_value() && ocr_slot.has_value()) {
    bool others_gone = pipeline.ocr_pick_frames >= 1U && all_gone;
    if (!others_gone) {
      others_gone = pipeline.ocr_pick_frames >= 2U;
    }
    if (pipeline.card_presence_valid && !others_gone) {
      others_gone = true;
      for (std::size_t index = 0U;
           index < pipeline.card_background_likeness.size(); ++index) {
        if (index == *ocr_slot) {
          continue;
        }
        if (lol_assistant::vision::CardLooksPresent(
                pipeline.card_background_likeness[index])) {
          others_gone = false;
          break;
        }
      }
    }
    if (others_gone) {
      selected = ocr_slot;
    }
  }
  if (!selected.has_value() && all_gone) {
    if (pipeline.ocr_pick_slot.has_value()) {
      selected = pipeline.ocr_pick_slot;
    } else if (pipeline.hover_click.glowing_slot().has_value()) {
      selected = pipeline.hover_click.glowing_slot();
    }
  }
  if (!selected.has_value()) {
    return false;
  }
  const std::size_t winner = *selected;
  if (winner >= pipeline.click_offer_ids.size() ||
      pipeline.click_offer_ids[winner].empty() ||
      pipeline.click_offer_ids[winner] == "UNKNOWN") {
    return false;
  }
  const auto &likeness = pipeline.card_background_likeness;
  float emptiest = -1.0F;
  for (std::size_t index = 0U; index < likeness.size(); ++index) {
    if (index != winner) {
      emptiest = std::max(emptiest, likeness[index]);
    }
  }

  std::string output{"{\"type\":\"selection_observed\",\"schema_version\":1,"};
  output.append("\"observed_at_utc\":");
  AppendJsonString(output,
                   lol_assistant::output::detail::FormatUtcTimestamp(
                       frame.timestamps.captured_at_utc.value_or(
                           lol_assistant::common::UtcTimestamp::clock::now())));
  output.append(",\"source\":\"sole_remaining\"");
  output.append(",\"reason\":\"two_cards_vanished\"");
  output.append(",\"offer_stage\":");
  output.append(std::to_string(pipeline.accepted_offer_count + 1U));
  output.append(",\"offer_augment_ids\":[");
  for (std::size_t index = 0U; index < pipeline.click_offer_ids.size();
       ++index) {
    if (index != 0U) {
      output.push_back(',');
    }
    AppendJsonString(output, pipeline.click_offer_ids[index]);
  }
  output.append("],\"selected_slot\":");
  AppendJsonString(output, kOfferSlots[winner]);
  output.append(",\"selected_augment_id\":");
  if (!pipeline.click_offer_ids[winner].empty()) {
    AppendJsonString(output, pipeline.click_offer_ids[winner]);
  } else {
    AppendJsonString(output, "UNKNOWN");
  }
  if (!pipeline.click_offer_names[winner].empty()) {
    output.append(",\"display_name\":");
    AppendJsonString(output, pipeline.click_offer_names[winner]);
  }
  output.append(",\"confidence\":");
  output.append(std::to_string(
      std::clamp(emptiest - likeness[winner], 0.0F, 1.0F)));
  output.append(",\"background_likeness\":[");
  for (std::size_t index = 0U; index < likeness.size(); ++index) {
    if (index != 0U) {
      output.push_back(',');
    }
    output.append(std::to_string(likeness[index]));
  }
  output.append("]");
  output.append(",\"candidate_scope\":\"pending_offer\"");
  output.append(",\"frame_id\":");
  output.append(std::to_string(frame.frame_id));
  output.push_back('}');
  if (pipeline.control_writer != nullptr) {
    if (!pipeline.control_writer->Write(output)) {
      throw std::runtime_error("Unable to flush flash-luma selection event");
    }
  } else {
    std::cout << output << '\n';
    std::cout.flush();
  }
  pipeline.hover_click.MarkEmitted();
  pipeline.pending_hud_selections.clear();
  return true;
}

void ObserveCardPickLumaOnly(Phase1Pipeline &pipeline,
                             const lol_assistant::common::Frame &frame) {
  if (pipeline.pick_tracker.emitted() || pipeline.stop_after_pick) {
    return;
  }
  RefreshHoverGlow(pipeline, frame);
  if (!pipeline.card_presence_valid) {
    return;
  }
  std::uint32_t present = 0U;
  for (const float value : pipeline.card_background_likeness) {
    if (lol_assistant::vision::CardLooksPresent(value)) {
      ++present;
    }
  }
  static_cast<void>(MaybeEmitFlashSelection(
      pipeline, frame, present >= 2U,
      pipeline.collection.latest_result.has_value()
          ? pipeline.collection.latest_result->recognition
          : std::nullopt));
}

[[nodiscard]] bool
IsTerminalHudBaselineFailure(const std::string_view reason) noexcept {
  return reason == "invalid_previous_frame" ||
         reason == "invalid_observed_frame" ||
         reason == "frame_geometry_mismatch" ||
         reason == "unsupported_frame_aspect_ratio" ||
         reason == "hud_slot_roi_out_of_bounds" ||
         reason == "hud_slot_rois_overlap" ||
         reason == "next_owned_slot_out_of_range" ||
         reason == "next_owned_slot_was_not_empty";
}

void EmitUnknownHudSelection(Phase1Pipeline &pipeline,
                             const PendingHudSelection &pending,
                             const lol_assistant::common::Frame &frame,
                             const std::string_view reason) {
  std::string output{"{\"type\":\"selection_observed\",\"schema_version\":1,"};
  output.append("\"observed_at_utc\":");
  AppendJsonString(output,
                   lol_assistant::output::detail::FormatUtcTimestamp(
                       frame.timestamps.captured_at_utc.value_or(
                           lol_assistant::common::UtcTimestamp::clock::now())));
  output.append(",\"source\":\"hud_icon_template\"");
  output.append(",\"recognition_status\":\"UNKNOWN\"");
  output.append(",\"reason\":");
  AppendJsonString(output, reason);
  output.append(",\"offer_stage\":");
  output.append(std::to_string(pending.stage));
  output.append(",\"offer_augment_ids\":[");
  for (std::size_t index = 0U; index < pending.offer_augment_ids.size();
       ++index) {
    if (index != 0U) {
      output.push_back(',');
    }
    AppendJsonString(output, pending.offer_augment_ids[index]);
  }
  output.append("],\"selected_slot\":\"UNKNOWN\"");
  output.append(",\"selected_augment_id\":\"UNKNOWN\"");
  output.append(",\"candidate_scope\":\"pending_offer\"");
  output.append(",\"hud_slot_index\":");
  output.append(std::to_string(pending.stage - 1U));
  output.append(",\"frame_id\":");
  output.append(std::to_string(frame.frame_id));
  output.push_back('}');
  if (pipeline.control_writer == nullptr ||
      !pipeline.control_writer->Write(output)) {
    throw std::runtime_error("Unable to flush UNKNOWN HUD selection event");
  }
}

void ObserveHudSelection(Phase1Pipeline &pipeline,
                         const lol_assistant::common::Frame &frame) {
  constexpr std::uint32_t kRequiredStableFrames = 2U;
  if (pipeline.pending_hud_selections.empty()) {
    return;
  }

  auto &pending = pipeline.pending_hud_selections.front();
  if (pending.detector == nullptr || pending.stage < 1U ||
      pending.stage > lol_assistant::vision::kHudOwnedSlotCount ||
      (pending.last_frame_id.has_value() &&
       *pending.last_frame_id == frame.frame_id)) {
    return;
  }
  pending.last_frame_id = frame.frame_id;

  const std::size_t hud_slot_index = pending.stage - 1U;
  const auto detected = pending.detector->DetectNextOwned(
      pending.empty_slot_baseline, frame, hud_slot_index);
  if (!detected.identified() || !detected.icon_match.top1_score.has_value() ||
      !detected.icon_match.margin.has_value()) {
    if (IsTerminalHudBaselineFailure(detected.reason)) {
      EmitUnknownHudSelection(pipeline, pending, frame, detected.reason);
      pipeline.pending_hud_selections.pop_front();
      return;
    }
    ResetHudSelectionConsensus(pending);
    return;
  }

  std::optional<std::size_t> offer_slot;
  for (std::size_t index = 0U; index < pending.offer_augment_ids.size();
       ++index) {
    if (pending.offer_augment_ids[index] == *detected.candidate_id) {
      if (offer_slot.has_value()) {
        ResetHudSelectionConsensus(pending);
        return;
      }
      offer_slot = index;
    }
  }
  if (!offer_slot.has_value()) {
    ResetHudSelectionConsensus(pending);
    return;
  }

  if (pending.stable_candidate_id == detected.candidate_id &&
      pending.stable_offer_slot == offer_slot) {
    ++pending.stable_frames;
    pending.minimum_top1_score =
        std::min(pending.minimum_top1_score, *detected.icon_match.top1_score);
    pending.minimum_top1_margin =
        std::min(pending.minimum_top1_margin, *detected.icon_match.margin);
  } else {
    pending.stable_candidate_id = detected.candidate_id;
    pending.stable_offer_slot = offer_slot;
    pending.stable_frames = 1U;
    pending.minimum_top1_score = *detected.icon_match.top1_score;
    pending.minimum_top1_margin = *detected.icon_match.margin;
  }
  if (pending.stable_frames < kRequiredStableFrames) {
    return;
  }

  std::string output{"{\"type\":\"selection_observed\",\"schema_version\":1,"};
  output.append("\"observed_at_utc\":");
  AppendJsonString(output,
                   lol_assistant::output::detail::FormatUtcTimestamp(
                       frame.timestamps.captured_at_utc.value_or(
                           lol_assistant::common::UtcTimestamp::clock::now())));
  output.append(",\"source\":\"hud_icon_template\"");
  output.append(",\"offer_stage\":");
  output.append(std::to_string(pending.stage));
  output.append(",\"offer_augment_ids\":[");
  for (std::size_t index = 0U; index < pending.offer_augment_ids.size();
       ++index) {
    if (index != 0U) {
      output.push_back(',');
    }
    AppendJsonString(output, pending.offer_augment_ids[index]);
  }
  output.append("],\"selected_slot\":");
  AppendJsonString(output, kOfferSlots[*offer_slot]);
  output.append(",\"selected_augment_id\":");
  AppendJsonString(output, *detected.candidate_id);
  output.append(",\"confidence\":");
  output.append(std::to_string(pending.minimum_top1_score));
  output.append(",\"top1_score\":");
  output.append(std::to_string(pending.minimum_top1_score));
  output.append(",\"top1_margin\":");
  output.append(std::to_string(pending.minimum_top1_margin));
  output.append(",\"stable_frames\":");
  output.append(std::to_string(pending.stable_frames));
  output.append(",\"candidate_scope\":\"pending_offer\"");
  output.append(",\"hud_slot_index\":");
  output.append(std::to_string(hud_slot_index));
  output.append(",\"frame_id\":");
  output.append(std::to_string(frame.frame_id));
  output.push_back('}');

  if (pipeline.control_writer == nullptr ||
      !pipeline.control_writer->Write(output)) {
    throw std::runtime_error("Unable to flush HUD selection JSONL event");
  }
  pipeline.pending_hud_selections.pop_front();
}

void EmitClickAck(const std::string_view cause, const std::string_view reason) {
  std::string output{"{\"type\":\"click_ack\",\"reread_offer\":true"};
  output.append(",\"reread_cause\":");
  AppendJsonString(output, cause);
  output.append(",\"reason\":");
  AppendJsonString(output, reason);
  output.push_back('}');
  std::cout << output << '\n';
  std::cout.flush();
}

[[nodiscard]] bool OcrHoldActive(const std::filesystem::path &workspace) {
  std::error_code error;
  return std::filesystem::is_regular_file(workspace / "ocr_hold.flag", error);
}

void EmitIntervalMiss() {
  std::cout << "{\"type\":\"frame_result\",\"reread_offer\":true,"
               "\"reread_cause\":\"interval\",\"reason\":\"no_current_frame\","
               "\"ocr_executed\":false,\"accepted\":false}\n";
  std::cout.flush();
}

[[nodiscard]] std::uint32_t ProcessFrame(
    Phase1Pipeline &pipeline, const lol_assistant::common::Frame &frame,
    const std::optional<StaticReplayDiagnostic> &static_replay = std::nullopt,
    const bool force_recognition = false,
    const LiveFrameContext *const live_context = nullptr,
    const bool reread_offer = false, const bool emit_always = false) {
  const auto vision_started_at = std::chrono::steady_clock::now();
  const auto result = pipeline.processor->Process(
      frame, lol_assistant::app::FrameProcessOptions{force_recognition,
                                                     reread_offer});
  const double vision_processing_latency_ms =
      std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - vision_started_at)
          .count();
  ++pipeline.frames_processed;
  // Observe an already accepted round before publishing a newly accepted
  // offer.  This ordering is essential for directly queued screens: the
  // bridge updates owned_augments before it ranks the following offer.
  RefreshClickOfferIds(pipeline, result);
  lol_assistant::app::RecordRecognitionObservation(
      pipeline.recognition_progress, result.stable_detector.visible,
      result.ocr_executed, result.accepted, result.reason);
  if (result.accepted) {
    ++pipeline.accepted_offer_count;
  }
  pipeline.collection.latest_frame = frame;
  pipeline.collection.latest_result = result;
  if (live_context != nullptr) {
    pipeline.collection.latest_live_context = *live_context;
  }
  if (result.rois.has_value() && result.rois->IsValid()) {
    pipeline.collection.current_rois = result.rois;
  }
  const bool offer_visible =
      result.stable_detector.visible || result.raw_detector.visible;
  RefreshHoverGlow(pipeline, frame);
  const bool flash_pick = MaybeEmitFlashSelection(
      pipeline, frame, offer_visible, result.recognition);
  if (flash_pick || pipeline.stop_after_pick) {
    return pipeline.accepted_offer_count;
  }
  if (force_recognition) {
    if (pipeline.force_recognition.sample_pending_request_id.has_value()) {
      const std::uint64_t request_id =
          *pipeline.force_recognition.sample_pending_request_id;
      if (pipeline.collection.requested &&
          EvaluateSamplePolicy(pipeline, frame, &result, live_context,
                               lol_assistant::app::SampleTrigger::ForceF9,
                               "force_recognition:" + result.reason, true,
                               request_id) ==
              lol_assistant::app::SampleDecision::Allow) {
        CollectFrame(
            pipeline, frame, &result,
            lol_assistant::collection::CaptureReason::DetectorSuspect,
            "force_recognition;trigger=f9_press_edge;processor_reason=" +
                result.reason,
            "force_recognition");
      }
      pipeline.force_recognition.sample_pending_request_id.reset();
    }
  } else {
    MaybeCollectAutomatically(pipeline, frame, result, live_context);
  }

  const bool reason_changed = !pipeline.previous_reason.has_value() ||
                              *pipeline.previous_reason != result.reason;
  if (reason_changed || result.ocr_executed || result.accepted ||
      reread_offer || flash_pick || emit_always) {
    std::string output{"{\"type\":\"frame_result\","};
    output.append("\"captured_at_utc\":");
    if (frame.timestamps.captured_at_utc.has_value()) {
      AppendJsonString(output,
                       lol_assistant::output::detail::FormatUtcTimestamp(
                           *frame.timestamps.captured_at_utc));
    } else {
      output.append("null");
    }
    output.append(",\"emitted_at_utc\":");
    AppendJsonString(output,
                     lol_assistant::output::detail::FormatUtcTimestamp(
                         lol_assistant::common::UtcTimestamp::clock::now()));
    output.push_back(',');
    const std::vector<FrameDiagnostic> frame_diagnostic{DescribeFrame(frame)};
    AppendFrames(output, frame_diagnostic);
    output.append(",\"static_replay\":");
    output.append(static_replay.has_value() ? "true" : "false");
    if (static_replay.has_value()) {
      output.append(",\"static_source_frame_id\":");
      output.append(std::to_string(static_replay->source_frame_id));
      output.append(",\"static_pass_index\":");
      output.append(std::to_string(static_replay->pass_index));
      output.append(",\"static_pass_count\":");
      output.append(std::to_string(static_replay->pass_count));
    }
    output.append(",\"raw_detector\":");
    AppendDetectorResult(output, result.raw_detector);
    output.append(",\"stable_detector\":");
    AppendDetectorResult(output, result.stable_detector);
    output.append(",\"ocr_executed\":");
    output.append(result.ocr_executed ? "true" : "false");
    output.append(",\"accepted\":");
    output.append(result.accepted ? "true" : "false");
    output.append(",\"duplicate\":");
    output.append(result.duplicate ? "true" : "false");
    output.append(",\"force_recognition\":");
    output.append(result.force_recognition ? "true" : "false");
    output.append(",\"detector_stability_bypassed\":");
    output.append(result.detector_stability_bypassed ? "true" : "false");
    output.append(",\"recommended_processing_hz\":");
    output.append(std::to_string(result.recommended_processing_hz));
    output.append(",\"vision_processing_latency_ms\":");
    output.append(std::to_string(vision_processing_latency_ms));
    output.append(",\"reread_offer\":");
    output.append(reread_offer ? "true" : "false");
    output.append(",\"reread_cause\":");
    AppendJsonString(output, pipeline.snapshot_cause);
    output.append(",\"reason\":");
    AppendJsonString(output, result.reason);
    output.append(",\"rois\":");
    AppendThreeCardRois(output, result.rois);
    output.append(",\"recognition_debug\":");
    AppendRecognitionDebug(output, result.recognition);
    output.append(",\"hover_slot\":null");
    output.append(",\"flash_pick\":");
    output.append(flash_pick ? "true" : "false");
    output.push_back('}');
    std::cout << output << '\n';
    std::cout.flush();
  }
  pipeline.previous_reason = result.reason;

  if (result.accepted) {
    if (!result.offer_json.has_value()) {
      throw std::runtime_error(
          "Frame processor accepted an offer without offer JSON");
    }
    std::cout << *result.offer_json;
    if (result.offer_json->empty() || result.offer_json->back() != '\n') {
      std::cout << '\n';
    }
    std::cout.flush();
  }

  if (force_recognition && pipeline.force_recognition.controller != nullptr) {
    EmitForceRecognitionEvents(
        pipeline, pipeline.force_recognition.controller->ObserveFrame(
                      true, result.accepted, result.duplicate,
                      std::chrono::steady_clock::now()));
  }

  if (pipeline.preview != nullptr && pipeline.preview->IsOpen()) {
    lol_assistant::output::DebugPreviewOverlay overlay;
    overlay.fps = static_cast<double>(result.recommended_processing_hz);
    overlay.status = result.reason;
    pipeline.preview->UpdateFrame(frame, overlay);
    if (!pipeline.preview->PumpMessages()) {
      pipeline.preview->Close();
    }
  }

  if (force_recognition &&
      ForceRecognitionActive(pipeline, std::chrono::steady_clock::now())) {
    return std::max(result.recommended_processing_hz, 20U);
  }
  return result.recommended_processing_hz;
}

void PumpPreview(Phase1Pipeline &pipeline) {
  if (pipeline.preview != nullptr && pipeline.preview->IsOpen() &&
      !pipeline.preview->PumpMessages()) {
    pipeline.preview->Close();
  }
}

[[nodiscard]] int ClosePipeline(Phase1Pipeline &pipeline,
                                const std::string_view status,
                                const std::string_view source_summary) {
  if (pipeline.preview != nullptr) {
    pipeline.preview->Close();
  }
  if (pipeline.runtime == nullptr) {
    return 0;
  }

  const std::string session_id = pipeline.runtime->SessionId();
  lol_assistant::app::SessionRuntimeStatus close_status;
  try {
    close_status = pipeline.runtime->Close();
  } catch (const std::exception &error) {
    close_status.code = lol_assistant::app::SessionRuntimeError::StorageError;
    close_status.message = error.what();
  } catch (...) {
    close_status.code = lol_assistant::app::SessionRuntimeError::StorageError;
    close_status.message = "Unknown exception while closing session runtime";
  }

  std::string output{"{\"type\":\"session_end\",\"session_id\":"};
  AppendJsonString(output, session_id);
  output.append(",\"status\":");
  AppendJsonString(output, status);
  output.append(",\"frames_processed\":");
  output.append(std::to_string(pipeline.frames_processed));
  output.append(",\"close_ok\":");
  output.append(close_status.IsSuccess() ? "true" : "false");
  if (!close_status.IsSuccess()) {
    output.append(",\"close_error\":");
    AppendJsonString(output, close_status.message);
  }
  output.append(",\"source_summary\":");
  output.append(source_summary);
  output.append(",\"collection\":{\"saved\":");
  output.append(std::to_string(pipeline.collection.counters.saved));
  output.append(",\"duplicates\":");
  output.append(std::to_string(pipeline.collection.counters.duplicates));
  output.append(",\"errors\":");
  output.append(std::to_string(pipeline.collection.counters.errors));
  output.append(",\"manual\":");
  output.append(std::to_string(pipeline.collection.counters.manual));
  output.append(",\"auto\":");
  output.append(std::to_string(pipeline.collection.counters.automatic));
  output.push_back('}');
  output.push_back('}');
  std::cout << output << '\n';
  return close_status.IsSuccess() ? 0 : kSourceErrorExitCode;
}

[[nodiscard]] int CloseAfterException(Phase1Pipeline &pipeline) noexcept {
  try {
    return ClosePipeline(pipeline, "error", "{}");
  } catch (...) {
    return kSourceErrorExitCode;
  }
}

[[nodiscard]] int RunReplay(const ApplicationOptions &options) {
  Phase1Pipeline pipeline;
  try {
    lol_assistant::replay::ImageReplaySource source{
        options.replay_path, ReplayInputKind(options.replay_kind)};
    const auto source_description = source.Source();
    InitializePipeline(options, "replay", source_description.id,
                       lol_assistant::collection::SampleKind::Unknown, "replay",
                       {}, pipeline);

    const auto started = std::chrono::steady_clock::now();
    const auto deadline =
        started +
        std::chrono::duration_cast<std::chrono::steady_clock::duration>(
            std::chrono::duration<double>{options.max_seconds});
    const bool static_replay = options.replay_kind == ReplayKind::Image;
    bool stopped_after_once = false;
    bool replay_completed = false;
    std::size_t static_passes_processed = 0U;

    if (static_replay) {
      const auto source_frame = source.TryGetNextFrame();
      if (!source_frame.has_value()) {
        throw std::runtime_error("Single-image replay produced no frame");
      }
      for (std::size_t pass_index = 0U;
           pass_index < lol_assistant::app::kStaticReplayPassCount &&
           std::chrono::steady_clock::now() < deadline;
           ++pass_index) {
        const auto pass_offset = static_cast<std::uint64_t>(pass_index);
        if (source_frame->frame_id >
            std::numeric_limits<std::uint64_t>::max() - pass_offset) {
          throw std::runtime_error("Static replay frame ID overflow");
        }
        lol_assistant::common::Frame analysis_frame = *source_frame;
        analysis_frame.frame_id = source_frame->frame_id + pass_offset;
        static_cast<void>(
            ProcessFrame(pipeline, analysis_frame,
                         StaticReplayDiagnostic{
                             source_frame->frame_id, pass_index,
                             lol_assistant::app::kStaticReplayPassCount}));
        ++static_passes_processed;
        if (lol_assistant::app::ShouldStopAfterRecognitionAttempt(
                options.once, pipeline.recognition_progress)) {
          stopped_after_once = true;
          break;
        }
      }
      replay_completed =
          static_passes_processed == lol_assistant::app::kStaticReplayPassCount;
    } else {
      while (std::chrono::steady_clock::now() < deadline) {
        auto frame = source.TryGetNextFrame();
        if (!frame.has_value()) {
          break;
        }
        static_cast<void>(ProcessFrame(pipeline, *frame));
        if (lol_assistant::app::ShouldStopAfterRecognitionAttempt(
                options.once, pipeline.recognition_progress)) {
          stopped_after_once = true;
          break;
        }
      }
      replay_completed = source.CurrentIndex() >= source.FrameCount();
    }
    const bool exhausted = source.CurrentIndex() >= source.FrameCount();
    const auto stop_reason =
        stopped_after_once
            ? lol_assistant::app::SessionStopReason::RecognitionAttemptCompleted
        : replay_completed
            ? lol_assistant::app::SessionStopReason::InputExhausted
            : lol_assistant::app::SessionStopReason::Timeout;
    const char *status = lol_assistant::app::ToString(
        lol_assistant::app::ResolveRecognitionSessionStatus(
            stop_reason, pipeline.recognition_progress));

    std::string summary{"{\"kind\":\"replay\",\"input_kind\":"};
    AppendJsonString(summary,
                     lol_assistant::app::ToString(options.replay_kind));
    summary.append(",\"id\":");
    AppendJsonString(summary, source_description.id);
    summary.append(",\"available_frames\":");
    summary.append(std::to_string(source.FrameCount()));
    summary.append(",\"exhausted\":");
    summary.append(exhausted ? "true" : "false");
    summary.append(",\"static_replay\":");
    summary.append(static_replay ? "true" : "false");
    if (static_replay) {
      summary.append(",\"static_passes_processed\":");
      summary.append(std::to_string(static_passes_processed));
      summary.append(",\"static_pass_count\":");
      summary.append(
          std::to_string(lol_assistant::app::kStaticReplayPassCount));
    }
    summary.append(",\"stable_observation_seen\":");
    summary.append(pipeline.recognition_progress.stable_observation_seen
                       ? "true"
                       : "false");
    summary.append(",\"ocr_executed\":");
    summary.append(pipeline.recognition_progress.ocr_executed ? "true"
                                                              : "false");
    summary.append(",\"accepted_offer\":");
    summary.append(pipeline.recognition_progress.accepted_offer ? "true"
                                                                : "false");
    summary.append(",\"recognition_attempt_completed\":");
    summary.append(pipeline.recognition_progress.recognition_attempt_completed
                       ? "true}"
                       : "false}");
    return ClosePipeline(pipeline, status, summary);
  } catch (const std::exception &error) {
    static_cast<void>(CloseAfterException(pipeline));
    std::cerr << ErrorJson("pipeline_error", error.what()) << '\n';
    return kSourceErrorExitCode;
  } catch (...) {
    static_cast<void>(CloseAfterException(pipeline));
    std::cerr << ErrorJson("pipeline_error", "Unknown replay exception")
              << '\n';
    return kSourceErrorExitCode;
  }
}

[[nodiscard]] std::optional<std::uintptr_t>
ResolveLiveWindow(const ApplicationOptions &options, std::string &error,
                  std::vector<WindowCandidate> &candidates) {
  const auto enumerate_candidates = [] {
    const auto windows = lol_assistant::capture::EnumerateTopLevelWindows();
    std::vector<WindowCandidate> snapshot;
    snapshot.reserve(windows.size());
    for (const auto &window : windows) {
      snapshot.push_back(WindowCandidate{
          reinterpret_cast<std::uintptr_t>(window.handle), window.title, true});
    }
    return snapshot;
  };

  auto snapshot = enumerate_candidates();
  auto selection = options.source_kind == SourceKind::WindowHandle
                       ? lol_assistant::app::SelectWindowByHandle(
                             options.window_handle, snapshot)
                       : lol_assistant::app::SelectWindowByTitle(
                             options.window_title, snapshot);
  error = std::move(selection.error);
  candidates = std::move(selection.candidates);
  if (!selection.ok()) {
    return std::nullopt;
  }

  auto confirmation = lol_assistant::app::SelectWindowByHandle(
      *selection.handle, enumerate_candidates());
  if (!confirmation.ok()) {
    error = "Selected game window changed or disappeared before capture: " +
            confirmation.error;
    candidates = std::move(confirmation.candidates);
    return std::nullopt;
  }
  candidates = std::move(confirmation.candidates);
  return confirmation.handle;
}

[[nodiscard]] lol_assistant::collection::WindowMetadata
LiveWindowMetadata(const std::uintptr_t handle) noexcept {
  lol_assistant::collection::WindowMetadata metadata;
  try {
    metadata.id = HandleText(handle);
    const HWND target = reinterpret_cast<HWND>(handle);
    DWORD process_id = 0U;
    static_cast<void>(GetWindowThreadProcessId(target, &process_id));
    if (process_id != 0U) {
      metadata.process_id = process_id;
    }
    for (const auto &window :
         lol_assistant::capture::EnumerateTopLevelWindows()) {
      if (window.handle == target) {
        metadata.title = WideToUtf8(window.title);
        if (window.process_id != 0U) {
          metadata.process_id = window.process_id;
        }
        break;
      }
    }
  } catch (...) {
    metadata.title.reset();
  }
  return metadata;
}

struct ObservedLiveFrame final {
  lol_assistant::common::CapturedFrame captured{};
  lol_assistant::app::FrameFreshnessObservation freshness{};
  LiveFrameContext context{};
};

struct WgcRestartState final {
  bool pending{false};
  std::size_t attempt_index{0U};
  std::optional<std::chrono::steady_clock::time_point> due{};
  std::optional<std::chrono::steady_clock::time_point>
      recovery_validation_deadline{};
  lol_assistant::app::RestartAttemptWindow attempts{};
};

struct DesktopRestartState final {
  bool pending{false};
  std::size_t attempt_index{0U};
  std::optional<std::chrono::steady_clock::time_point> due{};
  lol_assistant::app::RestartAttemptWindow attempts{};
};

struct LiveCaptureState final {
  CaptureBackend backend{CaptureBackend::Auto};
  lol_assistant::app::FrameFreshnessGate freshness_gate{};
  lol_assistant::app::CaptureHealthSnapshot health{};
  std::optional<lol_assistant::app::FrameFreshnessObservation> latest_wgc{};
  std::optional<lol_assistant::app::FrameFreshnessObservation> latest_desktop{};
  std::optional<std::chrono::steady_clock::time_point>
      wgc_last_content_change{};
  std::optional<std::chrono::steady_clock::time_point>
      desktop_last_content_change{};
  std::optional<std::chrono::steady_clock::time_point>
      desktop_last_physical_progress{};
  std::uint64_t wgc_epoch{0U};
  bool wgc_started{false};
  bool desktop_started{false};
  lol_assistant::app::CaptureTargetVisibility target_visibility{
      lol_assistant::app::CaptureTargetVisibility::Visible};
  bool target_visibility_initialized{false};
  lol_assistant::app::PendingPhysicalFrameBuffer pending_frames{};
  WgcRestartState restart{};
  DesktopRestartState desktop_restart{};
  std::uint64_t health_event_seq{0U};
  std::optional<std::chrono::steady_clock::time_point> last_health_emit{};
  std::string health_signature{};
};

[[nodiscard]] lol_assistant::app::PendingPhysicalFrame
PendingFromObserved(ObservedLiveFrame &&observed) {
  lol_assistant::app::PendingPhysicalFrame pending;
  pending.captured = std::move(observed.captured);
  pending.freshness = std::move(observed.freshness);
  pending.freshness.process_allowed = true;
  return pending;
}

[[nodiscard]] ObservedLiveFrame
ObserveLiveFrame(lol_assistant::app::FrameFreshnessGate &gate,
                 lol_assistant::common::CapturedFrame captured,
                 const std::string_view source,
                 const std::chrono::steady_clock::time_point now) {
  lol_assistant::app::FrameTransportInput input;
  input.source.assign(source);
  input.epoch = captured.identity.capture_epoch;
  if (captured.frame.timestamps.source_timestamp.has_value()) {
    input.source_time = captured.frame.timestamps.source_timestamp->count();
  }
  input.content_hash = TransportContentHash(captured.frame);
  input.progress = captured.identity.frame_id;
  input.observed_at = now;

  ObservedLiveFrame observed;
  observed.context =
      LiveFrameContext{input.source, input.epoch, input.content_hash, now};
  observed.freshness = gate.Observe(input);
  observed.captured = std::move(captured);
  return observed;
}

[[nodiscard]] std::optional<lol_assistant::app::FrameFreshnessObservation>
RecentObservation(
    const std::optional<lol_assistant::app::FrameFreshnessObservation> &value,
    const std::chrono::steady_clock::time_point now) {
  if (!value.has_value() || !lol_assistant::app::IsRecentPhysicalObservation(
                                *value, now, kCaptureFreshTimeout)) {
    return std::nullopt;
  }
  return value;
}

[[nodiscard]] const char *
UpperHealthState(const lol_assistant::app::CaptureHealthState state) noexcept {
  switch (state) {
  case lol_assistant::app::CaptureHealthState::Healthy:
    return "HEALTHY";
  case lol_assistant::app::CaptureHealthState::Suspect:
    return "SUSPECT";
  case lol_assistant::app::CaptureHealthState::DesktopFallback:
    return "DESKTOP_FALLBACK";
  case lol_assistant::app::CaptureHealthState::Degraded:
    return "DEGRADED";
  default:
    return "DEGRADED";
  }
}

[[nodiscard]] std::string
HealthSignature(const lol_assistant::app::CaptureHealthSnapshot &health,
                const WgcRestartState &restart,
                const DesktopRestartState &desktop_restart) {
  std::string signature{lol_assistant::app::ToString(health.state)};
  signature.push_back('|');
  signature.append(lol_assistant::app::ToString(health.reason));
  signature.push_back('|');
  signature.append(health.selected_source.value_or("none"));
  signature.push_back('|');
  signature.append(std::to_string(health.selected_epoch.value_or(0U)));
  signature.append(restart.pending ? "|restart_pending" : "|restart_idle");
  signature.append(restart.attempts.exhausted() ? "|restart_exhausted"
                                                : "|restart_available");
  signature.append(desktop_restart.pending ? "|desktop_restart_pending"
                                           : "|desktop_restart_idle");
  signature.append(desktop_restart.attempts.exhausted()
                       ? "|desktop_restart_exhausted"
                       : "|desktop_restart_available");
  return signature;
}

[[nodiscard]] std::string
AddBridgeHealthFields(std::string json,
                      const lol_assistant::app::CaptureHealthSnapshot &capture,
                      const bool invalidate_recommendation) {
  const bool healthy_active_fallback =
      !invalidate_recommendation && capture.selected_source.has_value() &&
      *capture.selected_source == "desktop" &&
      (capture.state ==
           lol_assistant::app::CaptureHealthState::DesktopFallback ||
       capture.state == lol_assistant::app::CaptureHealthState::Degraded);
  const char *const bridge_state =
      healthy_active_fallback ? "HEALTHY" : UpperHealthState(capture.state);
  const bool stale =
      invalidate_recommendation || !capture.selected_source.has_value() ||
      capture.state == lol_assistant::app::CaptureHealthState::Suspect;

  std::string fields{",\"status\":"};
  AppendJsonString(fields, bridge_state);
  fields.append(",\"state\":");
  AppendJsonString(fields, bridge_state);
  fields.append(",\"reason\":");
  AppendJsonString(fields, lol_assistant::app::ToString(capture.reason));
  fields.append(",\"recommendation_freshness\":");
  AppendJsonString(fields, stale ? "stale" : "current");
  fields.append(",\"recommendation_invalidated\":");
  fields.append(invalidate_recommendation ? "true" : "false");
  fields.append(",\"active_capture_state\":");
  AppendJsonString(fields, UpperHealthState(capture.state));

  const std::size_t insertion = json.find(",\"schema_version\"");
  if (insertion == std::string::npos) {
    throw std::runtime_error("capture health serializer contract changed");
  }
  json.insert(insertion, fields);
  return json;
}

void EmitCaptureHealth(Phase1Pipeline &pipeline, LiveCaptureState &live,
                       const std::chrono::steady_clock::time_point now,
                       const bool invalidate_recommendation, const bool force) {
  if (pipeline.control_writer == nullptr || pipeline.runtime == nullptr) {
    return;
  }
  if (!force && live.last_health_emit.has_value() &&
      now - *live.last_health_emit < kCaptureHealthInterval) {
    return;
  }

  lol_assistant::app::HealthJsonSnapshot snapshot;
  snapshot.session_id = pipeline.runtime->SessionId();
  snapshot.event_seq = ++live.health_event_seq;
  snapshot.monotonic_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                              now.time_since_epoch())
                              .count();
  snapshot.capture = live.health;
  if (const auto wgc = live.freshness_gate.Snapshot("wgc", now);
      wgc.has_value()) {
    snapshot.sources.push_back(*wgc);
  }
  if (const auto desktop = live.freshness_gate.Snapshot("desktop", now);
      desktop.has_value()) {
    snapshot.sources.push_back(*desktop);
  }
  if (pipeline.force_recognition.controller != nullptr) {
    snapshot.force = pipeline.force_recognition.controller->Snapshot(now);
  }
  if (pipeline.sample_policy != nullptr) {
    snapshot.samples = pipeline.sample_policy->Snapshot();
  }
  const std::string json =
      AddBridgeHealthFields(lol_assistant::app::SerializeHealthJson(snapshot),
                            live.health, invalidate_recommendation);
  if (!pipeline.control_writer->Write(json)) {
    throw std::runtime_error("Unable to flush capture health JSONL event");
  }
  live.last_health_emit = now;
  pipeline.control_state_dirty = false;
}

void RequestWgcRestart(LiveCaptureState &live,
                       const std::chrono::steady_clock::time_point now) {
  if (live.backend == CaptureBackend::Desktop || live.restart.pending) {
    return;
  }
  if (!live.restart.attempts.CanSchedule(now)) {
    live.restart.recovery_validation_deadline.reset();
    return;
  }
  constexpr std::array backoff{std::chrono::milliseconds{0},
                               std::chrono::milliseconds{250},
                               std::chrono::milliseconds{1'000}};
  live.restart.pending = true;
  live.restart.attempt_index = live.restart.attempts.attempts_in_window();
  live.restart.due = now + backoff[live.restart.attempt_index];
  live.restart.recovery_validation_deadline.reset();
}

void ServiceWgcRestart(
    LiveCaptureState &live,
    lol_assistant::capture::WindowsGraphicsCaptureSource *const source,
    const std::chrono::steady_clock::time_point now) {
  if (!live.restart.pending || !live.restart.due.has_value() ||
      now < *live.restart.due || source == nullptr) {
    return;
  }
  if (!live.restart.attempts.TryConsume(now) ||
      live.restart.attempt_index >= 3U) {
    live.restart.pending = false;
    live.restart.due.reset();
    live.restart.attempts.MarkExhausted();
    return;
  }

  source->Stop();
  live.wgc_started = false;
  live.latest_wgc.reset();
  live.wgc_last_content_change.reset();
  std::string restart_error;
  if (source->Start(&restart_error)) {
    live.wgc_started = true;
    live.wgc_epoch = source->CurrentCaptureEpoch();
    live.restart.pending = false;
    live.restart.due.reset();
    live.restart.recovery_validation_deadline = now + std::chrono::seconds{2};
    return;
  }

  ++live.restart.attempt_index;
  if (live.restart.attempt_index >= 3U ||
      live.restart.attempts.attempts_in_window() >= 3U) {
    live.restart.pending = false;
    live.restart.due.reset();
    live.restart.attempts.MarkExhausted();
    live.restart.recovery_validation_deadline.reset();
    return;
  }
  constexpr std::array backoff{std::chrono::milliseconds{0},
                               std::chrono::milliseconds{250},
                               std::chrono::milliseconds{1'000}};
  live.restart.due = now + backoff[live.restart.attempt_index];
}

void RequestDesktopRestart(LiveCaptureState &live,
                           const std::chrono::steady_clock::time_point now) {
  if (live.backend == CaptureBackend::Wgc || live.desktop_restart.pending) {
    return;
  }
  if (!live.desktop_restart.attempts.CanSchedule(now)) {
    return;
  }
  constexpr std::array backoff{std::chrono::milliseconds{0},
                               std::chrono::milliseconds{250},
                               std::chrono::milliseconds{1'000}};
  live.desktop_restart.pending = true;
  live.desktop_restart.attempt_index =
      live.desktop_restart.attempts.attempts_in_window();
  live.desktop_restart.due = now + backoff[live.desktop_restart.attempt_index];
}

void ServiceDesktopRestart(
    LiveCaptureState &live,
    lol_assistant::capture::DesktopDuplicationSource *const source,
    const std::chrono::steady_clock::time_point now) {
  if (!live.desktop_restart.pending || !live.desktop_restart.due.has_value() ||
      now < *live.desktop_restart.due || source == nullptr) {
    return;
  }
  if (!live.desktop_restart.attempts.TryConsume(now) ||
      live.desktop_restart.attempt_index >= 3U) {
    live.desktop_restart.pending = false;
    live.desktop_restart.due.reset();
    live.desktop_restart.attempts.MarkExhausted();
    return;
  }

  source->Stop();
  live.desktop_started = false;
  live.latest_desktop.reset();
  live.desktop_last_content_change.reset();
  live.desktop_last_physical_progress.reset();
  live.pending_frames.Invalidate();
  std::string restart_error;
  if (source->Start(&restart_error)) {
    live.desktop_started = true;
    live.desktop_last_physical_progress = now;
    live.desktop_restart.pending = false;
    live.desktop_restart.due.reset();
    return;
  }

  ++live.desktop_restart.attempt_index;
  if (live.desktop_restart.attempt_index >= 3U ||
      live.desktop_restart.attempts.attempts_in_window() >= 3U) {
    live.desktop_restart.pending = false;
    live.desktop_restart.due.reset();
    live.desktop_restart.attempts.MarkExhausted();
    return;
  }
  constexpr std::array backoff{std::chrono::milliseconds{0},
                               std::chrono::milliseconds{250},
                               std::chrono::milliseconds{1'000}};
  live.desktop_restart.due = now + backoff[live.desktop_restart.attempt_index];
}

[[nodiscard]] lol_assistant::app::CaptureHealthSnapshot
DecideCaptureHealth(LiveCaptureState &live,
                    const std::chrono::steady_clock::time_point now) {
  lol_assistant::app::LiveCaptureDecisionInput input;
  input.policy =
      live.backend == CaptureBackend::Wgc
          ? lol_assistant::app::LiveCapturePolicy::WgcOnly
      : live.backend == CaptureBackend::Desktop
          ? lol_assistant::app::LiveCapturePolicy::DesktopOnly
          : lol_assistant::app::LiveCapturePolicy::AutoDesktopAuthoritative;
  input.target_visibility = live.target_visibility;
  input.wgc_backend_available = live.wgc_started;
  input.desktop_backend_available = live.desktop_started;
  input.latest_wgc = live.latest_wgc;
  input.latest_desktop = live.latest_desktop;
  input.previous = live.health;
  input.now = now;
  input.maximum_observation_age = kCaptureFreshTimeout;
  auto decision = lol_assistant::app::DecideLiveCapture(input);

  const auto wgc = RecentObservation(live.latest_wgc, now);
  const auto desktop = RecentObservation(live.latest_desktop, now);

  if (live.restart.pending &&
      decision.selected_source == std::optional<std::string>{"desktop"}) {
    decision.reason = lol_assistant::app::HealthReason::WgcRecovering;
  }
  if (live.restart.attempts.exhausted() &&
      live.target_visibility ==
          lol_assistant::app::CaptureTargetVisibility::Visible) {
    decision.state = lol_assistant::app::CaptureHealthState::Degraded;
    decision.reason = lol_assistant::app::HealthReason::WgcUnavailable;
    if (desktop.has_value()) {
      decision.selected_source = "desktop";
      decision.selected_epoch = desktop->input.epoch;
    } else {
      decision.selected_source.reset();
      decision.selected_epoch.reset();
    }
  }
  const bool current_wgc_has_strong_proof =
      wgc.has_value() && wgc->input.source_time.has_value();
  if (current_wgc_has_strong_proof) {
    live.restart.recovery_validation_deadline.reset();
  } else if (live.restart.recovery_validation_deadline.has_value()) {
    decision.request_wgc_restart =
        now >= *live.restart.recovery_validation_deadline;
  }
  decision.switched_source =
      live.health.selected_source != decision.selected_source ||
      live.health.selected_epoch != decision.selected_epoch;
  decision.revoke_recommendation =
      decision.revoke_recommendation ||
      (live.health.selected_source.has_value() && decision.switched_source);
  return decision;
}

[[nodiscard]] bool
ApplyCaptureHealth(Phase1Pipeline &pipeline, LiveCaptureState &live,
                   lol_assistant::app::CaptureHealthSnapshot decision,
                   const std::chrono::steady_clock::time_point now) {
  if (decision.request_wgc_restart) {
    RequestWgcRestart(live, now);
  }
  const std::string signature =
      HealthSignature(decision, live.restart, live.desktop_restart);
  const bool changed = signature != live.health_signature;
  const bool invalidated = decision.revoke_recommendation ||
                           decision.switched_source ||
                           (changed && !decision.selected_source.has_value());
  live.health = std::move(decision);
  live.health_signature = signature;
  pipeline.active_live_source = live.health.selected_source;
  EmitCaptureHealth(pipeline, live, now, invalidated,
                    changed || pipeline.control_state_dirty);
  return changed;
}

constexpr auto kIdleLoopSleep = std::chrono::milliseconds{50};
constexpr auto kBusyLoopSleep = std::chrono::milliseconds{2};
constexpr auto kCaptureHoldAfterUse = std::chrono::milliseconds{800};
constexpr auto kEnterCaptureWarmup = std::chrono::milliseconds{200};

void StopLiveCaptureBackends(
    LiveCaptureState &live,
    lol_assistant::capture::WindowsGraphicsCaptureSource *wgc,
    lol_assistant::capture::DesktopDuplicationSource *desktop) {
  live.restart.pending = false;
  live.restart.due.reset();
  live.desktop_restart.pending = false;
  live.desktop_restart.due.reset();
  if (wgc != nullptr && live.wgc_started) {
    wgc->Stop();
    live.wgc_started = false;
  }
  if (desktop != nullptr && live.desktop_started) {
    desktop->Stop();
    live.desktop_started = false;
  }
  live.latest_wgc.reset();
  live.latest_desktop.reset();
  live.wgc_last_content_change.reset();
  live.desktop_last_content_change.reset();
  live.desktop_last_physical_progress.reset();
  live.pending_frames.Invalidate();
}

void StartCaptureIfNeeded(
    LiveCaptureState &live,
    lol_assistant::capture::WindowsGraphicsCaptureSource *wgc,
    lol_assistant::capture::DesktopDuplicationSource *desktop) {
  if (live.wgc_started || live.desktop_started) {
    return;
  }
  if (wgc != nullptr) {
    std::string error;
    if (wgc->Start(&error)) {
      live.wgc_started = true;
      live.wgc_epoch = wgc->CurrentCaptureEpoch();
      return;
    }
  }
  if (desktop != nullptr) {
    std::string error;
    if (desktop->Start(&error)) {
      live.desktop_started = true;
    }
  }
}

class GlobalLeftClickWatcher final {
 public:
  GlobalLeftClickWatcher() = default;
  ~GlobalLeftClickWatcher() { Stop(); }
  GlobalLeftClickWatcher(const GlobalLeftClickWatcher &) = delete;
  GlobalLeftClickWatcher &operator=(const GlobalLeftClickWatcher &) = delete;

  void Start(std::filesystem::path flag_path = {}, HWND = nullptr) {
    if (thread_.joinable()) {
      return;
    }
    flag_path_ = std::move(flag_path);
    stop_.store(false, std::memory_order_release);
    pending_.store(false, std::memory_order_release);
    thread_ = std::thread([this] { ThreadMain(); });
  }

  void Stop() {
    stop_.store(true, std::memory_order_release);
    if (thread_.joinable()) {
      thread_.join();
    }
  }

  [[nodiscard]] bool ConsumeClick() noexcept {
    return pending_.exchange(false, std::memory_order_acq_rel);
  }

 private:
  static LRESULT CALLBACK WndProc(HWND hwnd, UINT message, WPARAM wparam,
                                  LPARAM lparam) {
    if (message == WM_INPUT) {
      auto *self = reinterpret_cast<GlobalLeftClickWatcher *>(
          GetWindowLongPtrW(hwnd, GWLP_USERDATA));
      if (self != nullptr) {
        self->OnRawInput(lparam);
      }
      return 0;
    }
    return DefWindowProcW(hwnd, message, wparam, lparam);
  }

  void OnRawInput(LPARAM lparam) {
    RAWINPUT raw{};
    UINT size = sizeof(raw);
    if (GetRawInputData(reinterpret_cast<HRAWINPUT>(lparam), RID_INPUT, &raw,
                        &size, sizeof(RAWINPUTHEADER)) ==
        static_cast<UINT>(-1)) {
      return;
    }
    if (raw.header.dwType != RIM_TYPEMOUSE) {
      return;
    }
    if ((raw.data.mouse.usButtonFlags & RI_MOUSE_LEFT_BUTTON_DOWN) != 0U) {
      NoteLeftClick();
    }
  }

  void NoteLeftClick() noexcept {
    pending_.store(true, std::memory_order_release);
  }

  void ObserveKeyState() noexcept {
    const bool down = (GetAsyncKeyState(VK_LBUTTON) & 0x8000) != 0;
    if (down && !key_down_) {
      pending_.store(true, std::memory_order_release);
    }
    key_down_ = down;
  }

  void ObserveClickEvent() {
    if (click_event_ == nullptr) {
      return;
    }
    if (WaitForSingleObject(click_event_, 0) == WAIT_OBJECT_0) {
      pending_.store(true, std::memory_order_release);
    }
  }

  void ObserveFlagFile() {
    if (flag_path_.empty()) {
      return;
    }
    std::ifstream input(flag_path_);
    if (!input) {
      return;
    }
    std::uint64_t token = 0U;
    input >> token;
    if (!input || token == 0U || token == last_flag_token_) {
      return;
    }
    last_flag_token_ = token;
    pending_.store(true, std::memory_order_release);
  }

  void PrimeFlagToken() {
    if (flag_path_.empty()) {
      return;
    }
    std::ifstream input(flag_path_);
    std::uint64_t token = 0U;
    if (input >> token) {
      last_flag_token_ = token;
    }
  }

  void ThreadMain() {
    PrimeFlagToken();
    key_down_ = (GetAsyncKeyState(VK_LBUTTON) & 0x8000) != 0;
    wchar_t class_name[64]{};
    swprintf_s(class_name, L"LolAssistantRawLeftClickSink_%lu",
               static_cast<unsigned long>(GetCurrentProcessId()));
    WNDCLASSW window_class{};
    window_class.lpfnWndProc = &WndProc;
    window_class.hInstance = GetModuleHandleW(nullptr);
    window_class.lpszClassName = class_name;
    const ATOM atom = RegisterClassW(&window_class);
    HWND hwnd = nullptr;
    bool raw_registered = false;
    if (atom != 0 || GetLastError() == ERROR_CLASS_ALREADY_EXISTS) {
      hwnd = CreateWindowExW(0, class_name, L"", 0, 0, 0, 0, 0, HWND_MESSAGE,
                             nullptr, window_class.hInstance, nullptr);
    }
    if (hwnd != nullptr) {
      SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(this));
      RAWINPUTDEVICE device{};
      device.usUsagePage = 0x01;
      device.usUsage = 0x02;
      // INPUTSINK only: EXINPUTSINK skips delivery when the foreground
      // game already registered the mouse, which League does.
      device.dwFlags = RIDEV_INPUTSINK;
      device.hwndTarget = hwnd;
      raw_registered =
          RegisterRawInputDevices(&device, 1, sizeof(device)) != FALSE;
      if (!raw_registered) {
        DestroyWindow(hwnd);
        hwnd = nullptr;
      }
    }
    click_event_ =
        CreateEventW(nullptr, FALSE, FALSE, L"Local\\LoLAssistantLeftClick");
    timeBeginPeriod(1);
    while (!stop_.load(std::memory_order_acquire)) {
      ObserveKeyState();
      ObserveFlagFile();
      ObserveClickEvent();
      if (hwnd != nullptr) {
        const DWORD wait = MsgWaitForMultipleObjects(
            click_event_ != nullptr ? 1U : 0U,
            click_event_ != nullptr ? &click_event_ : nullptr, FALSE, 1,
            QS_ALLINPUT | QS_RAWINPUT);
        if (wait == WAIT_OBJECT_0 && click_event_ != nullptr) {
          pending_.store(true, std::memory_order_release);
        }
        MSG message{};
        while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE) != FALSE) {
          TranslateMessage(&message);
          DispatchMessageW(&message);
        }
      } else {
        Sleep(1);
      }
    }
    timeEndPeriod(1);
    if (click_event_ != nullptr) {
      CloseHandle(click_event_);
      click_event_ = nullptr;
    }
    if (raw_registered) {
      RAWINPUTDEVICE device{};
      device.usUsagePage = 0x01;
      device.usUsage = 0x02;
      device.dwFlags = RIDEV_REMOVE;
      static_cast<void>(RegisterRawInputDevices(&device, 1, sizeof(device)));
    }
    if (hwnd != nullptr) {
      DestroyWindow(hwnd);
    }
    if (atom != 0) {
      UnregisterClassW(class_name, window_class.hInstance);
    }
  }

  std::filesystem::path flag_path_{};
  std::uint64_t last_flag_token_{0U};
  HANDLE click_event_{nullptr};
  bool key_down_{false};
  std::atomic<bool> pending_{false};
  std::atomic<bool> stop_{false};
  std::thread thread_{};
};

void KeepCaptureResponsiveInBackground() {
  SetPriorityClass(GetCurrentProcess(), NORMAL_PRIORITY_CLASS);
#if defined(PROCESS_POWER_THROTTLING_CURRENT_VERSION)
  PROCESS_POWER_THROTTLING_STATE power{};
  power.Version = PROCESS_POWER_THROTTLING_CURRENT_VERSION;
  power.ControlMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED;
  power.StateMask = 0;
  SetProcessInformation(GetCurrentProcess(), ProcessPowerThrottling, &power,
                        sizeof(power));
#endif
  const HANDLE stdin_handle = GetStdHandle(STD_INPUT_HANDLE);
  if (stdin_handle != nullptr && stdin_handle != INVALID_HANDLE_VALUE) {
    DWORD mode = 0;
    if (GetConsoleMode(stdin_handle, &mode) != FALSE) {
      mode &= ~ENABLE_QUICK_EDIT_MODE;
      mode |= ENABLE_EXTENDED_FLAGS;
      SetConsoleMode(stdin_handle, mode);
    }
  }
  if (const HWND console = GetConsoleWindow()) {
    ShowWindow(console, SW_HIDE);
  }
}

[[nodiscard]] int RunLive(const ApplicationOptions &options) {
  KeepCaptureResponsiveInBackground();
  std::string resolve_error;
  std::vector<WindowCandidate> candidates;
  const auto handle = ResolveLiveWindow(options, resolve_error, candidates);
  if (!handle.has_value()) {
    std::cerr << ErrorJson("source_error", resolve_error, candidates, true)
              << '\n';
    return kSourceErrorExitCode;
  }

  const HWND target = reinterpret_cast<HWND>(*handle);
  const bool wants_wgc = options.capture_backend != CaptureBackend::Desktop;
  const bool wants_desktop = options.capture_backend != CaptureBackend::Wgc;
  lol_assistant::capture::DesktopCaptureTarget desktop_target;
  desktop_target.kind =
      lol_assistant::capture::DesktopCaptureTargetKind::WindowExtendedFrame;
  desktop_target.window = target;
  std::unique_ptr<lol_assistant::capture::WindowsGraphicsCaptureSource>
      wgc_source;
  std::unique_ptr<lol_assistant::capture::DesktopDuplicationSource>
      desktop_source;
  if (wants_wgc) {
    wgc_source =
        std::make_unique<lol_assistant::capture::WindowsGraphicsCaptureSource>(
            target);
  }
  if (wants_desktop) {
    desktop_source =
        std::make_unique<lol_assistant::capture::DesktopDuplicationSource>(
            desktop_target);
  }

  lol_assistant::app::FlushedJsonLineWriter control_writer{std::cout};
  lol_assistant::app::MayhemSelectionScheduler mayhem_scheduler{
      options.completed_offers};
  const auto emit_mayhem_events =
      [&](const std::vector<lol_assistant::app::MayhemSelectionEvent> &events,
          const std::string_view session_id,
          const std::chrono::steady_clock::time_point now) {
        static_cast<void>(now);
        for (const auto &event : events) {
          static_cast<void>(control_writer.Write(
              SerializeMayhemSelectionEvent(event, session_id)));
        }
      };
  std::mutex live_client_events_mutex;
  std::deque<lol_assistant::live_client::LiveClientEvent> live_client_events;
  std::string live_client_session_id;
  std::unique_ptr<lol_assistant::live_client::LiveClientPoller>
      live_client_poller;
  Phase1Pipeline pipeline;
  pipeline.control_writer = &control_writer;
  const auto drain_live_client_events = [&] {
    std::deque<lol_assistant::live_client::LiveClientEvent> pending;
    {
      std::lock_guard lock(live_client_events_mutex);
      pending.swap(live_client_events);
    }
    for (const auto &event : pending) {
      static_cast<void>(control_writer.Write(
          lol_assistant::live_client::SerializeLiveClientEventJson(
              event, live_client_session_id)));
      const auto now = std::chrono::steady_clock::now();
      const auto mayhem_events =
          mayhem_scheduler.Observe(event.snapshot, now);
      emit_mayhem_events(mayhem_events, live_client_session_id, now);
    }
  };
  std::mutex lcu_context_events_mutex;
  std::deque<lol_assistant::lcu::LcuContextEvent> lcu_context_events;
  std::string lcu_context_session_id;
  std::unique_ptr<lol_assistant::lcu::LcuContextPoller> lcu_context_poller;
  const auto drain_lcu_context_events = [&] {
    std::deque<lol_assistant::lcu::LcuContextEvent> pending;
    {
      std::lock_guard lock(lcu_context_events_mutex);
      pending.swap(lcu_context_events);
    }
    for (const auto &event : pending) {
      static_cast<void>(
          control_writer.Write(lol_assistant::lcu::SerializeLcuContextEventJson(
              event, lcu_context_session_id)));
    }
  };
  LiveCaptureState live;
  live.backend = options.capture_backend;
  try {
    if (wgc_source == nullptr && desktop_source == nullptr) {
      std::cerr << ErrorJson("source_error",
                             "No live capture backend was constructed")
                << '\n';
      return kSourceErrorExitCode;
    }

    std::string source_id = HandleText(*handle);
    source_id.append(";backend:");
    source_id.append(lol_assistant::app::ToString(options.capture_backend));
    InitializePipeline(options,
                       options.capture_backend == CaptureBackend::Wgc
                           ? "windows_graphics_capture"
                       : options.capture_backend == CaptureBackend::Desktop
                           ? "desktop_duplication"
                           : "live_capture_auto",
                       source_id, lol_assistant::collection::SampleKind::Real,
                       "real_window_capture", LiveWindowMetadata(*handle),
                       pipeline);
    if (options.live_client_mode == LiveClientMode::Auto) {
      try {
        live_client_session_id = pipeline.runtime->SessionId();
        auto reader =
            std::make_unique<lol_assistant::live_client::LiveClientReader>(
                std::make_unique<
                    lol_assistant::live_client::WinHttpLiveClientTransport>());
        live_client_poller =
            std::make_unique<lol_assistant::live_client::LiveClientPoller>(
                std::move(reader),
                lol_assistant::live_client::LiveClientPollerConfig{
                    kLiveClientPollInterval, kLiveClientUnchangedHeartbeat},
                [&live_client_events_mutex, &live_client_events](
                    const lol_assistant::live_client::LiveClientEvent &event) {
                  std::lock_guard lock(live_client_events_mutex);
                  // At a 200 ms Live Client cadence, 64 events cover only
                  // ~12.8 seconds. Preserve death edges across bounded OCR or
                  // artifact stalls without making the queue unbounded.
                  constexpr std::size_t kMaximumQueuedEvents = 1'024U;
                  if (live_client_events.size() == kMaximumQueuedEvents) {
                    live_client_events.pop_front();
                  }
                  live_client_events.push_back(event);
                });
        live_client_poller->Start();
      } catch (...) {
        lol_assistant::live_client::LiveClientEvent event;
        event.sequence = 1U;
        event.snapshot.status =
            lol_assistant::live_client::LiveClientStatus::InvalidResponse;
        event.snapshot.reason = "poller_start_failed";
        event.snapshot.observed_at = std::chrono::system_clock::now();
        static_cast<void>(control_writer.Write(
            lol_assistant::live_client::SerializeLiveClientEventJson(
                event, pipeline.runtime->SessionId())));
        live_client_poller.reset();
      }
    }
    if (options.lcu_context_mode == LcuContextMode::Auto) {
      try {
        lcu_context_session_id = pipeline.runtime->SessionId();
        auto reader = std::make_unique<lol_assistant::lcu::LcuContextReader>(
            std::make_unique<
                lol_assistant::lcu::EnvironmentLcuConnectionProvider>(),
            std::make_unique<lol_assistant::lcu::WinHttpLcuContextTransport>());
        lcu_context_poller =
            std::make_unique<lol_assistant::lcu::LcuContextPoller>(
                std::move(reader), lol_assistant::lcu::LcuContextPollerConfig{},
                [&lcu_context_events_mutex, &lcu_context_events](
                    const lol_assistant::lcu::LcuContextEvent &event) {
                  std::lock_guard lock(lcu_context_events_mutex);
                  constexpr std::size_t kMaximumQueuedEvents = 64U;
                  if (lcu_context_events.size() == kMaximumQueuedEvents) {
                    lcu_context_events.pop_front();
                  }
                  lcu_context_events.push_back(event);
                });
        lcu_context_poller->Start();
      } catch (...) {
        lol_assistant::lcu::LcuContextEvent event;
        event.sequence = 1U;
        event.snapshot.status =
            lol_assistant::lcu::LcuContextStatus::Unavailable;
        event.snapshot.reason = "poller_start_failed";
        event.snapshot.observed_at = std::chrono::system_clock::now();
        static_cast<void>(control_writer.Write(
            lol_assistant::lcu::SerializeLcuContextEventJson(
                event, pipeline.runtime->SessionId())));
        lcu_context_poller.reset();
      }
    }
    if (pipeline.collection.sample_hotkey_enabled) {
      pipeline.collection.f8_edge.Prime((GetAsyncKeyState(VK_F8) & 0x8000) !=
                                        0);
    }
    const auto started = std::chrono::steady_clock::now();
    if (live.desktop_started) {
      live.desktop_last_physical_progress = started;
    }
    pipeline.active_live_source.reset();
    if (desktop_source != nullptr && !live.desktop_started) {
      RequestDesktopRestart(live, started);
    }
    if (pipeline.force_recognition.controller != nullptr) {
      pipeline.force_recognition.controller->Prime(
          pipeline.force_recognition.enabled &&
              (GetAsyncKeyState(VK_F9) & 0x8000) != 0,
          started);
    }

    const auto deadline =
        started +
        std::chrono::duration_cast<std::chrono::steady_clock::duration>(
            std::chrono::duration<double>{options.max_seconds});
    auto next_process_at = started + kSnapshotIdle;
    const auto enter_snapshot_at = started + kEnterRecognitionDelay;
    auto next_auto_reread_at = started;
    auto next_miss_emit_at = started;
    bool ocr_was_held = OcrHoldActive(options.workspace_path);
    std::uint32_t interval_ticks = 0;
    std::optional<lol_assistant::app::PendingPhysicalFrame> last_held_frame;
    std::optional<lol_assistant::app::PendingPhysicalFrame> latest_physical_frame;
    bool stopped_after_once = false;
    bool window_closed = false;
    bool capture_failed = false;
    bool missing_frame_notified = false;
    auto capture_hold_until = started;
    auto next_capture_start_at = started;
    while (std::chrono::steady_clock::now() < deadline) {
      const auto now = std::chrono::steady_clock::now();
      if (IsWindow(target) == FALSE) {
        window_closed = true;
        break;
      }

      drain_live_client_events();
      drain_lcu_context_events();

      const bool ocr_held = OcrHoldActive(options.workspace_path);
      if (ocr_was_held && !ocr_held) {
        pipeline.snapshot_pending = true;
        pipeline.snapshot_cause = "interval";
        if (pipeline.processor != nullptr) {
          pipeline.processor->RequestImmediateReread();
        }
        live.pending_frames.AllowReplay();
      }
      ocr_was_held = ocr_held;
      if (ocr_held) {
        pipeline.snapshot_pending = false;
      } else if (!pipeline.enter_snapshot_done && now >= enter_snapshot_at) {
        pipeline.enter_snapshot_done = true;
        pipeline.snapshot_pending = true;
        pipeline.snapshot_cause = "enter";
        if (pipeline.processor != nullptr) {
          pipeline.processor->RequestImmediateReread();
        }
        live.pending_frames.AllowReplay();
      } else if (now >= next_auto_reread_at) {
        next_auto_reread_at = now + kOfferOcrIdle;
        pipeline.snapshot_pending = true;
        pipeline.snapshot_cause = "interval";
        ++interval_ticks;
        if (pipeline.processor != nullptr) {
          pipeline.processor->RequestImmediateReread();
        }
        live.pending_frames.AllowReplay();
      }

      const bool want_capture = true;
      if (want_capture) {
        if (now >= next_capture_start_at) {
          StartCaptureIfNeeded(live, wgc_source.get(), desktop_source.get());
          if (!live.wgc_started && !live.desktop_started) {
            next_capture_start_at = now + std::chrono::milliseconds{250};
          }
        }
      } else {
        StopLiveCaptureBackends(live, wgc_source.get(), desktop_source.get());
        next_capture_start_at = now;
      }

      // Rearm a 3-attempt/60-second backend budget before health is emitted.
      // This keeps the observable exhausted flag and scheduling decision in
      // the same tick.
      live.restart.attempts.Refresh(now);
      live.desktop_restart.attempts.Refresh(now);

      // Preserve the idle 250 ms cadence before potentially blocking capture
      // restart work. State transitions below still force an immediate flush.
      EmitCaptureHealth(pipeline, live, now, false,
                        pipeline.control_state_dirty);
      if (want_capture) {
        ServiceWgcRestart(live, wgc_source.get(), now);
      }

      const auto target_geometry =
          lol_assistant::capture::ResolveDesktopCaptureGeometry(
              desktop_target,
              lol_assistant::capture::PartialVisibilityPolicy::
                  CropVisibleIntersection,
              nullptr);
      const auto visibility =
          lol_assistant::app::ToCaptureTargetVisibility(target_geometry.status);
      const bool visibility_changed = !live.target_visibility_initialized ||
                                      live.target_visibility != visibility;
      live.target_visibility_initialized = true;
      live.target_visibility = visibility;
      const bool globally_visible =
          visibility == lol_assistant::app::CaptureTargetVisibility::Visible;

      const auto discard_capture_queues = [&] {
        if (wgc_source != nullptr) {
          static_cast<void>(wgc_source->TryGetNextCapturedFrame());
        }
        if (desktop_source != nullptr) {
          static_cast<void>(desktop_source->TryGetNextCapturedFrame());
        }
      };
      if ((visibility_changed || !globally_visible) &&
          !pipeline.snapshot_pending) {
        discard_capture_queues();
        live.pending_frames.Invalidate();
        live.latest_wgc.reset();
        live.latest_desktop.reset();
        live.wgc_last_content_change.reset();
        live.desktop_last_content_change.reset();
        live.desktop_last_physical_progress.reset();
        if (globally_visible && live.desktop_started) {
          live.desktop_last_physical_progress = now;
        }
      }
      if (globally_visible && want_capture) {
        ServiceDesktopRestart(live, desktop_source.get(), now);
      } else {
        // A hidden/minimized/offscreen target cannot validate a restarted
        // Desktop source.  Cancel the unconsumed request and re-arm it after
        // the next visible transition without burning an attempt.
        live.desktop_restart.pending = false;
        live.desktop_restart.due.reset();
      }

      // On a visibility transition to Visible, discard one final pre-transition
      // mailbox snapshot and wait for a subsequent physical callback/present.
      // A click/enter reread must still pull the newest swapchain/desktop
      // frame even if the game reports hidden or Auto preferred Desktop.
      const bool may_observe_frames =
          want_capture && (pipeline.snapshot_pending ||
                           (globally_visible && !visibility_changed));
      std::optional<ObservedLiveFrame> current_wgc;
      if (may_observe_frames && wgc_source != nullptr && live.wgc_started) {
        const bool needs_wgc_observation =
            pipeline.snapshot_pending ||
            live.backend != CaptureBackend::Auto || !live.desktop_started;
        if (!needs_wgc_observation) {
          live.latest_wgc.reset();
          live.wgc_last_content_change.reset();
        }
        std::optional<lol_assistant::common::CapturedFrame> captured;
        while (auto next = wgc_source->TryGetNextCapturedFrame()) {
          if (next->IsValid() && next->identity.capture_epoch ==
                                     wgc_source->CurrentCaptureEpoch()) {
            captured = std::move(*next);
            ObserveCardPickLumaOnly(pipeline, captured->frame);
            if (pipeline.stop_after_pick) {
              break;
            }
          }
        }
        if (captured.has_value() && needs_wgc_observation) {
          live.wgc_epoch = captured->identity.capture_epoch;
          current_wgc = ObserveLiveFrame(live.freshness_gate,
                                         std::move(*captured), "wgc", now);
          live.latest_wgc = current_wgc->freshness;
          if (current_wgc->freshness.process_allowed &&
              current_wgc->freshness.content_changed) {
            live.wgc_last_content_change = now;
          }
        }
      }
      if (wgc_source != nullptr && live.wgc_started) {
        const CaptureState state = wgc_source->State();
        if (state == CaptureState::Failed || state == CaptureState::Closed) {
          live.wgc_started = false;
          live.latest_wgc.reset();
          live.pending_frames.Invalidate();
          if (live.backend == CaptureBackend::Wgc || !live.desktop_started) {
            RequestWgcRestart(live, now);
          }
        }
      }

      std::optional<ObservedLiveFrame> current_desktop;
      if (may_observe_frames && desktop_source != nullptr &&
          live.desktop_started) {
        std::optional<lol_assistant::common::CapturedFrame> captured;
        while (auto next = desktop_source->TryGetNextCapturedFrame()) {
          if (next->IsValid()) {
            captured = std::move(*next);
            ObserveCardPickLumaOnly(pipeline, captured->frame);
            if (pipeline.stop_after_pick) {
              break;
            }
          }
        }
        if (captured.has_value()) {
          current_desktop = ObserveLiveFrame(
              live.freshness_gate, std::move(*captured), "desktop", now);
          live.latest_desktop = current_desktop->freshness;
          live.desktop_last_physical_progress = now;
          if (current_desktop->freshness.process_allowed &&
              current_desktop->freshness.content_changed) {
            live.desktop_last_content_change = now;
          }
        }
      }
      if (desktop_source != nullptr && live.desktop_started) {
        const CaptureState state = desktop_source->State();
        if (state == CaptureState::Failed || state == CaptureState::Closed) {
          live.desktop_started = false;
          live.latest_desktop.reset();
          live.pending_frames.Invalidate();
          if (globally_visible) {
            RequestDesktopRestart(live, now);
          }
        } else if (state == CaptureState::Paused) {
          live.latest_desktop.reset();
        }
      }
      if (desktop_source != nullptr &&
          lol_assistant::app::ShouldRestartStalledBackend(
              live.desktop_started, visibility,
              live.desktop_last_physical_progress, now,
              kDesktopStallRestartTimeout)) {
        // A nominally running backend without physical progress is unusable.
        // Mark it unavailable before scheduling recovery so auto mode can use
        // a strongly-proven WGC frame instead of waiting behind a stale bool.
        desktop_source->Stop();
        live.desktop_started = false;
        live.latest_desktop.reset();
        live.desktop_last_content_change.reset();
        live.desktop_last_physical_progress.reset();
        live.pending_frames.Invalidate();
        RequestDesktopRestart(live, now);
      } else if (want_capture && desktop_source != nullptr &&
                 !live.desktop_started && !live.wgc_started &&
                 globally_visible) {
        RequestDesktopRestart(live, now);
      }
      if (live.backend == CaptureBackend::Auto && live.desktop_started &&
          !pipeline.snapshot_pending) {
        // Desktop is authoritative while idle. A click/enter snapshot must
        // keep WGC recovery available so exclusive-fullscreen still has a
        // current swapchain frame.
        live.restart.pending = false;
        live.restart.due.reset();
        live.restart.recovery_validation_deadline.reset();
      } else if (want_capture && live.backend == CaptureBackend::Auto &&
                 !live.desktop_started && !live.wgc_started) {
        RequestWgcRestart(live, now);
      }

      const auto previous_source = live.health.selected_source;
      const auto previous_epoch = live.health.selected_epoch;
      auto decision = DecideCaptureHealth(live, now);
      static_cast<void>(
          ApplyCaptureHealth(pipeline, live, std::move(decision), now));
      const bool selection_changed =
          previous_source != live.health.selected_source ||
          previous_epoch != live.health.selected_epoch;
      if (selection_changed) {
        live.pending_frames.Invalidate();
        // Baselines are scoped to one capture source/epoch and geometry.
        // Carrying one across a switch can permanently block later stages.
        pipeline.pending_hud_selections.clear();
        next_process_at = now;
      }

      // F9 binds after this tick's authoritative health decision.
      const std::string active_source =
          pipeline.active_live_source.value_or("none");
      static_cast<void>(
          PollForceRecognitionHotkey(pipeline, active_source, now));
      PollSampleHotkey(pipeline);

      if (pipeline.force_recognition.controller != nullptr &&
          ForceRecognitionActive(pipeline, now)) {
        const auto force_snapshot =
            pipeline.force_recognition.controller->Snapshot(now);
        const auto reject_if_active_source =
            [&](const std::optional<ObservedLiveFrame> &observed) {
              if (observed.has_value() &&
                  observed->context.source == force_snapshot.source &&
                  !observed->freshness.process_allowed) {
                EmitForceRecognitionEvents(
                    pipeline,
                    pipeline.force_recognition.controller->ObserveFrame(
                        false, false, false, now));
              }
            };
        reject_if_active_source(current_wgc);
        reject_if_active_source(current_desktop);
      }

      const bool force_recognition = ForceRecognitionActive(pipeline, now);
      if (pipeline.snapshot_pending || force_recognition) {
        next_process_at = now;
      }

      if (pipeline.snapshot_pending && current_wgc.has_value()) {
        latest_physical_frame = PendingFromObserved(std::move(*current_wgc));
        current_wgc.reset();
      } else if (current_desktop.has_value()) {
        latest_physical_frame = PendingFromObserved(std::move(*current_desktop));
        current_desktop.reset();
      } else if (current_wgc.has_value()) {
        latest_physical_frame = PendingFromObserved(std::move(*current_wgc));
        current_wgc.reset();
      }

      const bool want_process =
          !pipeline.stop_after_pick &&
          (pipeline.snapshot_pending || force_recognition);
      std::optional<lol_assistant::app::PendingPhysicalFrame> due_frame;
      if (want_process) {
        if (latest_physical_frame.has_value() &&
            latest_physical_frame->captured.IsValid()) {
          latest_physical_frame->freshness.process_allowed = true;
          latest_physical_frame->freshness.input.observed_at = now;
          due_frame = latest_physical_frame;
        } else if (last_held_frame.has_value() &&
                   last_held_frame->captured.IsValid()) {
          last_held_frame->freshness.process_allowed = true;
          last_held_frame->freshness.input.observed_at = now;
          due_frame = last_held_frame;
        }
      }
      live.pending_frames.Invalidate();
      if (due_frame.has_value()) {
        const auto &transport = due_frame->freshness.input;
        const LiveFrameContext frame_context{transport.source, transport.epoch,
                                             transport.content_hash,
                                             transport.observed_at};
        const std::uint32_t accepted_before = pipeline.accepted_offer_count;
        const bool snapshot = pipeline.snapshot_pending;
        const bool interval_tick =
            snapshot && pipeline.snapshot_cause == "interval";
        const bool reread_offer = snapshot;
        pipeline.snapshot_pending = false;
        pipeline.click_armed = false;
        static_cast<void>(
            ProcessFrame(pipeline, due_frame->captured.frame, std::nullopt,
                         force_recognition || reread_offer, &frame_context,
                         reread_offer, interval_tick));
        last_held_frame = std::move(due_frame);
        capture_hold_until = std::chrono::steady_clock::now() +
                             kCaptureHoldAfterUse;
        pipeline.snapshot_cause.clear();
        if (pipeline.accepted_offer_count > accepted_before) {
          const auto accepted_at = std::chrono::steady_clock::now();
          emit_mayhem_events(mayhem_scheduler.OfferDetected(accepted_at),
                             live_client_session_id, accepted_at);
        }
        next_process_at = std::chrono::steady_clock::now() + kSnapshotIdle;
        missing_frame_notified = false;
        if (!ForceRecognitionActive(pipeline,
                                    std::chrono::steady_clock::now()) &&
            lol_assistant::app::ShouldStopAfterRecognitionAttempt(
                options.once, pipeline.recognition_progress)) {
          stopped_after_once = true;
          break;
        }
      } else if (pipeline.snapshot_pending) {
        if (pipeline.snapshot_cause == "interval" && now >= next_miss_emit_at) {
          next_miss_emit_at = now + kSnapshotIdle;
          missing_frame_notified = true;
          EmitIntervalMiss();
        }
        if (wgc_source != nullptr && !live.wgc_started) {
          RequestWgcRestart(live, now);
        }
      }

      if (pipeline.stop_after_pick) {
        break;
      }

      EmitCaptureHealth(pipeline, live, std::chrono::steady_clock::now(), false,
                        pipeline.control_state_dirty);
      PumpPreview(pipeline);

      const bool explicit_wgc_failed =
          options.capture_backend == CaptureBackend::Wgc &&
          live.restart.attempts.exhausted() && !live.wgc_started;
      const bool explicit_desktop_failed =
          options.capture_backend == CaptureBackend::Desktop &&
          live.desktop_restart.attempts.exhausted() && !live.desktop_started;
      const bool auto_failed =
          options.capture_backend == CaptureBackend::Auto &&
          live.restart.attempts.exhausted() &&
          live.desktop_restart.attempts.exhausted() && !live.wgc_started &&
          !live.desktop_started;
      if (explicit_wgc_failed || explicit_desktop_failed || auto_failed) {
        capture_failed = true;
        break;
      }
      std::this_thread::sleep_for(want_capture ? kBusyLoopSleep
                                               : kIdleLoopSleep);
    }

    if (pipeline.stop_after_pick) {
      return 0;
    }

    if (lcu_context_poller != nullptr) {
      lcu_context_poller->Stop();
    }
    if (live_client_poller != nullptr) {
      live_client_poller->Stop();
    }
    drain_live_client_events();
    drain_lcu_context_events();
    if (pipeline.force_recognition.controller != nullptr) {
      EmitForceRecognitionEvents(
          pipeline, pipeline.force_recognition.controller->EndSession(
                        std::chrono::steady_clock::now()));
    }
    live.health.state = lol_assistant::app::CaptureHealthState::Degraded;
    live.health.reason = lol_assistant::app::HealthReason::NoFreshSource;
    live.health.selected_source.reset();
    live.health.selected_epoch.reset();
    live.health.revoke_recommendation = true;
    live.health.switched_source = true;
    EmitCaptureHealth(pipeline, live, std::chrono::steady_clock::now(), true,
                      true);
    if (wgc_source != nullptr) {
      wgc_source->Stop();
      live.wgc_started = false;
    }
    if (desktop_source != nullptr) {
      desktop_source->Stop();
      live.desktop_started = false;
    }

    const char *status = "timeout";
    if (stopped_after_once) {
      status = lol_assistant::app::ToString(
          lol_assistant::app::ResolveRecognitionSessionStatus(
              lol_assistant::app::SessionStopReason::
                  RecognitionAttemptCompleted,
              pipeline.recognition_progress));
    } else if (window_closed) {
      status = "closed";
    } else if (capture_failed) {
      status = "failed";
    }

    std::string summary{"{\"kind\":\"live_capture\",\"backend\":"};
    AppendJsonString(summary,
                     lol_assistant::app::ToString(options.capture_backend));
    summary.append(",\"hwnd\":");
    AppendJsonString(summary, HandleText(*handle));
    summary.append(",\"id\":");
    AppendJsonString(summary, source_id);
    if (wgc_source != nullptr) {
      const auto statistics = wgc_source->Statistics();
      summary.append(",\"wgc\":{\"received\":");
      summary.append(std::to_string(statistics.received));
      summary.append(",\"converted\":");
      summary.append(std::to_string(statistics.converted));
      summary.append(",\"dropped\":");
      summary.append(std::to_string(statistics.dropped));
      summary.push_back('}');
    }
    if (desktop_source != nullptr) {
      const auto statistics = desktop_source->Statistics();
      summary.append(",\"desktop\":{\"acquired\":");
      summary.append(std::to_string(statistics.acquired));
      summary.append(",\"released\":");
      summary.append(std::to_string(statistics.released));
      summary.append(",\"converted\":");
      summary.append(std::to_string(statistics.converted));
      summary.append(",\"dropped\":");
      summary.append(std::to_string(statistics.dropped));
      summary.push_back('}');
    }
    summary.append(",\"capture_state\":");
    AppendJsonString(summary, capture_failed  ? "failed"
                              : window_closed ? "closed"
                                              : "stopped");
    summary.push_back('}');

    const int close_result = ClosePipeline(pipeline, status, summary);
    if (close_result != 0) {
      return close_result;
    }
    return capture_failed ? kSourceErrorExitCode : 0;
  } catch (const std::exception &error) {
    if (lcu_context_poller != nullptr) {
      lcu_context_poller->Stop();
    }
    if (live_client_poller != nullptr) {
      live_client_poller->Stop();
    }
    drain_live_client_events();
    drain_lcu_context_events();
    if (wgc_source != nullptr) {
      wgc_source->Stop();
    }
    if (desktop_source != nullptr) {
      desktop_source->Stop();
    }
    static_cast<void>(CloseAfterException(pipeline));
    std::cerr << ErrorJson("pipeline_error", error.what()) << '\n';
    return kSourceErrorExitCode;
  } catch (...) {
    if (lcu_context_poller != nullptr) {
      lcu_context_poller->Stop();
    }
    if (live_client_poller != nullptr) {
      live_client_poller->Stop();
    }
    drain_live_client_events();
    drain_lcu_context_events();
    if (wgc_source != nullptr) {
      wgc_source->Stop();
    }
    if (desktop_source != nullptr) {
      desktop_source->Stop();
    }
    static_cast<void>(CloseAfterException(pipeline));
    std::cerr << ErrorJson("pipeline_error", "Unknown live exception") << '\n';
    return kSourceErrorExitCode;
  }
}

} // namespace

int wmain(const int argc, wchar_t *argv[]) {
  try {
    std::vector<std::wstring> arguments;
    if (argc > 1) {
      arguments.reserve(static_cast<std::size_t>(argc - 1));
    }
    for (int index = 1; index < argc; ++index) {
      arguments.emplace_back(argv[index]);
    }

    const auto parsed = lol_assistant::app::ParseCommandLine(arguments);
    if (!parsed.ok()) {
      std::cerr << ErrorJson("cli_error", parsed.error) << '\n';
      return kUsageErrorExitCode;
    }
    ApplicationOptions options = *parsed.options;
    ApplyRunnerCaptureBackend(options);
    switch (options.command) {
    case lol_assistant::app::Command::Help:
      PrintHelp();
      return 0;
    case lol_assistant::app::Command::Version:
      std::cout << "lol_augment_assistant " << LOL_ASSISTANT_VERSION
                << " (Phase2 portable)\n";
      return 0;
    case lol_assistant::app::Command::ListWindows:
      return ListWindows();
    case lol_assistant::app::Command::ProbeLiveClient:
      return ProbeLiveClient();
    case lol_assistant::app::Command::Run:
      break;
    default:
      std::cerr << ErrorJson("cli_error", "Unknown command") << '\n';
      return kUsageErrorExitCode;
    }

    if (options.source_kind == SourceKind::Replay) {
      return RunReplay(options);
    }
    return RunLive(options);
  } catch (const std::exception &error) {
    std::cerr << ErrorJson("fatal_error", error.what()) << '\n';
    return kSourceErrorExitCode;
  }
}

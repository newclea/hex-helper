#include <array>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "cli.h"
#include "live_capture_supervisor.h"
#include "live_control.h"

namespace {

using namespace std::chrono_literals;
using lol_assistant::app::BoundedSamplePolicy;
using lol_assistant::app::CaptureBackend;
using lol_assistant::app::CaptureHealthState;
using lol_assistant::app::CaptureTargetVisibility;
using lol_assistant::app::ForceEventKind;
using lol_assistant::app::ForceRecognitionController;
using lol_assistant::app::ForceTerminalReason;
using lol_assistant::app::FrameFreshnessGate;
using lol_assistant::app::FrameTransportInput;
using lol_assistant::app::ParseCommandLine;
using lol_assistant::app::RestartAttemptWindow;
using lol_assistant::app::SampleDecision;
using lol_assistant::app::SamplePolicyInput;
using lol_assistant::app::SampleTrigger;

void Require(const bool condition, const std::string &message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

[[nodiscard]] std::string ReadText(const std::filesystem::path &path) {
  std::ifstream input(path, std::ios::binary);
  Require(static_cast<bool>(input), "unable to open " + path.string());
  return {std::istreambuf_iterator<char>{input},
          std::istreambuf_iterator<char>{}};
}

void TestBackendCliContract(const std::filesystem::path &root) {
  for (const auto &entry : std::vector<std::pair<std::wstring, CaptureBackend>>{
           {L"auto", CaptureBackend::Auto},
           {L"wgc", CaptureBackend::Wgc},
           {L"desktop", CaptureBackend::Desktop}}) {
    const auto parsed = ParseCommandLine(
        {L"--hwnd", L"4096", L"--capture-backend", entry.first}, root);
    Require(parsed.ok() && parsed.options->capture_backend == entry.second &&
                parsed.options->capture_backend_explicit,
            "all live capture backend spellings must parse exactly");
  }
  const auto replay = ParseCommandLine(
      {L"--replay", root.wstring(), L"--capture-backend", L"auto"}, root);
  Require(!replay.ok(), "Replay must reject the live-only backend option");
}

FrameTransportInput Frame(std::string source, const std::uint64_t epoch,
                          const std::optional<std::int64_t> source_time,
                          std::string hash, const std::uint64_t progress,
                          const std::chrono::steady_clock::time_point now) {
  FrameTransportInput input;
  input.source = std::move(source);
  input.epoch = epoch;
  input.source_time = source_time;
  input.content_hash = std::move(hash);
  input.progress = progress;
  input.observed_at = now;
  return input;
}

void TestProductionDesktopAuthoritativePolicy() {
  const auto now = std::chrono::steady_clock::time_point{};
  FrameFreshnessGate gate;
  auto wgc = gate.Observe(Frame("wgc", 1U, std::nullopt, "old-wgc", 1U, now));
  const auto desktop =
      gate.Observe(Frame("desktop", 4U, 100, "current-desktop", 1U, now));
  wgc =
      gate.Observe(Frame("wgc", 1U, std::nullopt, "old-wgc", 2U, now + 750ms));

  lol_assistant::app::LiveCaptureDecisionInput input;
  input.policy =
      lol_assistant::app::LiveCapturePolicy::AutoDesktopAuthoritative;
  input.target_visibility = CaptureTargetVisibility::Visible;
  input.wgc_backend_available = true;
  input.desktop_backend_available = true;
  input.latest_wgc = wgc;
  input.latest_desktop = desktop;
  input.now = now + 750ms;
  const auto startup = lol_assistant::app::DecideLiveCapture(input);
  Require(startup.state == CaptureHealthState::DesktopFallback &&
              startup.selected_source ==
                  std::optional<std::string>{"desktop"} &&
              startup.reason ==
                  lol_assistant::app::HealthReason::DesktopAuthoritative,
          "startup old-WGC/current-desktop must select desktop by 750 ms");

  wgc =
      gate.Observe(Frame("wgc", 1U, std::nullopt, "old-wgc", 3U, now + 800ms));
  input.previous = startup;
  input.latest_wgc = wgc;
  input.now = now + 800ms;
  const auto aged = lol_assistant::app::DecideLiveCapture(input);
  Require(!aged.selected_source.has_value() &&
              aged.state == CaptureHealthState::Suspect,
          "an 800 ms old desktop observation must not remain selected");

  input.policy = lol_assistant::app::LiveCapturePolicy::WgcOnly;
  input.desktop_backend_available = false;
  const auto explicit_unproven = lol_assistant::app::DecideLiveCapture(input);
  Require(!explicit_unproven.selected_source.has_value() &&
              explicit_unproven.state == CaptureHealthState::Degraded &&
              explicit_unproven.reason ==
                  lol_assistant::app::HealthReason::WgcFreshnessUnproven,
          "explicit WGC without source time must fail closed as degraded");

  const auto proved_wgc =
      gate.Observe(Frame("wgc", 2U, 900, "proved-wgc", 1U, now + 801ms));
  input.policy =
      lol_assistant::app::LiveCapturePolicy::AutoDesktopAuthoritative;
  input.latest_wgc = proved_wgc;
  input.latest_desktop.reset();
  input.desktop_backend_available = false;
  input.now = now + 801ms;
  const auto wgc_fallback = lol_assistant::app::DecideLiveCapture(input);
  Require(wgc_fallback.selected_source == std::optional<std::string>{"wgc"} &&
              wgc_fallback.state == CaptureHealthState::Degraded,
          "WGC is allowed only as a proved degraded fallback when desktop is "
          "unavailable");
}

void TestSelectedDesktopFeedsControlAndBudgets() {
  const auto now = std::chrono::steady_clock::time_point{};

  ForceRecognitionController force;
  auto events = force.Request("desktop", now + 10ms);
  Require(events.size() == 1U && events.front().kind == ForceEventKind::Started,
          "selected desktop must start one force-recognition request");
  Require(force.ObserveFrame(false, false, false, now + 11ms).empty(),
          "stale transport must not consume or terminate the burst");
  events = force.ObserveFrame(true, true, false, now + 12ms);
  Require(events.size() == 1U &&
              events.front().kind == ForceEventKind::Terminal &&
              events.front().terminal_reason == ForceTerminalReason::Accepted,
          "one fresh desktop acceptance must close the request exactly once");

  BoundedSamplePolicy samples;
  std::uint32_t allowed = 0U;
  for (std::uint32_t index = 0U; index < 70U; ++index) {
    SamplePolicyInput input;
    input.trigger = SampleTrigger::Auto;
    input.transport_fresh = true;
    input.source = "desktop";
    input.epoch = 4U;
    input.content_key = "offer-b";
    input.reason = "ocr_icon_conflict";
    input.auto_eligible = true;
    if (samples.Evaluate(input) == SampleDecision::Allow) {
      ++allowed;
    }
  }
  Require(allowed == 1U && samples.Snapshot().allowed == 1U,
          "same source/epoch/content/reason must consume one sample");
}

lol_assistant::common::Frame
SolidFrame(const std::uint32_t width, const std::uint32_t height,
           const std::uint8_t value,
           lol_assistant::common::FrameSource source =
               {lol_assistant::common::FrameSourceKind::DesktopDuplication,
                "hash-test-desktop"},
           const std::uint32_t extra_stride = 0U) {
  lol_assistant::common::Frame frame;
  frame.source = std::move(source);
  frame.frame_id = 1U;
  frame.width = width;
  frame.height = height;
  frame.stride = width * 4U + extra_stride;
  frame.buffer.assign(static_cast<std::size_t>(frame.stride) * height, value);
  return frame;
}

void TestFullTransportHashContract() {
  const auto original = SolidFrame(64U, 36U, 0U);
  auto localized = original;
  const std::size_t localized_offset = localized.stride + 4U;
  localized.buffer[localized_offset] = 0xFFU;
  localized.buffer[localized_offset + 1U] = 0x80U;
  localized.buffer[localized_offset + 2U] = 0x40U;

  const auto original_hash =
      lol_assistant::app::ComputeTransportContentHash(original);
  Require(original_hash !=
              lol_assistant::app::ComputeTransportContentHash(localized),
          "full-content hash must detect a localized unsampled pixel");
  Require(original_hash != lol_assistant::app::ComputeTransportContentHash(
                               SolidFrame(128U, 72U, 0U)),
          "transport hash must include dimensions");
  Require(original_hash != lol_assistant::app::ComputeTransportContentHash(
                               SolidFrame(64U, 36U, 0U, original.source, 4U)),
          "transport hash must include stride");
  auto wgc_source = original.source;
  wgc_source.kind =
      lol_assistant::common::FrameSourceKind::WindowsGraphicsCapture;
  wgc_source.id = "hash-test-wgc";
  Require(original_hash != lol_assistant::app::ComputeTransportContentHash(
                               SolidFrame(64U, 36U, 0U, wgc_source)),
          "transport hash must include source provenance");

  const auto performance_frame = SolidFrame(2560U, 1600U, 0x5AU);
  const auto started = std::chrono::steady_clock::now();
  const auto performance_hash =
      lol_assistant::app::ComputeTransportContentHash(performance_frame);
  const auto elapsed = std::chrono::duration<double, std::milli>(
      std::chrono::steady_clock::now() - started);
  Require(!performance_hash.empty(),
          "full-frame performance estimate must produce a hash");
  std::cout << "[PERF-ESTIMATE] transport_hash bytes="
            << performance_frame.buffer.size()
            << " elapsed_ms=" << elapsed.count() << '\n';
}

lol_assistant::app::PendingPhysicalFrame
PendingFrame(const std::uint64_t epoch, const std::uint64_t frame_id,
             const std::chrono::steady_clock::time_point observed_at) {
  lol_assistant::app::PendingPhysicalFrame pending;
  pending.captured.frame =
      SolidFrame(2U, 1U, static_cast<std::uint8_t>(frame_id));
  pending.captured.frame.frame_id = frame_id;
  pending.captured.identity = {pending.captured.frame.source, epoch, frame_id};
  pending.freshness.input =
      Frame("desktop", epoch, static_cast<std::int64_t>(frame_id),
            "pending-" + std::to_string(frame_id), frame_id, observed_at);
  pending.freshness.freshness = lol_assistant::app::FrameFreshness::Fresh;
  pending.freshness.process_allowed = true;
  return pending;
}

void TestThrottlePendingNewestAndInvalidation() {
  const auto start = std::chrono::steady_clock::time_point{};
  lol_assistant::app::PendingPhysicalFrameBuffer pending;
  Require(pending.Offer(PendingFrame(4U, 1U, start), "desktop", 4U),
          "selected fresh desktop frame must enter the throttle mailbox");
  Require(!pending.TakeIfDue(start + 100ms, start + 200ms, "desktop", 4U,
                             CaptureTargetVisibility::Visible, 750ms)
                  .has_value() &&
              pending.HasPending(),
          "a one-shot desktop frame must survive before processor due time");
  const auto due =
      pending.TakeIfDue(start + 200ms, start + 200ms, "desktop", 4U,
                        CaptureTargetVisibility::Visible, 750ms);
  Require(due.has_value() && due->captured.identity.frame_id == 1U,
          "the preserved desktop frame must be delivered at due time");
  Require(!pending.Offer(PendingFrame(4U, 1U, start + 201ms), "desktop", 4U),
          "the same physical identity must never be OCRed twice");
  pending.AllowReplay();
  Require(pending.Offer(PendingFrame(4U, 1U, start + 202ms), "desktop", 4U),
          "an explicit left-click replay must re-offer the last identity");
  static_cast<void>(pending.TakeIfDue(start + 202ms, start + 202ms, "desktop",
                                      4U, CaptureTargetVisibility::Visible,
                                      750ms));

  Require(pending.Offer(PendingFrame(4U, 2U, start + 300ms), "desktop", 4U),
          "a newer physical frame must be accepted");
  Require(!pending.TakeIfDue(start + 301ms, start + 500ms, "desktop", 4U,
                             CaptureTargetVisibility::Hidden, 750ms)
                  .has_value() &&
              !pending.HasPending(),
          "visibility loss must clear the pending selected frame");

  Require(pending.Offer(PendingFrame(4U, 3U, start + 400ms), "desktop", 4U),
          "a post-visibility frame must be independently offerable");
  Require(!pending.TakeIfDue(start + 401ms, start + 500ms, "wgc", 9U,
                             CaptureTargetVisibility::Visible, 750ms)
                  .has_value() &&
              !pending.HasPending(),
          "source or epoch changes must clear old pending pixels");

  Require(pending.Offer(PendingFrame(4U, 4U, start + 600ms), "desktop", 4U),
          "a click-held frame must enter the mailbox");
  const auto forced =
      pending.TakeIfDue(start + 601ms, start + 601ms, "desktop", 4U,
                        CaptureTargetVisibility::Hidden, 750ms, true);
  Require(forced.has_value() && forced->captured.identity.frame_id == 4U,
          "a left-click reread must OCR the last frame even if the game "
          "reports hidden");
}

void TestGlobalVisibilityFailClosed() {
  const auto now = std::chrono::steady_clock::time_point{};
  FrameFreshnessGate gate;
  const auto wgc = gate.Observe(Frame("wgc", 8U, 100, "wgc", 1U, now));
  const auto desktop =
      gate.Observe(Frame("desktop", 4U, 100, "desktop", 1U, now));
  lol_assistant::app::CaptureHealthSnapshot previous;
  previous.state = CaptureHealthState::DesktopFallback;
  previous.selected_source = "desktop";
  previous.selected_epoch = 4U;

  const std::array visibilities{
      CaptureTargetVisibility::Minimized, CaptureTargetVisibility::Hidden,
      CaptureTargetVisibility::Offscreen, CaptureTargetVisibility::CrossOutput};
  const std::array policies{
      lol_assistant::app::LiveCapturePolicy::AutoDesktopAuthoritative,
      lol_assistant::app::LiveCapturePolicy::WgcOnly};
  for (const auto policy : policies) {
    for (const auto visibility : visibilities) {
      lol_assistant::app::LiveCaptureDecisionInput input;
      input.policy = policy;
      input.target_visibility = visibility;
      input.wgc_backend_available = true;
      input.desktop_backend_available = true;
      input.latest_wgc = wgc;
      input.latest_desktop = desktop;
      input.previous = previous;
      input.now = now;
      const auto decision = lol_assistant::app::DecideLiveCapture(input);
      Require(!decision.selected_source.has_value() &&
                  decision.state == CaptureHealthState::Degraded &&
                  decision.revoke_recommendation,
              "auto and explicit WGC must fail closed for non-visible targets");
    }
  }
}

void TestRestartWindowRearmsAndStallWatchdog() {
  const auto start = std::chrono::steady_clock::time_point{};
  RestartAttemptWindow budget{3U, 60s};
  Require(budget.TryConsume(start) && budget.TryConsume(start) &&
              budget.TryConsume(start),
          "three attempts must fit inside the configured restart window");
  budget.MarkExhausted();
  Require(budget.exhausted() && !budget.CanSchedule(start + 59s),
          "an exhausted restart window must stay closed before expiry");
  Require(budget.CanSchedule(start + 60s) && !budget.exhausted() &&
              budget.attempts_in_window() == 0U &&
              budget.TryConsume(start + 60s),
          "the rolling restart budget must rearm exactly at 60 seconds");

  using lol_assistant::app::ShouldRestartStalledBackend;
  Require(!ShouldRestartStalledBackend(true, CaptureTargetVisibility::Visible,
                                       start, start + 3s, 3s),
          "the Desktop stall threshold is an inclusive grace interval");
  Require(ShouldRestartStalledBackend(true, CaptureTargetVisibility::Visible,
                                      start, start + 3001ms, 3s),
          "a visible started backend must restart after physical silence");
  for (const auto visibility : std::array{
           CaptureTargetVisibility::Minimized, CaptureTargetVisibility::Hidden,
           CaptureTargetVisibility::Offscreen,
           CaptureTargetVisibility::CrossOutput}) {
    Require(
        !ShouldRestartStalledBackend(true, visibility, start, start + 30s, 3s),
        "non-visible targets must not consume restart attempts");
  }
  Require(!ShouldRestartStalledBackend(false, CaptureTargetVisibility::Visible,
                                       start, start + 30s, 3s) &&
              !ShouldRestartStalledBackend(true,
                                           CaptureTargetVisibility::Visible,
                                           std::nullopt, start + 30s, 3s),
          "stopped or uninitialized backends are outside the stall watchdog");
}

void TestProductWiringAndForbiddenApiGuard(const std::filesystem::path &root) {
  const std::string main = ReadText(root / L"src/app/main.cpp");
  const std::string runner = ReadText(root / L"scripts/run_recommendation.ps1");
  const std::string cmake = ReadText(root / L"CMakeLists.txt");
  const std::string wgc =
      ReadText(root / L"src/capture/windows_graphics_capture_source.cpp");
  for (const auto &token : std::vector<std::string>{
           "DesktopDuplicationSource", "FrameFreshnessGate",
           "DecideLiveCapture", "PendingPhysicalFrameBuffer",
           "ForceRecognitionController", "BoundedSamplePolicy",
           "FlushedJsonLineWriter", "TryGetNextCapturedFrame",
           "CurrentCaptureEpoch", "SerializeHealthJson",
           "recommendation_freshness"}) {
    Require(main.find(token) != std::string::npos,
            "main live wiring must contain " + token);
  }
  const auto health_decision =
      main.find("auto decision = DecideCaptureHealth(live, now)");
  const auto f9_poll = main.find("PollForceRecognitionHotkey", health_decision);
  Require(health_decision != std::string::npos &&
              f9_poll != std::string::npos && f9_poll > health_decision,
          "F9 active source must bind after the current health decision");
  for (const auto &token : std::vector<std::string>{
           "SystemRelativeTime", "callback_epoch", "WgcCapturedFrameMailbox",
           "TryGetNextCapturedFrame"}) {
    Require(wgc.find(token) != std::string::npos,
            "WGC epoch/source-time implementation must contain " + token);
  }
  Require(main.find("captured.identity = {frame->source, live.wgc_epoch") ==
              std::string::npos,
          "main must not relabel ordinary WGC frames with the current epoch");
  Require(
      runner.find("CaptureBackend") != std::string::npos &&
          (runner.find("LOL_ASSISTANT_CAPTURE_BACKEND") != std::string::npos ||
           runner.find("--capture-backend") != std::string::npos),
      "runner must forward -CaptureBackend to the live C++ child");
  for (const auto &token : std::vector<std::string>{
           "desktop_capture_geometry.cpp", "desktop_duplication_source.cpp",
           "dxgi_desktop_duplication_backend.cpp",
           "live_capture_supervisor.cpp", "live_control.cpp", "health_json.cpp",
           "desktop_capture_geometry_test", "desktop_duplication_test",
           "live_control_test", "health_json_test"}) {
    Require(cmake.find(token) != std::string::npos,
            "CMake must register " + token);
  }
  for (const auto &forbidden : std::vector<std::string>{
           "PrintWindow", "SendInput", "keybd_event", "mouse_event",
           "ReadProcessMemory", "WriteProcessMemory", "SetWindowsHookEx"}) {
    Require(main.find(forbidden) == std::string::npos,
            "main must not use forbidden API " + forbidden);
    Require(runner.find(forbidden) == std::string::npos,
            "runner must not use forbidden API " + forbidden);
  }
}

} // namespace

int main() {
  try {
    const std::filesystem::path root{LOL_ASSISTANT_SOURCE_ROOT};
    TestBackendCliContract(root);
    TestProductionDesktopAuthoritativePolicy();
    TestSelectedDesktopFeedsControlAndBudgets();
    TestFullTransportHashContract();
    TestThrottlePendingNewestAndInvalidation();
    TestGlobalVisibilityFailClosed();
    TestRestartWindowRearmsAndStallWatchdog();
    TestProductWiringAndForbiddenApiGuard(root);
    std::cout << "phase5_live_integration_test passed: backends=3; "
                 "selected=desktop; age_gate=800ms; hash=full; "
                 "visibility=8/8; pending=due-once; f9=1+1; "
                 "restart_window=3/60s; stall_watchdog=visible-only; "
                 "sample_storm=1/70; forbidden_api=0\n";
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "phase5_live_integration_test failed: " << error.what()
              << '\n';
    return 1;
  }
}

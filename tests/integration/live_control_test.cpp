#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "live_control.h"

namespace {

using namespace std::chrono_literals;
using lol_assistant::app::BoundedSamplePolicy;
using lol_assistant::app::CaptureHealthState;
using lol_assistant::app::DualSourceArbiter;
using lol_assistant::app::ForceEventKind;
using lol_assistant::app::ForceRecognitionConfig;
using lol_assistant::app::ForceRecognitionController;
using lol_assistant::app::ForceTerminalReason;
using lol_assistant::app::FrameFreshness;
using lol_assistant::app::FrameFreshnessGate;
using lol_assistant::app::FrameTransportInput;
using lol_assistant::app::HealthReason;
using lol_assistant::app::MonotonicTime;
using lol_assistant::app::SampleDecision;
using lol_assistant::app::SamplePolicyInput;
using lol_assistant::app::SampleTrigger;

void Require(const bool condition, const std::string& message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

class FakeClock final {
 public:
  [[nodiscard]] MonotonicTime Now() const noexcept { return now_; }
  void Advance(const std::chrono::milliseconds amount) noexcept {
    now_ += amount;
  }

 private:
  MonotonicTime now_{};
};

FrameTransportInput Frame(std::string source, const std::uint64_t epoch,
                          const std::optional<std::int64_t> source_time,
                          std::string hash, const std::uint64_t progress,
                          const MonotonicTime observed_at) {
  FrameTransportInput input;
  input.source = std::move(source);
  input.epoch = epoch;
  input.source_time = source_time;
  input.content_hash = std::move(hash);
  input.progress = progress;
  input.observed_at = observed_at;
  return input;
}

void TestDesktopAuthoritativeAndStrongWgcFallback() {
  FakeClock clock;
  FrameFreshnessGate gate;
  DualSourceArbiter arbiter;

  const auto wgc_first = gate.Observe(
      Frame("wgc", 1U, 100, "offer-a", 1U, clock.Now()));
  const auto desktop_first = gate.Observe(
      Frame("desktop", 7U, 100, "offer-a", 1U, clock.Now()));
  const auto initial = arbiter.Decide(wgc_first, desktop_first);
  Require(initial.state == CaptureHealthState::DesktopFallback &&
              initial.selected_source ==
                  std::optional<std::string>{"desktop"} &&
              initial.reason == HealthReason::DesktopAuthoritative,
          "a fresh desktop frame must be authoritative even when WGC moves");

  clock.Advance(10ms);
  const auto wgc_stale = gate.Observe(
      Frame("wgc", 1U, 100, "offer-a", 2U, clock.Now()));
  const auto desktop_new = gate.Observe(
      Frame("desktop", 7U, 200, "offer-b", 2U, clock.Now()));
  Require(!wgc_stale.process_allowed && desktop_new.process_allowed,
          "duplicate WGC source time must be rejected before processing");
  const auto desktop_selected = arbiter.Decide(wgc_stale, desktop_new);
  Require(desktop_selected.state == CaptureHealthState::DesktopFallback &&
              desktop_selected.selected_source ==
                  std::optional<std::string>{"desktop"} &&
              desktop_selected.reason == HealthReason::DesktopAuthoritative,
          "stale WGC must never displace a fresh authoritative desktop");

  clock.Advance(10ms);
  const auto wgc_rebuilt = gate.Observe(
      Frame("wgc", 2U, 300, "offer-b", 1U, clock.Now()));
  const auto fallback = arbiter.Decide(
      std::optional<lol_assistant::app::FrameFreshnessObservation>{
          wgc_rebuilt},
      std::nullopt);
  Require(fallback.state == CaptureHealthState::Degraded &&
              fallback.selected_source ==
                  std::optional<std::string>{"wgc"} &&
              fallback.reason == HealthReason::DesktopUnavailableWgcFresh,
          "WGC may be a degraded fallback only with a producer source time");

  clock.Advance(10ms);
  const auto unproven_wgc = gate.Observe(
      Frame("wgc", 3U, std::nullopt, "offer-c", 1U, clock.Now()));
  const auto fail_closed = arbiter.Decide(
      std::optional<lol_assistant::app::FrameFreshnessObservation>{
          unproven_wgc},
      std::nullopt);
  Require(!fail_closed.selected_source.has_value() &&
              fail_closed.reason == HealthReason::WgcFreshnessUnproven,
          "source-time-free WGC activity must fail closed");
}

void TestLegitimateStaticContentRemainsHealthy() {
  FakeClock clock;
  FrameFreshnessGate gate;
  DualSourceArbiter arbiter;

  auto wgc = gate.Observe(
      Frame("wgc", 11U, 1'000, "static-offer", 1U, clock.Now()));
  auto desktop = gate.Observe(
      Frame("desktop", 4U, 1'000, "static-offer", 1U, clock.Now()));
  (void)arbiter.Decide(wgc, desktop);

  clock.Advance(16ms);
  wgc = gate.Observe(
      Frame("wgc", 11U, 1'016, "static-offer", 2U, clock.Now()));
  desktop = gate.Observe(
      Frame("desktop", 4U, 1'016, "static-offer", 2U, clock.Now()));
  const auto decision = arbiter.Decide(wgc, desktop);
  Require(wgc.process_allowed && !wgc.content_changed &&
              desktop.process_allowed && !desktop.content_changed,
          "unchanged pixels with advancing source clocks are fresh");
  Require(decision.state == CaptureHealthState::DesktopFallback &&
              decision.selected_source ==
                  std::optional<std::string>{"desktop"} &&
              !decision.request_wgc_restart,
          "legal static desktop content remains authoritative");
}

void TestFreshnessEpochAndMissingSourceTime() {
  FakeClock clock;
  FrameFreshnessGate gate;
  const auto first = gate.Observe(
      Frame("wgc", 3U, std::nullopt, "a", 10U, clock.Now()));
  Require(first.process_allowed &&
              first.freshness == FrameFreshness::FreshSourceTimeMissing,
          "the generic gate records source-time-free transport activity");
  clock.Advance(1ms);
  const auto duplicate_progress = gate.Observe(
      Frame("wgc", 3U, std::nullopt, "a", 10U, clock.Now()));
  Require(!duplicate_progress.process_allowed &&
              duplicate_progress.freshness ==
                  FrameFreshness::DuplicateProgress,
          "missing source time requires advancing progress");
  const auto old_epoch = gate.Observe(
      Frame("wgc", 2U, 100, "b", 11U, clock.Now()));
  Require(!old_epoch.process_allowed &&
              old_epoch.freshness == FrameFreshness::RegressedEpoch,
          "callbacks from an old capture epoch must be quarantined");

  DualSourceArbiter arbiter;
  const auto decision = arbiter.Decide(
      std::optional<lol_assistant::app::FrameFreshnessObservation>{first},
      std::nullopt);
  Require(!decision.selected_source.has_value() &&
              decision.reason == HealthReason::WgcFreshnessUnproven,
          "WGC missing source time must not pass the product arbiter");
}

void TestForceRecognitionExactlyOnceAndStaleBudget() {
  FakeClock clock;
  ForceRecognitionConfig config;
  config.enabled = true;
  config.fresh_frame_budget = 3U;
  config.deadline = 100ms;
  ForceRecognitionController controller(config);
  controller.Prime(false, clock.Now());

  Require(controller.Poll(false, "wgc", clock.Now()).empty(),
          "an unchanged key state must not start a request");
  auto events = controller.Poll(true, "desktop", clock.Now());
  Require(events.size() == 1U &&
              events.front().kind == ForceEventKind::Started &&
              events.front().source == "desktop",
          "F9 rising edge must start exactly one request on active source");

  for (int index = 0; index < 8; ++index) {
    const auto stale_events =
        controller.ObserveFrame(false, false, false, clock.Now());
    Require(stale_events.empty(),
            "transport-stale frames must not terminate an F9 request");
  }
  auto snapshot = controller.Snapshot(clock.Now());
  Require(snapshot.fresh_frames_remaining == 3U &&
              snapshot.stale_frames_skipped == 8U,
          "stale frames must not consume the fresh-frame burst budget");

  events = controller.ObserveFrame(true, true, false, clock.Now());
  Require(events.size() == 1U &&
              events.front().kind == ForceEventKind::Terminal &&
              events.front().terminal_reason ==
                  ForceTerminalReason::Accepted,
          "an accepted fresh frame must emit one terminal event");
  Require(controller.Tick(clock.Now()).empty(),
          "a completed request must never emit a second terminal");
  snapshot = controller.Snapshot(clock.Now());
  Require(snapshot.started_count == 1U && snapshot.terminal_count == 1U,
          "one F9 request must have exactly one started and one terminal");
}

void TestForceRecognitionTerminalReasons() {
  FakeClock clock;
  ForceRecognitionConfig config;
  config.enabled = true;
  config.fresh_frame_budget = 2U;
  config.deadline = 50ms;
  ForceRecognitionController no_fresh(config);
  auto events = no_fresh.Request("wgc", clock.Now());
  Require(events.size() == 1U && events[0].kind == ForceEventKind::Started,
          "explicit force request must emit started");
  (void)no_fresh.ObserveFrame(false, false, false, clock.Now());
  clock.Advance(51ms);
  events = no_fresh.Tick(clock.Now());
  Require(events.size() == 1U &&
              events[0].terminal_reason == ForceTerminalReason::NoFreshFrame,
          "deadline without a fresh frame needs a distinct terminal reason");

  ForceRecognitionController duplicate(config);
  events = duplicate.Request("desktop", clock.Now());
  events = duplicate.ObserveFrame(true, false, true, clock.Now());
  Require(events.size() == 1U &&
              events[0].terminal_reason ==
                  ForceTerminalReason::FreshDuplicate,
          "a transport-fresh content duplicate needs a distinct terminal");

  config.enabled = false;
  ForceRecognitionController disabled(config);
  events = disabled.Request("desktop", clock.Now());
  Require(events.size() == 2U &&
              events[0].kind == ForceEventKind::Started &&
              events[1].kind == ForceEventKind::Terminal &&
              events[1].terminal_reason == ForceTerminalReason::Disabled,
          "a disabled request must still close its observable lifecycle");

  config.enabled = true;
  ForceRecognitionController session_end(config);
  (void)session_end.Request("wgc", clock.Now());
  events = session_end.EndSession(clock.Now());
  Require(events.size() == 1U &&
              events[0].terminal_reason == ForceTerminalReason::SessionEnd,
          "session shutdown must close an active request exactly once");
}

SamplePolicyInput Sample(std::string content, std::string reason,
                         const bool fresh = true) {
  SamplePolicyInput input;
  input.trigger = SampleTrigger::Auto;
  input.transport_fresh = fresh;
  input.source = "wgc";
  input.epoch = 9U;
  input.content_key = std::move(content);
  input.reason = std::move(reason);
  input.auto_eligible = true;
  return input;
}

void TestSeventyFrameSampleStormBudget() {
  BoundedSamplePolicy policy;
  std::uint32_t allowed = 0U;
  std::uint32_t suppressed = 0U;
  for (int frame = 0; frame < 70; ++frame) {
    const auto decision = policy.Evaluate(Sample("offer-a", "ocr_unknown"));
    allowed += decision == SampleDecision::Allow ? 1U : 0U;
    suppressed +=
        decision == SampleDecision::SuppressPerContentReasonBudget ? 1U : 0U;
  }
  const auto snapshot = policy.Snapshot();
  Require(allowed == 1U && suppressed == 69U,
          "70 identical eligible frames must consume one default key budget");
  Require(snapshot.allowed == 1U && snapshot.session_budget_remaining == 19U,
          "default session sample budget must remain bounded at twenty");
}

void TestStaleSamplingDoesNotConsumeBudget() {
  BoundedSamplePolicy policy;
  for (int frame = 0; frame < 70; ++frame) {
    Require(policy.Evaluate(Sample("offer-a", "ocr_unknown", false)) ==
                SampleDecision::SuppressTransportStale,
            "stale samples must be suppressed before key/session budgets");
  }
  auto snapshot = policy.Snapshot();
  Require(snapshot.allowed == 0U && snapshot.session_budget_remaining == 20U &&
              snapshot.suppressed_stale == 70U,
          "stale storm must consume no sample budget");
  Require(policy.Evaluate(Sample("offer-a", "ocr_unknown")) ==
              SampleDecision::Allow,
          "a later fresh frame must retain the original budget");
}

void TestSampleSessionBudgetAndThreadSafety() {
  BoundedSamplePolicy policy;
  std::vector<std::thread> threads;
  std::vector<SampleDecision> decisions(32U);
  threads.reserve(decisions.size());
  for (std::size_t index = 0; index < decisions.size(); ++index) {
    threads.emplace_back([&policy, &decisions, index]() {
      decisions[index] = policy.Evaluate(Sample("same", "icon_conflict"));
    });
  }
  for (auto& thread : threads) {
    thread.join();
  }
  Require(std::count(decisions.begin(), decisions.end(),
                     SampleDecision::Allow) == 1,
          "concurrent callers must atomically share one content/reason budget");

  for (std::uint64_t index = 1U; index < 20U; ++index) {
    Require(policy.Evaluate(Sample("content-" + std::to_string(index),
                                   "low_confidence")) ==
                SampleDecision::Allow,
            "unique eligible keys must consume the session budget");
  }
  Require(policy.Evaluate(Sample("overflow", "low_confidence")) ==
              SampleDecision::SuppressSessionBudget,
          "the twenty-first attempt must hit the session hard cap");
}

}  // namespace

int main() {
  try {
    TestDesktopAuthoritativeAndStrongWgcFallback();
    TestLegitimateStaticContentRemainsHealthy();
    TestFreshnessEpochAndMissingSourceTime();
    TestForceRecognitionExactlyOnceAndStaleBudget();
    TestForceRecognitionTerminalReasons();
    TestSeventyFrameSampleStormBudget();
    TestStaleSamplingDoesNotConsumeBudget();
    TestSampleSessionBudgetAndThreadSafety();
    std::cout << "live_control_test passed: sample_storm allowed=1 "
                 "suppressed=69; stale_allowed=0; f9 started=1 terminal=1; "
                 "dual_source=desktop_authoritative; static=healthy\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "live_control_test failed: " << error.what() << '\n';
    return 1;
  }
}

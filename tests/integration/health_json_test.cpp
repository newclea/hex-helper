#include <chrono>
#include <cstdint>
#include <iostream>
#include <optional>
#include <ostream>
#include <sstream>
#include <stdexcept>
#include <string>

#include "health_json.h"

namespace {

using lol_assistant::app::CaptureHealthSnapshot;
using lol_assistant::app::CaptureHealthState;
using lol_assistant::app::ForceEventKind;
using lol_assistant::app::ForcePhase;
using lol_assistant::app::ForceRecognitionEvent;
using lol_assistant::app::ForceTerminalReason;
using lol_assistant::app::FlushedJsonLineWriter;
using lol_assistant::app::FrameFreshness;
using lol_assistant::app::FrameFreshnessSnapshot;
using lol_assistant::app::HealthJsonSnapshot;
using lol_assistant::app::HealthReason;
using lol_assistant::app::SampleBudgetSnapshot;
using lol_assistant::app::SerializeForceRecognitionEventJson;
using lol_assistant::app::SerializeHealthJson;

void Require(const bool condition, const std::string& message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

class CountingBuffer final : public std::stringbuf {
 public:
  int sync() override {
    ++sync_count;
    return std::stringbuf::sync();
  }

  std::uint32_t sync_count{0U};
};

HealthJsonSnapshot MakeSnapshot() {
  HealthJsonSnapshot health;
  health.session_id = "session-\"one\nline";
  health.event_seq = 42U;
  health.monotonic_ms = 1'876U;
  health.capture.state = CaptureHealthState::DesktopFallback;
  health.capture.reason = HealthReason::WgcStalled;
  health.capture.selected_source = "desktop";
  health.capture.selected_epoch = 7U;
  health.capture.revoke_recommendation = true;
  health.capture.request_wgc_restart = true;
  health.capture.switched_source = true;

  FrameFreshnessSnapshot wgc;
  wgc.source = "wgc";
  wgc.epoch = 2U;
  wgc.source_time = 100;
  wgc.content_hash = "abc123";
  wgc.progress = 8U;
  wgc.last_freshness = FrameFreshness::DuplicateSourceTime;
  wgc.accepted_frames = 7U;
  wgc.rejected_frames = 1U;
  wgc.last_fresh_age_ms = 250;
  health.sources.push_back(wgc);

  health.force.enabled = true;
  health.force.primed_down = false;
  health.force.key_down = true;
  health.force.poll_count = 90U;
  health.force.edge_count = 1U;
  health.force.request_id = 4U;
  health.force.phase = ForcePhase::Active;
  health.force.source = "desktop";
  health.force.fresh_frames_remaining = 4U;
  health.force.stale_frames_skipped = 3U;
  health.force.started_count = 1U;
  health.force.terminal_count = 0U;
  health.force.deadline_remaining_ms = 1'200;

  health.samples.evaluated = 70U;
  health.samples.allowed = 1U;
  health.samples.suppressed_stale = 0U;
  health.samples.suppressed_per_content_reason = 69U;
  health.samples.session_budget = 20U;
  health.samples.session_budget_remaining = 19U;
  return health;
}

void TestHealthSerializationContract() {
  const auto json = SerializeHealthJson(MakeSnapshot());
  Require(!json.empty() && json.front() == '{' && json.back() == '}',
          "health serializer must return one JSON object without newline");
  Require(json.find("\"type\":\"capture_health\"") != std::string::npos &&
              json.find("\"schema_version\":1") != std::string::npos &&
              json.find("\"event_seq\":42") != std::string::npos,
          "health JSON must expose additive type/version/sequence fields");
  Require(json.find("session-\\\"one\\nline") != std::string::npos,
          "JSON strings must escape quote and newline characters");
  Require(json.find("\"state\":\"desktop_fallback\"") !=
                  std::string::npos &&
              json.find("\"selected_source\":\"desktop\"") !=
                  std::string::npos &&
              json.find("\"transport_freshness\":\"duplicate_source_time\"") !=
                  std::string::npos,
          "capture decision and source freshness must be serialized");
  Require(json.find("\"started_count\":1") != std::string::npos &&
              json.find("\"terminal_count\":0") != std::string::npos &&
              json.find("\"suppressed_per_content_reason\":69") !=
                  std::string::npos,
          "F9 and bounded-sampling counters must be observable");
}

void TestForceEventSerializationContract() {
  ForceRecognitionEvent event;
  event.kind = ForceEventKind::Terminal;
  event.request_id = 9U;
  event.source = "desktop";
  event.monotonic_ms = 500;
  event.fresh_frames_seen = 1U;
  event.stale_frames_skipped = 6U;
  event.terminal_reason = ForceTerminalReason::FreshDuplicate;
  const auto json = SerializeForceRecognitionEventJson(event);
  Require(json.find("\"type\":\"force_recognition\"") !=
                  std::string::npos &&
              json.find("\"event\":\"terminal\"") != std::string::npos &&
              json.find("\"request_id\":9") != std::string::npos &&
              json.find("\"terminal_reason\":\"fresh_duplicate\"") !=
                  std::string::npos,
          "force event JSON must preserve exactly-once lifecycle identity");
}

void TestNewlineAndFlushSeam() {
  CountingBuffer buffer;
  std::ostream output(&buffer);
  FlushedJsonLineWriter writer(output);
  const auto health = SerializeHealthJson(MakeSnapshot());
  Require(writer.Write(health), "a valid single-line JSON record must write");
  Require(writer.Write("{\"type\":\"second\"}"),
          "a second JSON record must write independently");
  Require(buffer.str() == health + "\n{\"type\":\"second\"}\n",
          "writer must append exactly one newline per event");
  Require(buffer.sync_count == 2U,
          "writer must flush immediately after every newline");
  Require(!writer.Write("{\"bad\":\"raw\nnewline\"}"),
          "raw multi-line payloads must be rejected at the JSONL seam");
  Require(buffer.sync_count == 2U,
          "a rejected payload must not touch or flush the stream");
}

}  // namespace

int main() {
  try {
    TestHealthSerializationContract();
    TestForceEventSerializationContract();
    TestNewlineAndFlushSeam();
    std::cout << "health_json_test passed: schema=1; jsonl_records=2; "
                 "flushes=2; force_terminal=fresh_duplicate\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "health_json_test failed: " << error.what() << '\n';
    return 1;
  }
}

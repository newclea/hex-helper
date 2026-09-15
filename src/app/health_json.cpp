#include "health_json.h"

#include <iomanip>
#include <optional>
#include <sstream>

namespace lol_assistant::app {
namespace {

void AppendEscaped(std::ostringstream& output, const std::string_view value) {
  output << '"';
  for (const unsigned char character : value) {
    switch (character) {
      case '"':
        output << "\\\"";
        break;
      case '\\':
        output << "\\\\";
        break;
      case '\b':
        output << "\\b";
        break;
      case '\f':
        output << "\\f";
        break;
      case '\n':
        output << "\\n";
        break;
      case '\r':
        output << "\\r";
        break;
      case '\t':
        output << "\\t";
        break;
      default:
        if (character < 0x20U) {
          output << "\\u00" << std::hex << std::setw(2)
                 << std::setfill('0') << static_cast<unsigned int>(character)
                 << std::dec << std::setfill(' ');
        } else {
          output << static_cast<char>(character);
        }
        break;
    }
  }
  output << '"';
}

void AppendBool(std::ostringstream& output, const bool value) {
  output << (value ? "true" : "false");
}

void AppendOptionalString(std::ostringstream& output,
                          const std::optional<std::string>& value) {
  if (value.has_value()) {
    AppendEscaped(output, *value);
  } else {
    output << "null";
  }
}

template <typename Integer>
void AppendOptionalInteger(std::ostringstream& output,
                           const std::optional<Integer>& value) {
  if (value.has_value()) {
    output << *value;
  } else {
    output << "null";
  }
}

void AppendForceTerminalReason(
    std::ostringstream& output,
    const std::optional<ForceTerminalReason>& reason) {
  if (reason.has_value()) {
    AppendEscaped(output, ToString(*reason));
  } else {
    output << "null";
  }
}

void AppendCapture(std::ostringstream& output,
                   const CaptureHealthSnapshot& capture) {
  output << "{\"state\":";
  AppendEscaped(output, ToString(capture.state));
  output << ",\"reason\":";
  AppendEscaped(output, ToString(capture.reason));
  output << ",\"selected_source\":";
  AppendOptionalString(output, capture.selected_source);
  output << ",\"selected_epoch\":";
  AppendOptionalInteger(output, capture.selected_epoch);
  output << ",\"revoke_recommendation\":";
  AppendBool(output, capture.revoke_recommendation);
  output << ",\"request_wgc_restart\":";
  AppendBool(output, capture.request_wgc_restart);
  output << ",\"switched_source\":";
  AppendBool(output, capture.switched_source);
  output << '}';
}

void AppendSource(std::ostringstream& output,
                  const FrameFreshnessSnapshot& source) {
  output << "{\"source\":";
  AppendEscaped(output, source.source);
  output << ",\"epoch\":" << source.epoch << ",\"source_time\":";
  AppendOptionalInteger(output, source.source_time);
  output << ",\"content_hash\":";
  AppendEscaped(output, source.content_hash);
  output << ",\"progress\":" << source.progress
         << ",\"transport_freshness\":";
  AppendEscaped(output, ToString(source.last_freshness));
  output << ",\"accepted_frames\":" << source.accepted_frames
         << ",\"rejected_frames\":" << source.rejected_frames
         << ",\"last_fresh_age_ms\":";
  AppendOptionalInteger(output, source.last_fresh_age_ms);
  output << '}';
}

void AppendForce(std::ostringstream& output,
                 const ForceRecognitionSnapshot& force) {
  output << "{\"enabled\":";
  AppendBool(output, force.enabled);
  output << ",\"primed_down\":";
  AppendBool(output, force.primed_down);
  output << ",\"key_down\":";
  AppendBool(output, force.key_down);
  output << ",\"poll_count\":" << force.poll_count
         << ",\"edge_count\":" << force.edge_count
         << ",\"request_id\":" << force.request_id << ",\"phase\":";
  AppendEscaped(output, ToString(force.phase));
  output << ",\"source\":";
  AppendEscaped(output, force.source);
  output << ",\"fresh_frames_remaining\":"
         << force.fresh_frames_remaining << ",\"fresh_frames_seen\":"
         << force.fresh_frames_seen << ",\"stale_frames_skipped\":"
         << force.stale_frames_skipped << ",\"started_count\":"
         << force.started_count << ",\"terminal_count\":"
         << force.terminal_count << ",\"last_poll_age_ms\":";
  AppendOptionalInteger(output, force.last_poll_age_ms);
  output << ",\"last_edge_age_ms\":";
  AppendOptionalInteger(output, force.last_edge_age_ms);
  output << ",\"deadline_remaining_ms\":";
  AppendOptionalInteger(output, force.deadline_remaining_ms);
  output << ",\"terminal_reason\":";
  AppendForceTerminalReason(output, force.terminal_reason);
  output << '}';
}

void AppendSamples(std::ostringstream& output,
                   const SampleBudgetSnapshot& samples) {
  output << "{\"evaluated\":" << samples.evaluated
         << ",\"allowed\":" << samples.allowed
         << ",\"suppressed_stale\":" << samples.suppressed_stale
         << ",\"suppressed_not_eligible\":"
         << samples.suppressed_not_eligible
         << ",\"suppressed_invalid_identity\":"
         << samples.suppressed_invalid_identity
         << ",\"suppressed_per_content_reason\":"
         << samples.suppressed_per_content_reason
         << ",\"suppressed_session_budget\":"
         << samples.suppressed_session_budget
         << ",\"per_content_reason_budget\":"
         << samples.per_content_reason_budget << ",\"session_budget\":"
         << samples.session_budget << ",\"session_budget_remaining\":"
         << samples.session_budget_remaining << '}';
}

}  // namespace

std::string SerializeHealthJson(const HealthJsonSnapshot& snapshot) {
  std::ostringstream output;
  output << "{\"type\":\"capture_health\",\"schema_version\":"
         << snapshot.schema_version << ",\"session_id\":";
  AppendEscaped(output, snapshot.session_id);
  output << ",\"event_seq\":" << snapshot.event_seq
         << ",\"monotonic_ms\":" << snapshot.monotonic_ms
         << ",\"capture\":";
  AppendCapture(output, snapshot.capture);
  output << ",\"sources\":[";
  for (std::size_t index = 0; index < snapshot.sources.size(); ++index) {
    if (index > 0U) {
      output << ',';
    }
    AppendSource(output, snapshot.sources[index]);
  }
  output << "],\"force_recognition\":";
  AppendForce(output, snapshot.force);
  output << ",\"sample_budget\":";
  AppendSamples(output, snapshot.samples);
  output << '}';
  return output.str();
}

std::string SerializeForceRecognitionEventJson(
    const ForceRecognitionEvent& event) {
  std::ostringstream output;
  output << "{\"type\":\"force_recognition\",\"schema_version\":1,"
            "\"event\":";
  AppendEscaped(output, ToString(event.kind));
  output << ",\"request_id\":" << event.request_id << ",\"source\":";
  AppendEscaped(output, event.source);
  output << ",\"monotonic_ms\":" << event.monotonic_ms
         << ",\"fresh_frames_seen\":" << event.fresh_frames_seen
         << ",\"stale_frames_skipped\":" << event.stale_frames_skipped
         << ",\"terminal_reason\":";
  AppendForceTerminalReason(output, event.terminal_reason);
  output << '}';
  return output.str();
}

FlushedJsonLineWriter::FlushedJsonLineWriter(std::ostream& output) noexcept
    : output_(&output) {}

bool FlushedJsonLineWriter::Write(const std::string_view json) {
  if (json.empty() || json.find_first_of("\r\n") != std::string_view::npos) {
    return false;
  }
  std::lock_guard lock(mutex_);
  output_->write(json.data(), static_cast<std::streamsize>(json.size()));
  output_->put('\n');
  output_->flush();
  return output_->good();
}

}  // namespace lol_assistant::app

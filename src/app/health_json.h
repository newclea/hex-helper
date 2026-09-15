#pragma once

#include <cstdint>
#include <mutex>
#include <ostream>
#include <string>
#include <string_view>
#include <vector>

#include "live_control.h"

namespace lol_assistant::app {

struct HealthJsonSnapshot final {
  std::uint32_t schema_version{1U};
  std::string session_id{};
  std::uint64_t event_seq{0U};
  std::int64_t monotonic_ms{0};
  CaptureHealthSnapshot capture{};
  std::vector<FrameFreshnessSnapshot> sources{};
  ForceRecognitionSnapshot force{};
  SampleBudgetSnapshot samples{};
};

// Stateless deterministic serializers. Returned strings never include a
// trailing newline, so the JSONL/flush seam remains independently testable.
[[nodiscard]] std::string SerializeHealthJson(
    const HealthJsonSnapshot& snapshot);
[[nodiscard]] std::string SerializeForceRecognitionEventJson(
    const ForceRecognitionEvent& event);

// Thread safety: Write serializes write + newline + flush under one mutex.
// Payloads containing raw CR/LF are rejected so one call is exactly one record.
class FlushedJsonLineWriter final {
 public:
  explicit FlushedJsonLineWriter(std::ostream& output) noexcept;
  FlushedJsonLineWriter(const FlushedJsonLineWriter&) = delete;
  FlushedJsonLineWriter& operator=(const FlushedJsonLineWriter&) = delete;

  [[nodiscard]] bool Write(std::string_view json);

 private:
  std::ostream* output_;
  std::mutex mutex_{};
};

}  // namespace lol_assistant::app

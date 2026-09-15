#pragma once

#include <chrono>
#include <optional>

namespace lol_assistant::common {

using UtcTimestamp = std::chrono::system_clock::time_point;
using MonotonicTimestamp = std::chrono::steady_clock::time_point;

// source_timestamp is relative to the source's own media clock. The capture
// timestamps use steady_clock and must never be compared with captured_at_utc.
struct FrameTimestamps final {
  std::optional<std::chrono::nanoseconds> source_timestamp{};
  std::optional<MonotonicTimestamp> capture_started{};
  std::optional<MonotonicTimestamp> capture_completed{};
  std::optional<UtcTimestamp> captured_at_utc{};

  [[nodiscard]] bool IsValid() const noexcept;
};

}  // namespace lol_assistant::common

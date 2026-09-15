#pragma once

#include <optional>
#include <string>
#include <string_view>

#include "lol_assistant/common/confidence.h"
#include "lol_assistant/common/timestamp.h"

namespace lol_assistant::common {

inline constexpr std::string_view kUnknownObservationSource = "unknown";
inline constexpr std::string_view kStubObservationSource = "stub";

// Shared provenance for detector, recognition, offer, and game-state records.
// Unknown observations have confidence=0 and no timestamp by default.
struct ObservationMetadata final {
  std::string source{std::string{kUnknownObservationSource}};
  Confidence confidence{};
  std::optional<UtcTimestamp> observed_at{};

  [[nodiscard]] bool IsValid() const noexcept;
  [[nodiscard]] bool IsStub() const noexcept;
  [[nodiscard]] static ObservationMetadata Stub();
};

}  // namespace lol_assistant::common

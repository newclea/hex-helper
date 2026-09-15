#pragma once

#include <optional>

namespace lol_assistant::common {

// Confidence is always a normalized value in the inclusive range [0, 1].
// The default value is deliberately zero and does not imply a positive result.
struct Confidence final {
  float value{0.0F};

  [[nodiscard]] bool IsValid() const noexcept;
  [[nodiscard]] static std::optional<Confidence> TryCreate(float value) noexcept;

  friend bool operator==(const Confidence&, const Confidence&) = default;
};

}  // namespace lol_assistant::common

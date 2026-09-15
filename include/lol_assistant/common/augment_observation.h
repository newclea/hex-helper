#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>

#include "lol_assistant/common/geometry.h"
#include "lol_assistant/common/observation.h"

namespace lol_assistant::common {

inline constexpr std::size_t kAugmentCardCount = 3U;

enum class DetectionState : std::uint8_t {
  Unknown = 0,
  NotDetected = 1,
  Detected = 2,
};

struct AugmentScreenDetection final {
  DetectionState state{DetectionState::Unknown};
  std::optional<NormalizedRoi> offer_roi{};
  std::array<std::optional<CardSlot>, kAugmentCardCount> card_slots{};
  ObservationMetadata metadata{};

  [[nodiscard]] bool IsValid() const noexcept;
};

enum class RecognitionState : std::uint8_t {
  Unknown = 0,
  NotRecognized = 1,
  Recognized = 2,
};

struct AugmentRecognition final {
  RecognitionState state{RecognitionState::Unknown};
  CardSlotId slot{CardSlotId::Unknown};
  std::optional<std::string> augment_id{};
  std::optional<std::string> display_name{};
  ObservationMetadata metadata{};

  [[nodiscard]] bool IsValid() const noexcept;
};

struct AugmentOfferObservation final {
  std::optional<AugmentScreenDetection> screen_detection{};
  std::array<std::optional<AugmentRecognition>, kAugmentCardCount> recognitions{};
  ObservationMetadata metadata{};

  [[nodiscard]] bool IsValid() const noexcept;
};

}  // namespace lol_assistant::common

#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "lol_assistant/common/augment_observation.h"
#include "lol_assistant/common/observation.h"

namespace lol_assistant::common {

struct GameState final {
  std::optional<std::string> champion{};
  std::optional<std::uint32_t> offer_round{};
  std::vector<AugmentRecognition> selected_augments{};
  std::optional<AugmentOfferObservation> current_offer{};
  ObservationMetadata metadata{};

  [[nodiscard]] bool IsValid() const noexcept;
};

}  // namespace lol_assistant::common

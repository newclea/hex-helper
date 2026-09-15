#pragma once

#include <array>
#include <optional>
#include <string>

#include "lol_assistant/common/game_state.h"

namespace lol_assistant::output {

// Deterministic UTF-8 JSON serialization for the shared observation contracts.
// Unknown values remain explicitly UNKNOWN/null and are never promoted to a
// detected or recognized fact.
class StructuredJsonWriter final {
 public:
  [[nodiscard]] static std::string WriteGameState(
      const common::GameState& state);
  [[nodiscard]] static std::string WriteScreenDetection(
      const common::AugmentScreenDetection& detection);
  [[nodiscard]] static std::string WriteRecognitions(
      const std::array<std::optional<common::AugmentRecognition>,
                       common::kAugmentCardCount>& recognitions);
};

}  // namespace lol_assistant::output

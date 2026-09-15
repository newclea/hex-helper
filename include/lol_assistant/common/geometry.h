#pragma once

#include <cstdint>

namespace lol_assistant::common {

// Coordinates are normalized to the containing frame: top-left is (0, 0),
// bottom-right is (1, 1). Width and height must both be positive.
struct NormalizedRoi final {
  double x{0.0};
  double y{0.0};
  double width{0.0};
  double height{0.0};

  [[nodiscard]] bool IsValid() const noexcept;
};

enum class CardSlotId : std::uint8_t {
  Unknown = 0,
  Left = 1,
  Center = 2,
  Right = 3,
};

struct CardSlot final {
  CardSlotId id{CardSlotId::Unknown};
  NormalizedRoi roi{};

  [[nodiscard]] bool IsValid() const noexcept;
};

}  // namespace lol_assistant::common

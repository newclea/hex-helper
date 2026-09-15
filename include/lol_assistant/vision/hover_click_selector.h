#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>

#include "lol_assistant/common/augment_observation.h"

namespace lol_assistant::vision {

struct HoverClickConfig final {
  // Hovered Arena cards light one gold frame more than the other two.
  float unique_lead{24.0F};
  float minimum_winner_luma{48.0F};
  std::uint32_t stable_frames{2U};
  std::uint32_t grace_frames{4U};

  [[nodiscard]] bool IsValid() const noexcept;
};

// Tracks which of the three offer cards is glowing, then confirms a left
// click only while that glow is still active or inside a short grace window.
class HoverClickSelector final {
 public:
  explicit HoverClickSelector(HoverClickConfig config = {});

  void ObserveLuma(
      const std::array<float, common::kAugmentCardCount> &luma) noexcept;

  [[nodiscard]] std::optional<std::size_t> glowing_slot() const noexcept {
    return glowing_slot_;
  }
  [[nodiscard]] bool tracking_glow() const noexcept {
    return pending_slot_.has_value() || glowing_slot_.has_value();
  }

  // Consumes a left-click edge. Returns the glowing slot when the click
  // happened on a currently (or just-faded) hover glow.
  [[nodiscard]] std::optional<std::size_t> ConfirmClick() noexcept;

  void MarkEmitted() noexcept;
  void ResetForNewOffer() noexcept;

  [[nodiscard]] bool emitted() const noexcept { return emitted_; }

 private:
  HoverClickConfig config_{};
  std::optional<std::size_t> pending_slot_{};
  std::uint32_t pending_frames_{0U};
  std::optional<std::size_t> glowing_slot_{};
  std::uint32_t grace_remaining_{0U};
  bool emitted_{false};
};

// One card's inner area spikes while the other two stay dim: the pick flash.
[[nodiscard]] std::optional<std::size_t> DetectCardFlashSelection(
    const std::array<float, common::kAugmentCardCount> &inner,
    const std::array<float, common::kAugmentCardCount> *previous_inner =
        nullptr) noexcept;

// The chosen card stays lit while the other two collapse off the board.
[[nodiscard]] std::optional<std::size_t> DetectCardCollapseSelection(
    const std::array<float, common::kAugmentCardCount> &inner,
    const std::array<float, common::kAugmentCardCount> &previous_inner) noexcept;

// A slot still holds a card when it does not look like the empty alley
// between cards. Values are normalized correlation vs that background.
[[nodiscard]] bool CardLooksPresent(float background_likeness) noexcept;
[[nodiscard]] bool CardLooksGone(float background_likeness) noexcept;

// Exactly one card remains; the other two look like empty background.
[[nodiscard]] std::optional<std::size_t> DetectSoleRemainingCard(
    const std::array<float, common::kAugmentCardCount>
        &background_likeness) noexcept;

// Auto pick: emit only after three cards were present, then two vanish.
class CardPickTracker final {
 public:
  [[nodiscard]] std::optional<std::size_t> Observe(
      const std::array<float, common::kAugmentCardCount>
          &background_likeness) noexcept;
  void ArmOffer() noexcept { offer_was_visible_ = true; }
  void Reset() noexcept;
  [[nodiscard]] bool emitted() const noexcept { return emitted_; }
  [[nodiscard]] bool offer_seen() const noexcept { return offer_was_visible_; }

 private:
  bool offer_was_visible_{false};
  std::optional<std::size_t> last_remaining_{};
  std::optional<std::size_t> pending_{};
  bool emitted_{false};
};

}  // namespace lol_assistant::vision

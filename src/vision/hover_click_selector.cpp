#include "lol_assistant/vision/hover_click_selector.h"

#include <algorithm>
#include <cmath>

namespace lol_assistant::vision {

bool HoverClickConfig::IsValid() const noexcept {
  return std::isfinite(unique_lead) && unique_lead > 0.0F &&
         std::isfinite(minimum_winner_luma) && minimum_winner_luma >= 0.0F &&
         stable_frames > 0U && grace_frames > 0U;
}

HoverClickSelector::HoverClickSelector(HoverClickConfig config)
    : config_(config.IsValid() ? config : HoverClickConfig{}) {}

void HoverClickSelector::ObserveLuma(
    const std::array<float, common::kAugmentCardCount> &luma) noexcept {
  if (emitted_) {
    return;
  }
  std::size_t winner = 0U;
  for (std::size_t index = 1U; index < luma.size(); ++index) {
    if (luma[index] > luma[winner]) {
      winner = index;
    }
  }
  float second = -1.0e9F;
  for (std::size_t index = 0U; index < luma.size(); ++index) {
    if (index == winner) {
      continue;
    }
    second = std::max(second, luma[index]);
  }
  const bool glowing = luma[winner] >= config_.minimum_winner_luma &&
                       luma[winner] - second >= config_.unique_lead;
  if (glowing) {
    if (pending_slot_ == winner) {
      pending_frames_ = std::min(pending_frames_ + 1U, config_.stable_frames);
    } else {
      pending_slot_ = winner;
      pending_frames_ = 1U;
    }
    if (pending_frames_ >= config_.stable_frames) {
      glowing_slot_ = winner;
      grace_remaining_ = config_.grace_frames;
    }
    return;
  }
  pending_slot_.reset();
  pending_frames_ = 0U;
  if (glowing_slot_.has_value() && grace_remaining_ > 0U) {
    --grace_remaining_;
    if (grace_remaining_ == 0U) {
      glowing_slot_.reset();
    }
  }
}

std::optional<std::size_t> HoverClickSelector::ConfirmClick() noexcept {
  if (emitted_ || !glowing_slot_.has_value()) {
    return std::nullopt;
  }
  emitted_ = true;
  return glowing_slot_;
}

void HoverClickSelector::MarkEmitted() noexcept { emitted_ = true; }

void HoverClickSelector::ResetForNewOffer() noexcept {
  pending_slot_.reset();
  pending_frames_ = 0U;
  glowing_slot_.reset();
  grace_remaining_ = 0U;
  emitted_ = false;
}

std::optional<std::size_t> DetectCardFlashSelection(
    const std::array<float, common::kAugmentCardCount> &inner,
    const std::array<float, common::kAugmentCardCount> *previous_inner) noexcept {
  std::size_t winner = 0U;
  for (std::size_t index = 1U; index < inner.size(); ++index) {
    if (inner[index] > inner[winner]) {
      winner = index;
    }
  }
  float second = -1.0e9F;
  for (std::size_t index = 0U; index < inner.size(); ++index) {
    if (index != winner) {
      second = std::max(second, inner[index]);
    }
  }
  const float lead = inner[winner] - second;
  constexpr float kMinimumWinner = 80.0F;
  constexpr float kUniqueLead = 30.0F;
  constexpr float kStrongWinner = 110.0F;
  constexpr float kMinimumJump = 18.0F;
  if (inner[winner] < kMinimumWinner || lead < kUniqueLead) {
    return std::nullopt;
  }
  const bool strong = inner[winner] >= kStrongWinner;
  if (previous_inner == nullptr) {
    return winner;
  }
  const float jump = inner[winner] - (*previous_inner)[winner];
  if (strong || jump >= kMinimumJump) {
    return winner;
  }
  return std::nullopt;
}

std::optional<std::size_t> DetectCardCollapseSelection(
    const std::array<float, common::kAugmentCardCount> &inner,
    const std::array<float, common::kAugmentCardCount> &previous_inner) noexcept {
  float previous_min = previous_inner[0];
  float previous_max = previous_inner[0];
  for (std::size_t index = 1U; index < previous_inner.size(); ++index) {
    previous_min = std::min(previous_min, previous_inner[index]);
    previous_max = std::max(previous_max, previous_inner[index]);
  }
  if (previous_min < 28.0F || previous_max - previous_min > 50.0F) {
    return std::nullopt;
  }
  std::size_t winner = 0U;
  for (std::size_t index = 1U; index < inner.size(); ++index) {
    if (inner[index] > inner[winner]) {
      winner = index;
    }
  }
  std::uint32_t collapsed = 0U;
  for (std::size_t index = 0U; index < inner.size(); ++index) {
    if (index == winner) {
      continue;
    }
    if (previous_inner[index] - inner[index] >= 12.0F || inner[index] < 42.0F) {
      ++collapsed;
    }
  }
  const bool winner_held =
      inner[winner] >= previous_inner[winner] - 8.0F && inner[winner] >= 50.0F;
  const bool winner_flash = inner[winner] - previous_inner[winner] >= 15.0F;
  if (collapsed == 2U && (winner_held || winner_flash)) {
    return winner;
  }
  return std::nullopt;
}

bool CardLooksPresent(const float background_likeness) noexcept {
  return std::isfinite(background_likeness) && background_likeness < 0.28F;
}

bool CardLooksGone(const float background_likeness) noexcept {
  return std::isfinite(background_likeness) && background_likeness >= 0.40F;
}

std::optional<std::size_t> DetectSoleRemainingCard(
    const std::array<float, common::kAugmentCardCount>
        &background_likeness) noexcept {
  std::optional<std::size_t> remaining{};
  std::uint32_t present = 0U;
  std::uint32_t gone = 0U;
  for (std::size_t index = 0U; index < background_likeness.size(); ++index) {
    if (CardLooksPresent(background_likeness[index])) {
      ++present;
      remaining = index;
      continue;
    }
    if (CardLooksGone(background_likeness[index])) {
      ++gone;
    }
  }
  if (present == 1U && gone == 2U) {
    return remaining;
  }
  return std::nullopt;
}

std::optional<std::size_t> CardPickTracker::Observe(
    const std::array<float, common::kAugmentCardCount>
        &background_likeness) noexcept {
  if (emitted_) {
    return std::nullopt;
  }
  std::uint32_t present = 0U;
  std::uint32_t gone = 0U;
  std::optional<std::size_t> sole_present;
  for (std::size_t index = 0U; index < background_likeness.size(); ++index) {
    const float value = background_likeness[index];
    if (CardLooksPresent(value)) {
      ++present;
      sole_present = index;
      continue;
    }
    if (CardLooksGone(value)) {
      ++gone;
    }
  }
  if (present >= 2U) {
    offer_was_visible_ = true;
  }
  if (present == 1U) {
    last_remaining_ = sole_present;
  }
  if (!offer_was_visible_) {
    return std::nullopt;
  }
  if (const auto remaining = DetectSoleRemainingCard(background_likeness);
      remaining.has_value()) {
    emitted_ = true;
    pending_ = remaining;
    last_remaining_ = remaining;
    return remaining;
  }
  if (present == 1U && gone >= 1U) {
    emitted_ = true;
    pending_ = sole_present;
    return sole_present;
  }
  if (present == 0U && gone >= 2U && last_remaining_.has_value()) {
    emitted_ = true;
    pending_ = last_remaining_;
    return last_remaining_;
  }
  return std::nullopt;
}

void CardPickTracker::Reset() noexcept {
  offer_was_visible_ = false;
  last_remaining_.reset();
  pending_.reset();
  emitted_ = false;
}

}  // namespace lol_assistant::vision

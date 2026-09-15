#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "lol_assistant/common/frame.h"
#include "lol_assistant/common/geometry.h"
#include "lol_assistant/detector/roi.h"
#include "lol_assistant/vision/icon_matcher.h"

namespace lol_assistant::vision {

inline constexpr std::size_t kHudOwnedSlotCount = 4U;

// Row-major 2x2 HUD icon geometry. Values are normalized to the measured
// 2560x1600 UI canvas. Product conversion scales the canvas uniformly from the
// captured frame height and keeps it left/bottom anchored, so the same layout
// remains geometrically meaningful on wider 16:9 captures.
struct HudOwnedSlotLayout final {
  std::array<common::NormalizedRoi, kHudOwnedSlotCount> icon_regions{};

  [[nodiscard]] bool IsValid() const noexcept;

  // Measured from the 2560x1600 real frames captured at 18:22, 18:26, and
  // 18:32. The four 79x79 slot ROIs begin at (398,1408), (488,1408),
  // (398,1503), and (488,1503), respectively.
  [[nodiscard]] static HudOwnedSlotLayout Measured2560x1600() noexcept;
};

struct HudOwnedSlotRoiResult final {
  std::optional<std::array<detector::PixelRoi, kHudOwnedSlotCount>> value{};
  std::string reason{"invalid_hud_slot_layout"};

  [[nodiscard]] bool ok() const noexcept { return value.has_value(); }
};

[[nodiscard]] HudOwnedSlotRoiResult ComputeHudOwnedSlotRois(
    std::uint32_t frame_width, std::uint32_t frame_height,
    const HudOwnedSlotLayout& layout =
        HudOwnedSlotLayout::Measured2560x1600()) noexcept;

struct HudIconAppearance final {
  std::uint8_t minimum_luma{0U};
  std::uint8_t maximum_luma{0U};
  float edge_fraction{0.0F};

  friend bool operator==(const HudIconAppearance&,
                         const HudIconAppearance&) = default;
};

struct SelectedCardDetectorConfig final {
  HudOwnedSlotLayout layout{HudOwnedSlotLayout::Measured2560x1600()};
  std::uint32_t maximum_hamming_distance{10U};
  float matcher_minimum_margin{0.08F};
  float minimum_top1_score{0.84F};
  float minimum_top1_margin{0.08F};
  std::uint8_t minimum_luma_range{20U};
  float minimum_edge_fraction{0.015F};
  float minimum_slot_luma_change{0.02F};
  float maximum_aspect_ratio_relative_error{0.002F};

  [[nodiscard]] bool IsValid() const noexcept;
};

struct SelectedCardDetectionResult final {
  std::optional<std::string> candidate_id{};
  std::optional<std::size_t> hud_slot_index{};
  std::optional<detector::PixelRoi> icon_roi{};
  std::optional<std::uint64_t> observed_difference_hash{};
  HudIconAppearance previous_appearance{};
  HudIconAppearance appearance{};
  float mean_absolute_luma_change{0.0F};
  IconMatchResult icon_match{};
  std::string reason{"unknown"};

  [[nodiscard]] bool identified() const noexcept {
    return candidate_id.has_value();
  }
};

// Stateless, full-frame-pixel-only identification of the next owned augment
// icon in the bottom-left 2x2 HUD. The caller supplies a prior full frame, a
// later full frame, and the row-major slot that was empty before the choice
// (0..3). A candidate ID is exposed only when the prior slot is visibly empty,
// the slot changes, and the existing perceptual-hash matcher returns one unique
// template through stricter score/margin gates. Templates must be scoped to the
// three IDs in the pending offer; full-catalog dHash collisions intentionally
// remain Unknown. Placeholder, empty, conflict, low-information, out-of-range,
// and invalid-frame states all fail closed.
class SelectedCardDetector final {
 public:
  SelectedCardDetector(
      std::vector<IconHashTemplate> templates,
      SelectedCardDetectorConfig config = {},
      std::optional<std::string> mode = std::nullopt);

  [[nodiscard]] SelectedCardDetectionResult DetectNextOwned(
      const common::Frame& previous_full_frame,
      const common::Frame& observed_full_frame,
      std::size_t next_owned_slot_index) const noexcept;

  [[nodiscard]] const SelectedCardDetectorConfig& config() const noexcept {
    return config_;
  }

  [[nodiscard]] std::optional<std::string_view> mode() const noexcept {
    if (!mode_.has_value()) {
      return std::nullopt;
    }
    return *mode_;
  }

 private:
  SelectedCardDetectorConfig config_{};
  std::optional<std::string> mode_{};
  PerceptualHashTemplateMatcher matcher_{};
};

}  // namespace lol_assistant::vision

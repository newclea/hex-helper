#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <string>

#include "lol_assistant/common/frame.h"
#include "lol_assistant/detector/roi.h"

namespace lol_assistant::detector {

struct RoiSearchConfig final {
  bool enabled{true};
  double maximum_horizontal_offset_ratio{0.015};
  double maximum_vertical_offset_ratio{0.030};
  double offset_step_ratio{0.010};

  [[nodiscard]] bool IsValid() const noexcept;
};

struct AugmentScreenDetectorConfig final {
  NormalizedThreeCardLayout layout{};
  double aspect_ratio_tolerance{0.002};
  std::optional<double> ui_scale{};
  RoiSearchConfig roi_search{};
  std::uint32_t sample_step{2U};
  std::uint8_t edge_delta_threshold{20U};
  std::uint8_t bright_border_luma_threshold{110U};
  float minimum_card_luma{0.16F};
  float minimum_edge_density{0.025F};
  float minimum_surround_luma_contrast{0.04F};
  float minimum_bright_border_density{0.55F};
  float minimum_border_luma_contrast{0.28F};
  float maximum_luma_spread{0.20F};
  float maximum_edge_spread{0.18F};
  float maximum_surround_contrast_spread{0.18F};
  // Hovering one augment intentionally brightens one complete frame. Keep
  // per-card minimum evidence strict while allowing that state difference.
  float maximum_bright_border_density_spread{0.30F};
  float maximum_border_luma_contrast_spread{0.25F};
  float minimum_three_column_consistency{0.65F};
  float visible_confidence_threshold{0.62F};

  [[nodiscard]] bool IsValid() const noexcept;
};

struct DetectorMetrics final {
  std::array<float, common::kAugmentCardCount> mean_luma{};
  std::array<float, common::kAugmentCardCount> edge_density{};
  std::array<float, common::kAugmentCardCount> surround_luma_contrast{};
  // Minimum bright-pixel coverage across the top/bottom/left/right frame
  // bands. Taking the weakest side makes a popup over a card fail closed.
  std::array<float, common::kAugmentCardCount> bright_border_density{};
  std::array<float, common::kAugmentCardCount> border_luma_contrast{};
  float three_column_consistency{0.0F};
  std::int32_t selected_offset_x{0};
  std::int32_t selected_offset_y{0};
  std::uint32_t evaluated_candidates{0U};
};

struct DetectorResult final {
  std::uint64_t frame_id{0U};
  bool visible{false};
  float confidence{0.0F};
  std::string reason{"not_evaluated"};
  std::optional<ThreeCardRois> rois{};
  DetectorMetrics metrics{};
};

class AugmentScreenDetector final {
 public:
  explicit AugmentScreenDetector(AugmentScreenDetectorConfig config);

  [[nodiscard]] const AugmentScreenDetectorConfig& config() const noexcept;
  [[nodiscard]] DetectorResult Detect(const common::Frame& frame) const;

 private:
  AugmentScreenDetectorConfig config_{};
};

struct StableDetectorConfig final {
  std::uint32_t required_consecutive_frames{3U};
  float minimum_frame_confidence{0.62F};

  [[nodiscard]] bool IsValid() const noexcept;
};

class ConsecutiveFrameConfirmer final {
 public:
  explicit ConsecutiveFrameConfirmer(StableDetectorConfig config = {});

  [[nodiscard]] DetectorResult Observe(const DetectorResult& current);
  void Reset() noexcept;
  [[nodiscard]] std::uint32_t consecutive_visible_frames() const noexcept;

 private:
  StableDetectorConfig config_{};
  std::uint32_t consecutive_visible_frames_{0U};
  std::optional<std::uint64_t> previous_frame_id_{};
  float window_confidence_{1.0F};
};

}  // namespace lol_assistant::detector

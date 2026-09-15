#include "selected_card_detector.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <utility>

namespace lol_assistant::vision {
namespace {

constexpr double kMeasuredWidth = 2560.0;
constexpr double kMeasuredHeight = 1600.0;

[[nodiscard]] common::NormalizedRoi MeasuredRoi(
    const double x, const double y, const double width,
    const double height) noexcept {
  return {x / kMeasuredWidth, y / kMeasuredHeight, width / kMeasuredWidth,
          height / kMeasuredHeight};
}

[[nodiscard]] bool IsUnitInterval(const float value) noexcept {
  return std::isfinite(value) && value >= 0.0F && value <= 1.0F;
}

[[nodiscard]] std::optional<detector::PixelRoi> ToPixels(
    const common::NormalizedRoi& normalized, const std::uint32_t frame_width,
    const std::uint32_t frame_height) noexcept {
  if (!normalized.IsValid() || frame_width == 0U || frame_height == 0U) {
    return std::nullopt;
  }
  const double ui_scale = static_cast<double>(frame_height) / kMeasuredHeight;
  // HUD geometry is height-scaled and left anchored. Scaling x by the full
  // frame width would drift every icon to the right on 16:9 captures.
  const auto left = static_cast<std::int64_t>(std::llround(
      normalized.x * kMeasuredWidth * ui_scale));
  const auto top = static_cast<std::int64_t>(std::llround(
      normalized.y * static_cast<double>(frame_height)));
  const auto width = static_cast<std::int64_t>(std::llround(
      normalized.width * kMeasuredWidth * ui_scale));
  const auto height = static_cast<std::int64_t>(std::llround(
      normalized.height * static_cast<double>(frame_height)));
  if (left < 0 || top < 0 || width <= 0 || height <= 0 ||
      left > std::numeric_limits<std::uint32_t>::max() ||
      top > std::numeric_limits<std::uint32_t>::max() ||
      width > std::numeric_limits<std::uint32_t>::max() ||
      height > std::numeric_limits<std::uint32_t>::max()) {
    return std::nullopt;
  }
  detector::PixelRoi roi{static_cast<std::uint32_t>(left),
                         static_cast<std::uint32_t>(top),
                         static_cast<std::uint32_t>(width),
                         static_cast<std::uint32_t>(height)};
  if (!roi.IsInside(frame_width, frame_height)) {
    return std::nullopt;
  }
  return roi;
}

[[nodiscard]] bool RectanglesOverlap(const detector::PixelRoi& left,
                                     const detector::PixelRoi& right) noexcept {
  const std::uint64_t left_right =
      static_cast<std::uint64_t>(left.x) + left.width;
  const std::uint64_t right_right =
      static_cast<std::uint64_t>(right.x) + right.width;
  const std::uint64_t left_bottom =
      static_cast<std::uint64_t>(left.y) + left.height;
  const std::uint64_t right_bottom =
      static_cast<std::uint64_t>(right.y) + right.height;
  return static_cast<std::uint64_t>(left.x) < right_right &&
         static_cast<std::uint64_t>(right.x) < left_right &&
         static_cast<std::uint64_t>(left.y) < right_bottom &&
         static_cast<std::uint64_t>(right.y) < left_bottom;
}

[[nodiscard]] std::uint8_t LumaAt(const detector::OwningBgraCrop& crop,
                                  const std::uint32_t x,
                                  const std::uint32_t y) noexcept {
  const auto offset = static_cast<std::size_t>(y) * crop.stride +
                      static_cast<std::size_t>(x) * 4U;
  const std::uint32_t blue = crop.pixels[offset + 0U];
  const std::uint32_t green = crop.pixels[offset + 1U];
  const std::uint32_t red = crop.pixels[offset + 2U];
  return static_cast<std::uint8_t>((29U * blue + 150U * green + 77U * red) >>
                                   8U);
}

[[nodiscard]] std::uint32_t AbsoluteDelta(const std::uint8_t left,
                                          const std::uint8_t right) noexcept {
  return left >= right ? static_cast<std::uint32_t>(left - right)
                       : static_cast<std::uint32_t>(right - left);
}

[[nodiscard]] HudIconAppearance MeasureAppearance(
    const detector::OwningBgraCrop& crop) noexcept {
  HudIconAppearance appearance;
  appearance.minimum_luma = 255U;
  std::uint64_t edge_count = 0U;
  std::uint64_t edge_samples = 0U;
  for (std::uint32_t y = 0U; y < crop.height; ++y) {
    for (std::uint32_t x = 0U; x < crop.width; ++x) {
      const std::uint8_t luma = LumaAt(crop, x, y);
      appearance.minimum_luma = std::min(appearance.minimum_luma, luma);
      appearance.maximum_luma = std::max(appearance.maximum_luma, luma);
      if (x + 1U < crop.width && y + 1U < crop.height) {
        ++edge_samples;
        const auto horizontal = AbsoluteDelta(luma, LumaAt(crop, x + 1U, y));
        const auto vertical = AbsoluteDelta(luma, LumaAt(crop, x, y + 1U));
        if (std::max(horizontal, vertical) >= 18U) {
          ++edge_count;
        }
      }
    }
  }
  if (edge_samples != 0U) {
    appearance.edge_fraction = static_cast<float>(edge_count) /
                               static_cast<float>(edge_samples);
  }
  return appearance;
}

[[nodiscard]] bool IsLowInformation(
    const HudIconAppearance& appearance,
    const SelectedCardDetectorConfig& config) noexcept {
  const auto luma_range = static_cast<std::uint32_t>(
      appearance.maximum_luma - appearance.minimum_luma);
  return luma_range < config.minimum_luma_range ||
         appearance.edge_fraction < config.minimum_edge_fraction;
}

[[nodiscard]] float MeanAbsoluteLumaChange(
    const detector::OwningBgraCrop& previous,
    const detector::OwningBgraCrop& observed) noexcept {
  if (!previous.IsValid() || !observed.IsValid() ||
      previous.width != observed.width || previous.height != observed.height) {
    return 0.0F;
  }
  std::uint64_t sum = 0U;
  const std::uint64_t samples =
      static_cast<std::uint64_t>(previous.width) * previous.height;
  for (std::uint32_t y = 0U; y < previous.height; ++y) {
    for (std::uint32_t x = 0U; x < previous.width; ++x) {
      sum += AbsoluteDelta(LumaAt(previous, x, y), LumaAt(observed, x, y));
    }
  }
  return static_cast<float>(sum) /
         (static_cast<float>(samples) * 255.0F);
}

[[nodiscard]] bool HasSupportedAspectRatio(
    const common::Frame& frame, const float maximum_relative_error) noexcept {
  if (frame.height == 0U) {
    return false;
  }
  const double ratio = static_cast<double>(frame.width) /
                       static_cast<double>(frame.height);
  constexpr double kSixteenTen = 16.0 / 10.0;
  constexpr double kSixteenNine = 16.0 / 9.0;
  return ratio >= kSixteenTen * (1.0 - maximum_relative_error) &&
         ratio <= kSixteenNine * (1.0 + maximum_relative_error);
}

}  // namespace

bool HudOwnedSlotLayout::IsValid() const noexcept {
  return std::all_of(icon_regions.begin(), icon_regions.end(),
                     [](const common::NormalizedRoi& roi) {
                       return roi.IsValid();
                     });
}

HudOwnedSlotLayout HudOwnedSlotLayout::Measured2560x1600() noexcept {
  HudOwnedSlotLayout layout;
  layout.icon_regions = {
      MeasuredRoi(398.0, 1408.0, 79.0, 79.0),
      MeasuredRoi(488.0, 1408.0, 79.0, 79.0),
      MeasuredRoi(398.0, 1503.0, 79.0, 79.0),
      MeasuredRoi(488.0, 1503.0, 79.0, 79.0),
  };
  return layout;
}

HudOwnedSlotRoiResult ComputeHudOwnedSlotRois(
    const std::uint32_t frame_width, const std::uint32_t frame_height,
    const HudOwnedSlotLayout& layout) noexcept {
  HudOwnedSlotRoiResult result;
  if (!layout.IsValid() || frame_width == 0U || frame_height == 0U) {
    return result;
  }

  std::array<detector::PixelRoi, kHudOwnedSlotCount> rois{};
  for (std::size_t index = 0U; index < layout.icon_regions.size(); ++index) {
    const auto roi = ToPixels(layout.icon_regions[index], frame_width,
                              frame_height);
    if (!roi.has_value()) {
      result.reason = "hud_slot_roi_out_of_bounds";
      return result;
    }
    rois[index] = *roi;
  }
  for (std::size_t left = 0U; left < rois.size(); ++left) {
    for (std::size_t right = left + 1U; right < rois.size(); ++right) {
      if (RectanglesOverlap(rois[left], rois[right])) {
        result.reason = "hud_slot_rois_overlap";
        return result;
      }
    }
  }
  result.value = rois;
  result.reason = "ok";
  return result;
}

bool SelectedCardDetectorConfig::IsValid() const noexcept {
  return layout.IsValid() && maximum_hamming_distance <= 64U &&
         IsUnitInterval(matcher_minimum_margin) &&
         IsUnitInterval(minimum_top1_score) &&
         IsUnitInterval(minimum_top1_margin) && minimum_luma_range > 0U &&
         IsUnitInterval(minimum_edge_fraction) &&
         IsUnitInterval(minimum_slot_luma_change) &&
         IsUnitInterval(maximum_aspect_ratio_relative_error);
}

SelectedCardDetector::SelectedCardDetector(
    std::vector<IconHashTemplate> templates,
    SelectedCardDetectorConfig config, std::optional<std::string> mode)
    : config_(std::move(config)),
      mode_(std::move(mode)),
      matcher_(std::move(templates), config_.maximum_hamming_distance,
               config_.matcher_minimum_margin) {
  if (!config_.IsValid()) {
    throw std::invalid_argument("invalid selected-card detector config");
  }
  if (mode_.has_value() && mode_->empty()) {
    throw std::invalid_argument("selected-card detector mode must be non-empty");
  }
}

SelectedCardDetectionResult SelectedCardDetector::DetectNextOwned(
    const common::Frame& previous_full_frame,
    const common::Frame& observed_full_frame,
    const std::size_t next_owned_slot_index) const noexcept {
  SelectedCardDetectionResult result;
  if (!previous_full_frame.IsValid()) {
    result.reason = "invalid_previous_frame";
    return result;
  }
  if (!observed_full_frame.IsValid()) {
    result.reason = "invalid_observed_frame";
    return result;
  }
  if (next_owned_slot_index >= kHudOwnedSlotCount) {
    result.reason = "next_owned_slot_out_of_range";
    return result;
  }
  if (previous_full_frame.width != observed_full_frame.width ||
      previous_full_frame.height != observed_full_frame.height) {
    result.reason = "frame_geometry_mismatch";
    return result;
  }
  if (!HasSupportedAspectRatio(observed_full_frame,
                               config_.maximum_aspect_ratio_relative_error)) {
    result.reason = "unsupported_frame_aspect_ratio";
    return result;
  }
  const auto rois = ComputeHudOwnedSlotRois(
      observed_full_frame.width, observed_full_frame.height, config_.layout);
  if (!rois.ok()) {
    result.reason = rois.reason;
    return result;
  }

  result.hud_slot_index = next_owned_slot_index;
  result.icon_roi = (*rois.value)[next_owned_slot_index];
  const auto previous_crop =
      detector::CropRawBgraOwning(previous_full_frame, *result.icon_roi);
  const auto observed_crop =
      detector::CropRawBgraOwning(observed_full_frame, *result.icon_roi);
  if (!previous_crop.ok() || !observed_crop.ok()) {
    result.reason = "hud_slot_crop_failed:" +
                    (!previous_crop.ok() ? previous_crop.reason
                                         : observed_crop.reason);
    return result;
  }
  result.previous_appearance = MeasureAppearance(*previous_crop.value);
  if (!IsLowInformation(result.previous_appearance, config_)) {
    result.reason = "next_owned_slot_was_not_empty";
    return result;
  }
  result.appearance = MeasureAppearance(*observed_crop.value);
  if (IsLowInformation(result.appearance, config_)) {
    result.reason = "hud_slot_low_information";
    return result;
  }
  result.mean_absolute_luma_change =
      MeanAbsoluteLumaChange(*previous_crop.value, *observed_crop.value);
  if (result.mean_absolute_luma_change < config_.minimum_slot_luma_change) {
    result.reason = "hud_slot_change_below_threshold";
    return result;
  }

  const auto hash = ComputeDifferenceHash(*observed_crop.value);
  if (!hash.ok()) {
    result.reason = "hud_slot_hash_failed:" + hash.reason;
    return result;
  }
  result.observed_difference_hash = hash.hash;
  const auto match_mode = mode_.has_value()
                              ? std::optional<std::string_view>{*mode_}
                              : std::nullopt;
  result.icon_match = matcher_.Match(*observed_crop.value, match_mode);
  if (result.icon_match.state != IconMatchState::Matched ||
      !result.icon_match.augment_id.has_value() ||
      result.icon_match.augment_id->empty()) {
    result.reason = "icon_not_unique_high_confidence:" +
                    result.icon_match.reason;
    return result;
  }
  if (!result.icon_match.top1_score.has_value() ||
      *result.icon_match.top1_score < config_.minimum_top1_score) {
    result.reason = "icon_top1_score_below_threshold";
    return result;
  }
  if (!result.icon_match.margin.has_value() ||
      *result.icon_match.margin < config_.minimum_top1_margin) {
    result.reason = "icon_top1_margin_below_threshold";
    return result;
  }

  result.candidate_id = result.icon_match.augment_id;
  result.reason = "unique_high_confidence_hud_icon";
  return result;
}

}  // namespace lol_assistant::vision

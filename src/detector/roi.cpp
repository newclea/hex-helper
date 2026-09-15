#include "lol_assistant/detector/roi.h"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstddef>
#include <cstring>
#include <limits>

namespace lol_assistant::detector {
namespace {

constexpr std::array<common::NormalizedRoi, common::kAugmentCardCount>
    kPhase2MeasuredCardRegions{{
        {0.19765625, 0.17777777777777778, 0.1859375, 0.49027777777777776},
        {0.4109375, 0.17777777777777778, 0.1859375, 0.49027777777777776},
        {0.62421875, 0.17777777777777778, 0.1859375, 0.49027777777777776},
    }};

// Real 2560x1440 desktop capture measured on 2026-08-30.  The game was
// configured for 2560x1600 but constrained by a 1440-high desktop, so LoL
// narrowed the cards and pulled the outer columns toward the centre.  This is
// not representable as a common x/y offset of the original Phase2 seed.
constexpr std::array<common::NormalizedRoi, common::kAugmentCardCount>
    kCompactDesktop16By9CardRegions{{
        {570.0 / 2560.0, 306.0 / 1440.0, 437.0 / 2560.0, 721.0 / 1440.0},
        {1071.0 / 2560.0, 306.0 / 1440.0, 437.0 / 2560.0, 721.0 / 1440.0},
        {1573.0 / 2560.0, 306.0 / 1440.0, 437.0 / 2560.0, 721.0 / 1440.0},
    }};

// The same UI measured in the visible window client (approximately
// 2538x1487 after removing non-client chrome).  Window resizing produces an
// aspect ratio between canonical 16:10 and 16:9, so it needs its own seed.
constexpr std::array<common::NormalizedRoi, common::kAugmentCardCount>
    kCompactWindowClientCardRegions{{
        {555.0 / 2538.0, 274.0 / 1487.0, 441.0 / 2538.0, 720.0 / 1487.0},
        {1055.0 / 2538.0, 274.0 / 1487.0, 444.0 / 2538.0, 720.0 / 1487.0},
        {1562.0 / 2538.0, 274.0 / 1487.0, 440.0 / 2538.0, 720.0 / 1487.0},
    }};

constexpr std::array<common::NormalizedRoi, common::kAugmentCardCount>
    kPhase1PlaceholderCardRegions{{
        {0.15, 0.20, 0.20, 0.55},
        {0.40, 0.20, 0.20, 0.55},
        {0.65, 0.20, 0.20, 0.55},
    }};

[[nodiscard]] bool SameRegion(const common::NormalizedRoi &left,
                              const common::NormalizedRoi &right) noexcept {
  constexpr double epsilon = 1.0e-12;
  return std::abs(left.x - right.x) <= epsilon &&
         std::abs(left.y - right.y) <= epsilon &&
         std::abs(left.width - right.width) <= epsilon &&
         std::abs(left.height - right.height) <= epsilon;
}

[[nodiscard]] bool
UsesPhase1PlaceholderLayout(const NormalizedThreeCardLayout &input) noexcept {
  bool phase1_placeholder = true;
  for (std::size_t index = 0U; index < input.card_regions.size(); ++index) {
    phase1_placeholder =
        phase1_placeholder && SameRegion(input.card_regions[index],
                                         kPhase1PlaceholderCardRegions[index]);
  }
  return phase1_placeholder;
}

[[nodiscard]] NormalizedThreeCardLayout
ResolveMeasuredLayout(const NormalizedThreeCardLayout &input) noexcept {
  if (!UsesPhase1PlaceholderLayout(input)) {
    return input;
  }
  auto resolved = input;
  resolved.card_regions = kPhase2MeasuredCardRegions;
  return resolved;
}

[[nodiscard]] bool Contains(const common::NormalizedRoi &outer,
                            const common::NormalizedRoi &inner) noexcept {
  constexpr double epsilon = 1.0e-12;
  return inner.x + epsilon >= outer.x && inner.y + epsilon >= outer.y &&
         inner.x + inner.width <= outer.x + outer.width + epsilon &&
         inner.y + inner.height <= outer.y + outer.height + epsilon;
}

[[nodiscard]] bool Contains(const PixelRect &outer,
                            const PixelRect &inner) noexcept {
  if (outer.width == 0U || outer.height == 0U || inner.width == 0U ||
      inner.height == 0U || inner.x < outer.x || inner.y < outer.y) {
    return false;
  }
  const auto outer_right = static_cast<std::uint64_t>(outer.x) + outer.width;
  const auto outer_bottom = static_cast<std::uint64_t>(outer.y) + outer.height;
  const auto inner_right = static_cast<std::uint64_t>(inner.x) + inner.width;
  const auto inner_bottom = static_cast<std::uint64_t>(inner.y) + inner.height;
  return inner_right <= outer_right && inner_bottom <= outer_bottom;
}

[[nodiscard]] bool IsSupportedRatio(const std::uint32_t width,
                                    const std::uint32_t height,
                                    const std::uint32_t ratio_width,
                                    const std::uint32_t ratio_height,
                                    const double relative_tolerance) noexcept {
  if (width == 0U || height == 0U || ratio_width == 0U || ratio_height == 0U ||
      !std::isfinite(relative_tolerance) || relative_tolerance < 0.0) {
    return false;
  }
  const double lhs =
      static_cast<double>(width) * static_cast<double>(ratio_height);
  const double rhs =
      static_cast<double>(height) * static_cast<double>(ratio_width);
  return std::abs(lhs - rhs) / rhs <= relative_tolerance;
}

[[nodiscard]] std::optional<PixelRoi>
ToPixels(const common::NormalizedRoi &normalized,
         const std::uint32_t frame_width,
         const std::uint32_t frame_height) noexcept {
  if (!normalized.IsValid() || frame_width == 0U || frame_height == 0U) {
    return std::nullopt;
  }

  const double width = static_cast<double>(frame_width);
  const double height = static_cast<double>(frame_height);
  const double left_value = normalized.x * width;
  const double top_value = normalized.y * height;
  const double right_value = (normalized.x + normalized.width) * width;
  const double bottom_value = (normalized.y + normalized.height) * height;

  const auto left = static_cast<std::uint64_t>(std::floor(left_value));
  const auto top = static_cast<std::uint64_t>(std::floor(top_value));
  const auto right = static_cast<std::uint64_t>(std::ceil(right_value));
  const auto bottom = static_cast<std::uint64_t>(std::ceil(bottom_value));
  if (right <= left || bottom <= top || right > frame_width ||
      bottom > frame_height) {
    return std::nullopt;
  }

  const PixelRoi result{static_cast<std::uint32_t>(left),
                        static_cast<std::uint32_t>(top),
                        static_cast<std::uint32_t>(right - left),
                        static_cast<std::uint32_t>(bottom - top)};
  if (!result.IsInside(frame_width, frame_height)) {
    return std::nullopt;
  }
  return result;
}

[[nodiscard]] std::optional<PixelRoi>
ToPixelsWithin(const PixelRoi &parent,
               const common::NormalizedRoi &normalized_within_parent) noexcept {
  const auto local =
      ToPixels(normalized_within_parent, parent.width, parent.height);
  if (!local.has_value()) {
    return std::nullopt;
  }
  const auto absolute_x = static_cast<std::uint64_t>(parent.x) + local->x;
  const auto absolute_y = static_cast<std::uint64_t>(parent.y) + local->y;
  if (absolute_x > std::numeric_limits<std::uint32_t>::max() ||
      absolute_y > std::numeric_limits<std::uint32_t>::max()) {
    return std::nullopt;
  }
  PixelRoi result{static_cast<std::uint32_t>(absolute_x),
                  static_cast<std::uint32_t>(absolute_y), local->width,
                  local->height};
  if (!Contains(parent.Bounds(), result.Bounds())) {
    return std::nullopt;
  }
  return result;
}

[[nodiscard]] std::optional<PixelRoi>
ShiftRoi(const PixelRoi &roi, const std::int32_t offset_x,
         const std::int32_t offset_y,
         const FrameResolution resolution) noexcept {
  const auto shifted_x = static_cast<std::int64_t>(roi.x) + offset_x;
  const auto shifted_y = static_cast<std::int64_t>(roi.y) + offset_y;
  if (shifted_x < 0 || shifted_y < 0 ||
      shifted_x > std::numeric_limits<std::uint32_t>::max() ||
      shifted_y > std::numeric_limits<std::uint32_t>::max()) {
    return std::nullopt;
  }
  PixelRoi shifted{static_cast<std::uint32_t>(shifted_x),
                   static_cast<std::uint32_t>(shifted_y), roi.width,
                   roi.height};
  if (!shifted.IsInside(resolution.width, resolution.height)) {
    return std::nullopt;
  }
  return shifted;
}

void AppendRect(std::string &output, const PixelRect &rect) {
  output.append("{\"x\":");
  output.append(std::to_string(rect.x));
  output.append(",\"y\":");
  output.append(std::to_string(rect.y));
  output.append(",\"width\":");
  output.append(std::to_string(rect.width));
  output.append(",\"height\":");
  output.append(std::to_string(rect.height));
  output.push_back('}');
}

template <std::size_t Size>
void AppendRects(std::string &output,
                 const std::array<PixelRect, Size> &rects) {
  output.push_back('[');
  for (std::size_t index = 0U; index < rects.size(); ++index) {
    if (index != 0U) {
      output.push_back(',');
    }
    AppendRect(output, rects[index]);
  }
  output.push_back(']');
}

[[nodiscard]] bool AppendFiniteDouble(std::string &output, const double value) {
  char buffer[64]{};
  const auto converted = std::to_chars(
      std::begin(buffer), std::end(buffer), value, std::chars_format::general,
      std::numeric_limits<double>::max_digits10);
  if (converted.ec != std::errc{}) {
    return false;
  }
  output.append(buffer, converted.ptr);
  return true;
}

[[nodiscard]] CropResult CropBounds(const common::Frame &frame,
                                    const PixelRect &bounds) {
  if (!frame.IsValid()) {
    return {std::nullopt, "invalid_frame"};
  }
  if (!bounds.IsInside(frame.width, frame.height)) {
    return {std::nullopt, "roi_out_of_bounds"};
  }
  if (bounds.width > std::numeric_limits<std::uint32_t>::max() /
                         common::Frame::kBytesPerPixel) {
    return {std::nullopt, "crop_stride_overflow"};
  }

  OwningBgraCrop crop;
  crop.width = bounds.width;
  crop.height = bounds.height;
  crop.stride = static_cast<std::uint32_t>(
      static_cast<std::size_t>(bounds.width) * common::Frame::kBytesPerPixel);
  const auto size = static_cast<std::size_t>(crop.stride) * crop.height;
  if (crop.height != 0U && size / crop.height != crop.stride) {
    return {std::nullopt, "crop_size_overflow"};
  }
  crop.pixels.resize(size);

  const auto source_x =
      static_cast<std::size_t>(bounds.x) * common::Frame::kBytesPerPixel;
  for (std::uint32_t row = 0U; row < bounds.height; ++row) {
    const auto source_offset =
        static_cast<std::size_t>(bounds.y + row) * frame.stride + source_x;
    const auto destination_offset = static_cast<std::size_t>(row) * crop.stride;
    std::memcpy(crop.pixels.data() + destination_offset,
                frame.buffer.data() + source_offset, crop.stride);
  }
  return {std::move(crop), "ok"};
}

} // namespace

bool PixelRect::IsInside(const std::uint32_t frame_width,
                         const std::uint32_t frame_height) const noexcept {
  if (width == 0U || height == 0U || x >= frame_width || y >= frame_height) {
    return false;
  }
  return width <= frame_width - x && height <= frame_height - y;
}

bool PixelRoi::IsInside(const std::uint32_t frame_width,
                        const std::uint32_t frame_height) const noexcept {
  if (!Bounds().IsInside(frame_width, frame_height)) {
    return false;
  }
  return !primary_ocr_rect.has_value() ||
         (primary_ocr_rect->IsInside(frame_width, frame_height) &&
          Contains(Bounds(), *primary_ocr_rect));
}

PixelRect PixelRoi::Bounds() const noexcept { return {x, y, width, height}; }

PixelRect PixelRoi::PrimaryCrop() const noexcept {
  return primary_ocr_rect.value_or(Bounds());
}

bool NormalizedThreeCardLayout::IsValid() const noexcept {
  const common::NormalizedRoi unit_region{0.0, 0.0, 1.0, 1.0};
  if (!offer_region.IsValid() || !title_region_within_card.IsValid() ||
      !icon_region_within_card.IsValid() ||
      !Contains(unit_region, title_region_within_card) ||
      !Contains(unit_region, icon_region_within_card)) {
    return false;
  }
  for (const auto &card : card_regions) {
    if (!card.IsValid() || !Contains(offer_region, card)) {
      return false;
    }
  }
  for (std::size_t index = 1U; index < card_regions.size(); ++index) {
    const auto &previous = card_regions[index - 1U];
    const auto &current = card_regions[index];
    if (previous.x + previous.width > current.x) {
      return false;
    }
  }
  return true;
}

bool FrameResolution::IsValid() const noexcept {
  return width > 0U && height > 0U;
}

bool RoiCalibration::IsValid() const noexcept {
  if (!resolution.IsValid() ||
      (ui_scale.has_value() &&
       (!std::isfinite(*ui_scale) || *ui_scale <= 0.0)) ||
      !offer.IsInside(resolution.width, resolution.height)) {
    return false;
  }
  for (std::size_t index = 0U; index < card_rect.size(); ++index) {
    if (!card_rect[index].IsInside(resolution.width, resolution.height) ||
        !title_rect[index].IsInside(resolution.width, resolution.height) ||
        !icon_rect[index].IsInside(resolution.width, resolution.height) ||
        !Contains(offer, card_rect[index]) ||
        !Contains(card_rect[index], title_rect[index]) ||
        !Contains(card_rect[index], icon_rect[index])) {
      return false;
    }
  }
  for (std::size_t index = 1U; index < card_rect.size(); ++index) {
    const auto previous_right =
        static_cast<std::uint64_t>(card_rect[index - 1U].x) +
        card_rect[index - 1U].width;
    if (previous_right > card_rect[index].x) {
      return false;
    }
  }
  return true;
}

bool ThreeCardRois::IsValid() const noexcept {
  const auto calibration = Calibration();
  if (!calibration.IsValid()) {
    return false;
  }
  for (std::size_t index = 0U; index < cards.size(); ++index) {
    if (!cards[index].primary_ocr_rect.has_value() ||
        *cards[index].primary_ocr_rect != title_rects[index].Bounds()) {
      return false;
    }
  }
  return true;
}

RoiCalibration ThreeCardRois::Calibration() const noexcept {
  RoiCalibration calibration;
  calibration.resolution = resolution;
  calibration.ui_scale = ui_scale;
  calibration.offer = offer_region.Bounds();
  for (std::size_t index = 0U; index < cards.size(); ++index) {
    calibration.card_rect[index] = cards[index].Bounds();
    calibration.title_rect[index] = title_rects[index].Bounds();
    calibration.icon_rect[index] = icon_rects[index].Bounds();
  }
  return calibration;
}

bool OwningBgraCrop::IsValid() const noexcept {
  if (width == 0U || height == 0U ||
      width > std::numeric_limits<std::uint32_t>::max() /
                  common::Frame::kBytesPerPixel) {
    return false;
  }
  const auto expected_stride = static_cast<std::uint32_t>(
      static_cast<std::size_t>(width) * common::Frame::kBytesPerPixel);
  if (stride != expected_stride) {
    return false;
  }
  const auto expected_size =
      static_cast<std::size_t>(stride) * static_cast<std::size_t>(height);
  return expected_size / static_cast<std::size_t>(height) == stride &&
         pixels.size() == expected_size;
}

bool IsSupported16By9(const std::uint32_t width, const std::uint32_t height,
                      const double relative_tolerance) noexcept {
  return IsSupportedThreeCardAspectRatio(width, height, relative_tolerance);
}

bool IsExact16By9(const std::uint32_t width, const std::uint32_t height,
                  const double relative_tolerance) noexcept {
  return IsSupportedRatio(width, height, 16U, 9U, relative_tolerance);
}

bool IsSupportedThreeCardAspectRatio(const std::uint32_t width,
                                     const std::uint32_t height,
                                     const double relative_tolerance) noexcept {
  if (width == 0U || height == 0U || !std::isfinite(relative_tolerance) ||
      relative_tolerance < 0.0) {
    return false;
  }
  // A maximized/windowed LoL client can expose a client-area ratio between
  // 16:10 and 16:9 because non-client chrome and desktop bounds constrain the
  // configured render size.  Keep ultrawide and 4:3 frames rejected while
  // accepting that real continuum.
  constexpr double minimum_ratio = 16.0 / 10.0;
  constexpr double maximum_ratio = 16.0 / 9.0;
  const double ratio = static_cast<double>(width) / static_cast<double>(height);
  return ratio >= minimum_ratio * (1.0 - relative_tolerance) &&
         ratio <= maximum_ratio * (1.0 + relative_tolerance);
}

RoiComputationResult
ComputeThreeCardRois(const std::uint32_t frame_width,
                     const std::uint32_t frame_height,
                     const NormalizedThreeCardLayout &layout,
                     const double aspect_ratio_tolerance) noexcept {
  if (!layout.IsValid()) {
    return {std::nullopt, "invalid_layout"};
  }
  if (!IsSupportedThreeCardAspectRatio(frame_width, frame_height,
                                       aspect_ratio_tolerance)) {
    return {std::nullopt, "unsupported_aspect_ratio"};
  }

  // ProductDetectorConfig still supplies the Phase1 placeholder card regions.
  // Keep that public configuration source-compatible and migrate only that
  // exact triplet to the measured Phase2 geometry at the detector boundary.
  const auto resolved_layout = ResolveMeasuredLayout(layout);
  const auto offer =
      ToPixels(resolved_layout.offer_region, frame_width, frame_height);
  if (!offer.has_value()) {
    return {std::nullopt, "offer_roi_out_of_bounds"};
  }

  ThreeCardRois result;
  result.offer_region = *offer;
  result.resolution = {frame_width, frame_height};
  for (std::size_t index = 0U; index < resolved_layout.card_regions.size();
       ++index) {
    const auto card = ToPixels(resolved_layout.card_regions[index], frame_width,
                               frame_height);
    if (!card.has_value()) {
      return {std::nullopt, "card_roi_out_of_bounds"};
    }
    const auto title =
        ToPixelsWithin(*card, resolved_layout.title_region_within_card);
    if (!title.has_value()) {
      return {std::nullopt, "title_roi_out_of_bounds"};
    }
    // Keep the measured icon geometry card-local. At 2560x1600 this maps the
    // 476x785 cards to exact 240x240 squares; other supported resolutions use
    // the same normalized card/frame derivation and half-open rounding.
    const auto icon =
        ToPixelsWithin(*card, resolved_layout.icon_region_within_card);
    if (!icon.has_value()) {
      return {std::nullopt, "icon_roi_out_of_bounds"};
    }
    result.cards[index] = *card;
    result.title_rects[index] = *title;
    result.icon_rects[index] = *icon;
    result.cards[index].primary_ocr_rect = title->Bounds();
  }
  for (std::size_t index = 1U; index < result.cards.size(); ++index) {
    const auto previous_right =
        static_cast<std::uint64_t>(result.cards[index - 1U].x) +
        result.cards[index - 1U].width;
    if (previous_right > result.cards[index].x) {
      return {std::nullopt, "card_roi_pixel_overlap"};
    }
  }
  if (!result.IsValid()) {
    return {std::nullopt, "computed_roi_calibration_invalid"};
  }
  return {result, "ok"};
}

RoiCandidateComputationResult
ComputeThreeCardRoiCandidates(const std::uint32_t frame_width,
                              const std::uint32_t frame_height,
                              const NormalizedThreeCardLayout &layout,
                              const double aspect_ratio_tolerance) {
  RoiCandidateComputationResult candidates;
  const auto primary = ComputeThreeCardRois(frame_width, frame_height, layout,
                                            aspect_ratio_tolerance);
  if (!primary.ok()) {
    candidates.reason = primary.reason;
    return candidates;
  }
  candidates.values.push_back(*primary.value);
  candidates.reason = "ok";

  // A custom explicit layout is authoritative.  ProductDetectorConfig uses
  // the exact Phase1 placeholder triplet, which opts into measured variants.
  if (!UsesPhase1PlaceholderLayout(layout)) {
    return candidates;
  }

  auto alternate_layout = layout;
  if (IsExact16By9(frame_width, frame_height, aspect_ratio_tolerance)) {
    alternate_layout.card_regions = kCompactDesktop16By9CardRegions;
  } else if (!IsSupportedRatio(frame_width, frame_height, 16U, 10U,
                               aspect_ratio_tolerance)) {
    alternate_layout.card_regions = kCompactWindowClientCardRegions;
  } else {
    return candidates;
  }

  const auto alternate = ComputeThreeCardRois(
      frame_width, frame_height, alternate_layout, aspect_ratio_tolerance);
  if (alternate.ok()) {
    candidates.values.push_back(*alternate.value);
  }
  return candidates;
}

std::optional<ThreeCardRois>
OffsetThreeCardRois(const ThreeCardRois &seed, const std::int32_t offset_x,
                    const std::int32_t offset_y) noexcept {
  if (!seed.IsValid()) {
    return std::nullopt;
  }

  ThreeCardRois shifted;
  shifted.resolution = seed.resolution;
  shifted.ui_scale = seed.ui_scale;
  const auto offer =
      ShiftRoi(seed.offer_region, offset_x, offset_y, seed.resolution);
  if (!offer.has_value()) {
    return std::nullopt;
  }
  shifted.offer_region = *offer;
  for (std::size_t index = 0U; index < seed.cards.size(); ++index) {
    const auto card =
        ShiftRoi(seed.cards[index], offset_x, offset_y, seed.resolution);
    const auto title =
        ShiftRoi(seed.title_rects[index], offset_x, offset_y, seed.resolution);
    const auto icon =
        ShiftRoi(seed.icon_rects[index], offset_x, offset_y, seed.resolution);
    if (!card.has_value() || !title.has_value() || !icon.has_value()) {
      return std::nullopt;
    }
    shifted.cards[index] = *card;
    shifted.title_rects[index] = *title;
    shifted.icon_rects[index] = *icon;
    shifted.cards[index].primary_ocr_rect = title->Bounds();
  }
  if (!shifted.IsValid()) {
    return std::nullopt;
  }
  return shifted;
}

std::optional<std::string>
SerializeRoiCalibration(const RoiCalibration &calibration) {
  if (!calibration.IsValid()) {
    return std::nullopt;
  }
  std::string output;
  output.reserve(768U);
  output.append("{\"resolution\":{\"width\":");
  output.append(std::to_string(calibration.resolution.width));
  output.append(",\"height\":");
  output.append(std::to_string(calibration.resolution.height));
  output.append("},\"ui_scale\":");
  if (calibration.ui_scale.has_value()) {
    if (!AppendFiniteDouble(output, *calibration.ui_scale)) {
      return std::nullopt;
    }
  } else {
    output.append("null");
  }
  output.append(",\"offer\":");
  AppendRect(output, calibration.offer);
  output.append(",\"card_rect\":");
  AppendRects(output, calibration.card_rect);
  output.append(",\"title_rect\":");
  AppendRects(output, calibration.title_rect);
  output.append(",\"icon_rect\":");
  AppendRects(output, calibration.icon_rect);
  output.push_back('}');
  return output;
}

CropResult CropBgraOwning(const common::Frame &frame, const PixelRoi &roi) {
  if (!frame.IsValid()) {
    return {std::nullopt, "invalid_frame"};
  }
  if (!roi.IsInside(frame.width, frame.height)) {
    return {std::nullopt, "roi_out_of_bounds"};
  }
  return CropBounds(frame, roi.PrimaryCrop());
}

CropResult CropRawBgraOwning(const common::Frame &frame, const PixelRoi &roi) {
  if (!frame.IsValid()) {
    return {std::nullopt, "invalid_frame"};
  }
  if (!roi.IsInside(frame.width, frame.height)) {
    return {std::nullopt, "roi_out_of_bounds"};
  }
  return CropBounds(frame, roi.Bounds());
}

} // namespace lol_assistant::detector

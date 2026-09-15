#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "lol_assistant/common/augment_observation.h"
#include "lol_assistant/common/frame.h"
#include "lol_assistant/common/geometry.h"

namespace lol_assistant::detector {

struct PixelRect final {
  std::uint32_t x{0U};
  std::uint32_t y{0U};
  std::uint32_t width{0U};
  std::uint32_t height{0U};

  [[nodiscard]] bool IsInside(std::uint32_t frame_width,
                              std::uint32_t frame_height) const noexcept;

  friend bool operator==(const PixelRect &, const PixelRect &) = default;
};

struct PixelRoi final {
  std::uint32_t x{0U};
  std::uint32_t y{0U};
  std::uint32_t width{0U};
  std::uint32_t height{0U};
  // ComputeThreeCardRois sets this on card ROIs. Existing callers still see
  // the full card coordinates, while the existing OCR crop path consumes the
  // title sub-ROI without requiring recognition_pipeline changes.
  std::optional<PixelRect> primary_ocr_rect{};

  [[nodiscard]] bool IsInside(std::uint32_t frame_width,
                              std::uint32_t frame_height) const noexcept;
  [[nodiscard]] PixelRect Bounds() const noexcept;
  [[nodiscard]] PixelRect PrimaryCrop() const noexcept;

  friend bool operator==(const PixelRoi &, const PixelRoi &) = default;
};

struct NormalizedThreeCardLayout final {
  common::NormalizedRoi offer_region{};
  std::array<common::NormalizedRoi, common::kAugmentCardCount> card_regions{};
  // Relative to each card. The title values preserve the measured title band.
  // The icon values are the tight 240x240 square measured in the Phase2 clear
  // 2560x1600 WGC frame; other resolutions inherit the same card-local
  // normalized geometry through the existing half-open pixel rounding.
  common::NormalizedRoi title_region_within_card{0.12605042016806722, 0.4421,
                                                 0.7478991596638656, 0.06494};
  common::NormalizedRoi icon_region_within_card{
      0.25, 0.07388535031847134, 0.5042016806722689, 0.3057324840764331};

  [[nodiscard]] bool IsValid() const noexcept;
};

struct FrameResolution final {
  std::uint32_t width{0U};
  std::uint32_t height{0U};

  [[nodiscard]] bool IsValid() const noexcept;

  friend bool operator==(const FrameResolution &,
                         const FrameResolution &) = default;
};

// Serializable calibration snapshot. Rectangles use absolute frame pixels;
// card/title/icon arrays are always Left, Center, Right.
struct RoiCalibration final {
  FrameResolution resolution{};
  std::optional<double> ui_scale{};
  PixelRect offer{};
  std::array<PixelRect, common::kAugmentCardCount> card_rect{};
  std::array<PixelRect, common::kAugmentCardCount> title_rect{};
  std::array<PixelRect, common::kAugmentCardCount> icon_rect{};

  [[nodiscard]] bool IsValid() const noexcept;
};

struct ThreeCardRois final {
  PixelRoi offer_region{};
  // Fixed semantic order: Left, Center, Right.
  std::array<PixelRoi, common::kAugmentCardCount> cards{};
  std::array<PixelRoi, common::kAugmentCardCount> title_rects{};
  std::array<PixelRoi, common::kAugmentCardCount> icon_rects{};
  FrameResolution resolution{};
  std::optional<double> ui_scale{};

  [[nodiscard]] bool IsValid() const noexcept;
  [[nodiscard]] RoiCalibration Calibration() const noexcept;
};

struct RoiComputationResult final {
  std::optional<ThreeCardRois> value{};
  std::string reason{};

  [[nodiscard]] bool ok() const noexcept { return value.has_value(); }
};

// Ordered detector seeds for one capture geometry.  The first value preserves
// the original measured layout; later values cover real window/client scaling
// modes that keep the same three-card UI but alter card width and spacing.
// Explicit caller-provided card regions remain single-seed.
struct RoiCandidateComputationResult final {
  std::vector<ThreeCardRois> values{};
  std::string reason{};

  [[nodiscard]] bool ok() const noexcept { return !values.empty(); }
};

struct OwningBgraCrop final {
  std::uint32_t width{0U};
  std::uint32_t height{0U};
  std::uint32_t stride{0U};
  std::vector<std::uint8_t> pixels{};

  [[nodiscard]] bool IsValid() const noexcept;
};

struct CropResult final {
  std::optional<OwningBgraCrop> value{};
  std::string reason{};

  [[nodiscard]] bool ok() const noexcept { return value.has_value(); }
};

// Legacy Phase1 compatibility entry point. Its supported family now includes
// both 16:9 and 16:10 so existing downstream guards do not hard-reject 16:10.
[[nodiscard]] bool IsSupported16By9(std::uint32_t width, std::uint32_t height,
                                    double relative_tolerance = 0.002) noexcept;

[[nodiscard]] bool IsExact16By9(std::uint32_t width, std::uint32_t height,
                                double relative_tolerance = 0.002) noexcept;

[[nodiscard]] bool
IsSupportedThreeCardAspectRatio(std::uint32_t width, std::uint32_t height,
                                double relative_tolerance = 0.002) noexcept;

[[nodiscard]] RoiComputationResult
ComputeThreeCardRois(std::uint32_t frame_width, std::uint32_t frame_height,
                     const NormalizedThreeCardLayout &layout,
                     double aspect_ratio_tolerance = 0.002) noexcept;

[[nodiscard]] RoiCandidateComputationResult
ComputeThreeCardRoiCandidates(std::uint32_t frame_width,
                              std::uint32_t frame_height,
                              const NormalizedThreeCardLayout &layout,
                              double aspect_ratio_tolerance = 0.002);

[[nodiscard]] std::optional<ThreeCardRois>
OffsetThreeCardRois(const ThreeCardRois &seed, std::int32_t offset_x,
                    std::int32_t offset_y) noexcept;

// Emits deterministic compact JSON, or nullopt for an invalid calibration.
[[nodiscard]] std::optional<std::string>
SerializeRoiCalibration(const RoiCalibration &calibration);

// Honors PixelRoi::primary_ocr_rect when present. Unannotated Phase1 ROIs
// retain the old full-rectangle behavior.
[[nodiscard]] CropResult CropBgraOwning(const common::Frame &frame,
                                        const PixelRoi &roi);

// Explicit full-rectangle crop for raw-card diagnostics.
[[nodiscard]] CropResult CropRawBgraOwning(const common::Frame &frame,
                                           const PixelRoi &roi);

} // namespace lol_assistant::detector

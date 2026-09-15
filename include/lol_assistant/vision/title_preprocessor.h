#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "lol_assistant/detector/roi.h"

namespace lol_assistant::vision {

enum class TitleThresholdMode : std::uint8_t {
  None = 0,
  Fixed = 1,
  Otsu = 2,
};

struct TitlePreprocessParameters final {
  bool contrast_stretch{false};
  TitleThresholdMode threshold_mode{TitleThresholdMode::None};
  std::uint8_t fixed_threshold{128U};
  std::uint32_t scale{1U};

  [[nodiscard]] bool IsValid() const noexcept;

  friend bool operator==(const TitlePreprocessParameters&,
                         const TitlePreprocessParameters&) = default;
};

struct AppliedTitlePreprocessParameters final {
  TitlePreprocessParameters requested{};
  std::uint8_t contrast_black_point{0U};
  std::uint8_t contrast_white_point{255U};
  std::optional<std::uint8_t> resolved_threshold{};

  friend bool operator==(const AppliedTitlePreprocessParameters&,
                         const AppliedTitlePreprocessParameters&) = default;
};

struct OwningGrayImage final {
  std::uint32_t width{0U};
  std::uint32_t height{0U};
  std::uint32_t stride{0U};
  std::vector<std::uint8_t> pixels{};

  [[nodiscard]] bool IsValid() const noexcept;

  friend bool operator==(const OwningGrayImage&, const OwningGrayImage&) =
      default;
};

struct TitlePreprocessVariant final {
  std::string id{};
  AppliedTitlePreprocessParameters parameters{};
  OwningGrayImage image{};

  [[nodiscard]] bool IsValid() const noexcept;

  friend bool operator==(const TitlePreprocessVariant&,
                         const TitlePreprocessVariant&) = default;
};

struct TitlePreprocessResult final {
  std::optional<TitlePreprocessVariant> value{};
  std::string reason{};

  [[nodiscard]] bool ok() const noexcept { return value.has_value(); }
};

struct TitlePreprocessEnumerationResult final {
  std::vector<TitlePreprocessVariant> variants{};
  std::string reason{};

  [[nodiscard]] bool ok() const noexcept { return !variants.empty(); }
};

class TitlePreprocessor final {
 public:
  // Policy-neutral baseline only. No OCR/synthetic accuracy claim is encoded
  // in this ordering or default.
  [[nodiscard]] static TitlePreprocessParameters DefaultParameters() noexcept;

  [[nodiscard]] static TitlePreprocessResult Process(
      const detector::OwningBgraCrop& title_crop,
      const TitlePreprocessParameters& parameters);

  // Deterministic benchmark grid: contrast off/on x threshold
  // none/fixed-128/Otsu x scale 1x/2x/3x.
  [[nodiscard]] static TitlePreprocessEnumerationResult EnumerateVariants(
      const detector::OwningBgraCrop& title_crop);
};

}  // namespace lol_assistant::vision

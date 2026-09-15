#pragma once

#include <cstdint>
#include <optional>
#include <string>

#include <winrt/Windows.Graphics.Imaging.h>

#include "lol_assistant/common/frame.h"
#include "lol_assistant/detector/roi.h"

namespace lol_assistant::vision {

enum class SoftwareBitmapConversionStatus : std::uint8_t {
  Converted = 0,
  InvalidInput = 1,
  ConversionFailed = 2,
};

struct SoftwareBitmapConversionResult final {
  SoftwareBitmapConversionStatus status{
      SoftwareBitmapConversionStatus::InvalidInput};
  std::optional<winrt::Windows::Graphics::Imaging::SoftwareBitmap> bitmap{};
  std::string reason{};

  [[nodiscard]] bool ok() const noexcept {
    return status == SoftwareBitmapConversionStatus::Converted &&
           bitmap.has_value();
  }
};

[[nodiscard]] SoftwareBitmapConversionResult BgraCropToSoftwareBitmap(
    const detector::OwningBgraCrop& crop) noexcept;

[[nodiscard]] SoftwareBitmapConversionResult FrameRoiToSoftwareBitmap(
    const common::Frame& frame, const detector::PixelRoi& roi) noexcept;

}  // namespace lol_assistant::vision

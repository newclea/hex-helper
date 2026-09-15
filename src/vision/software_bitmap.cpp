#include "lol_assistant/vision/software_bitmap.h"

#include <limits>

#include <winrt/Windows.Storage.Streams.h>
#include <winrt/base.h>

namespace lol_assistant::vision {

SoftwareBitmapConversionResult BgraCropToSoftwareBitmap(
    const detector::OwningBgraCrop& crop) noexcept {
  using winrt::Windows::Graphics::Imaging::BitmapAlphaMode;
  using winrt::Windows::Graphics::Imaging::BitmapPixelFormat;
  using winrt::Windows::Graphics::Imaging::SoftwareBitmap;
  using winrt::Windows::Storage::Streams::DataWriter;

  if (!crop.IsValid() ||
      crop.width > static_cast<std::uint32_t>(std::numeric_limits<int>::max()) ||
      crop.height >
          static_cast<std::uint32_t>(std::numeric_limits<int>::max())) {
    return {SoftwareBitmapConversionStatus::InvalidInput, std::nullopt,
            "invalid_bgra_crop"};
  }
  try {
    SoftwareBitmap bitmap{BitmapPixelFormat::Bgra8,
                          static_cast<int>(crop.width),
                          static_cast<int>(crop.height),
                          BitmapAlphaMode::Ignore};
    DataWriter writer;
    writer.WriteBytes(crop.pixels);
    bitmap.CopyFromBuffer(writer.DetachBuffer());
    return {SoftwareBitmapConversionStatus::Converted, std::move(bitmap),
            "converted"};
  } catch (const winrt::hresult_error& error) {
    return {SoftwareBitmapConversionStatus::ConversionFailed, std::nullopt,
            "software_bitmap_conversion_failed:" +
                winrt::to_string(error.message())};
  } catch (...) {
    return {SoftwareBitmapConversionStatus::ConversionFailed, std::nullopt,
            "software_bitmap_conversion_failed:unknown_exception"};
  }
}

SoftwareBitmapConversionResult FrameRoiToSoftwareBitmap(
    const common::Frame& frame, const detector::PixelRoi& roi) noexcept {
  const auto crop = detector::CropBgraOwning(frame, roi);
  if (!crop.ok()) {
    return {SoftwareBitmapConversionStatus::InvalidInput, std::nullopt,
            crop.reason};
  }
  return BgraCropToSoftwareBitmap(*crop.value);
}

}  // namespace lol_assistant::vision

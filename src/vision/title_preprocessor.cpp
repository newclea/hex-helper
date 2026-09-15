#include "lol_assistant/vision/title_preprocessor.h"

#include <algorithm>
#include <array>
#include <cstddef>
#include <limits>
#include <string_view>
#include <utility>

namespace lol_assistant::vision {
namespace {

[[nodiscard]] std::uint8_t ToGray(const std::uint8_t* pixel) noexcept {
  const std::uint32_t blue = pixel[0];
  const std::uint32_t green = pixel[1];
  const std::uint32_t red = pixel[2];
  return static_cast<std::uint8_t>((29U * blue + 150U * green + 77U * red) >>
                                   8U);
}

[[nodiscard]] std::string_view ThresholdName(
    const TitleThresholdMode mode) noexcept {
  switch (mode) {
    case TitleThresholdMode::None:
      return "gray";
    case TitleThresholdMode::Fixed:
      return "fixed";
    case TitleThresholdMode::Otsu:
      return "otsu";
  }
  return "invalid";
}

[[nodiscard]] std::string MakeVariantId(
    const TitlePreprocessParameters& parameters) {
  std::string id;
  id.reserve(32U);
  if (parameters.contrast_stretch) {
    id.append("contrast_");
  }
  id.append(ThresholdName(parameters.threshold_mode));
  if (parameters.threshold_mode == TitleThresholdMode::Fixed) {
    id.append(std::to_string(parameters.fixed_threshold));
  }
  id.push_back('_');
  id.append(std::to_string(parameters.scale));
  id.push_back('x');
  return id;
}

[[nodiscard]] OwningGrayImage ConvertToGray(
    const detector::OwningBgraCrop& crop) {
  OwningGrayImage gray;
  gray.width = crop.width;
  gray.height = crop.height;
  gray.stride = crop.width;
  gray.pixels.resize(static_cast<std::size_t>(gray.stride) * gray.height);
  for (std::uint32_t y = 0U; y < crop.height; ++y) {
    for (std::uint32_t x = 0U; x < crop.width; ++x) {
      const auto source_offset =
          static_cast<std::size_t>(y) * crop.stride +
          static_cast<std::size_t>(x) * common::Frame::kBytesPerPixel;
      gray.pixels[static_cast<std::size_t>(y) * gray.stride + x] =
          ToGray(crop.pixels.data() + source_offset);
    }
  }
  return gray;
}

void StretchContrast(OwningGrayImage& image,
                     AppliedTitlePreprocessParameters& applied) noexcept {
  const auto [minimum, maximum] =
      std::minmax_element(image.pixels.begin(), image.pixels.end());
  const std::uint8_t black_point = *minimum;
  const std::uint8_t white_point = *maximum;
  applied.contrast_black_point = black_point;
  applied.contrast_white_point = white_point;
  if (white_point == black_point) {
    return;
  }
  const std::uint32_t range =
      static_cast<std::uint32_t>(white_point) -
      static_cast<std::uint32_t>(black_point);
  for (auto& pixel : image.pixels) {
    const std::uint32_t shifted =
        static_cast<std::uint32_t>(pixel) -
        static_cast<std::uint32_t>(black_point);
    pixel = static_cast<std::uint8_t>((shifted * 255U + range / 2U) / range);
  }
}

[[nodiscard]] std::uint8_t OtsuThreshold(
    const OwningGrayImage& image) noexcept {
  std::array<std::uint64_t, 256U> histogram{};
  std::uint64_t weighted_sum = 0U;
  for (const auto pixel : image.pixels) {
    ++histogram[pixel];
    weighted_sum += pixel;
  }

  const std::uint64_t total = image.pixels.size();
  std::uint64_t background_weight = 0U;
  std::uint64_t background_sum = 0U;
  long double best_score = -1.0L;
  std::uint8_t best_threshold = image.pixels.front();
  for (std::uint32_t threshold = 0U; threshold < 256U;
       ++threshold) {
    background_weight += histogram[threshold];
    background_sum += histogram[threshold] * threshold;
    if (background_weight == 0U) {
      continue;
    }
    const std::uint64_t foreground_weight = total - background_weight;
    if (foreground_weight == 0U) {
      break;
    }
    const long double background_mean =
        static_cast<long double>(background_sum) /
        static_cast<long double>(background_weight);
    const long double foreground_mean =
        static_cast<long double>(weighted_sum - background_sum) /
        static_cast<long double>(foreground_weight);
    const long double difference = background_mean - foreground_mean;
    const long double score =
        static_cast<long double>(background_weight) *
        static_cast<long double>(foreground_weight) * difference * difference;
    // Strict comparison deliberately keeps the lowest threshold on ties.
    if (score > best_score) {
      best_score = score;
      best_threshold = static_cast<std::uint8_t>(threshold);
    }
  }
  return best_threshold;
}

void ApplyThreshold(OwningGrayImage& image,
                    AppliedTitlePreprocessParameters& applied) noexcept {
  if (applied.requested.threshold_mode == TitleThresholdMode::None) {
    return;
  }
  const std::uint8_t threshold =
      applied.requested.threshold_mode == TitleThresholdMode::Otsu
          ? OtsuThreshold(image)
          : applied.requested.fixed_threshold;
  applied.resolved_threshold = threshold;
  for (auto& pixel : image.pixels) {
    pixel = pixel > threshold ? 255U : 0U;
  }
}

[[nodiscard]] std::optional<OwningGrayImage> ScaleNearestNeighbor(
    const OwningGrayImage& source, const std::uint32_t scale) {
  if (scale == 1U) {
    return source;
  }
  if (source.width > std::numeric_limits<std::uint32_t>::max() / scale ||
      source.height > std::numeric_limits<std::uint32_t>::max() / scale) {
    return std::nullopt;
  }
  OwningGrayImage scaled;
  scaled.width = source.width * scale;
  scaled.height = source.height * scale;
  scaled.stride = scaled.width;
  const auto size = static_cast<std::size_t>(scaled.stride) * scaled.height;
  if (scaled.height != 0U && size / scaled.height != scaled.stride) {
    return std::nullopt;
  }
  scaled.pixels.resize(size);
  for (std::uint32_t y = 0U; y < scaled.height; ++y) {
    const std::uint32_t source_y = y / scale;
    for (std::uint32_t x = 0U; x < scaled.width; ++x) {
      const std::uint32_t source_x = x / scale;
      scaled.pixels[static_cast<std::size_t>(y) * scaled.stride + x] =
          source.pixels[static_cast<std::size_t>(source_y) * source.stride +
                        source_x];
    }
  }
  return scaled;
}

}  // namespace

bool TitlePreprocessParameters::IsValid() const noexcept {
  const bool valid_threshold = threshold_mode == TitleThresholdMode::None ||
                               threshold_mode == TitleThresholdMode::Fixed ||
                               threshold_mode == TitleThresholdMode::Otsu;
  return valid_threshold && scale >= 1U && scale <= 3U;
}

bool OwningGrayImage::IsValid() const noexcept {
  if (width == 0U || height == 0U || stride != width) {
    return false;
  }
  const auto expected_size =
      static_cast<std::size_t>(stride) * static_cast<std::size_t>(height);
  return expected_size / static_cast<std::size_t>(height) == stride &&
         pixels.size() == expected_size;
}

bool TitlePreprocessVariant::IsValid() const noexcept {
  if (id.empty() || !parameters.requested.IsValid() || !image.IsValid() ||
      parameters.contrast_black_point > parameters.contrast_white_point) {
    return false;
  }
  const bool threshold_expected = parameters.requested.threshold_mode !=
                                  TitleThresholdMode::None;
  return parameters.resolved_threshold.has_value() == threshold_expected;
}

TitlePreprocessParameters TitlePreprocessor::DefaultParameters() noexcept {
  return {};
}

TitlePreprocessResult TitlePreprocessor::Process(
    const detector::OwningBgraCrop& title_crop,
    const TitlePreprocessParameters& parameters) {
  if (!title_crop.IsValid()) {
    return {std::nullopt, "invalid_title_crop"};
  }
  if (!parameters.IsValid()) {
    return {std::nullopt, "invalid_preprocess_parameters"};
  }

  AppliedTitlePreprocessParameters applied;
  applied.requested = parameters;
  auto image = ConvertToGray(title_crop);
  if (parameters.contrast_stretch) {
    StretchContrast(image, applied);
  }
  ApplyThreshold(image, applied);
  auto scaled = ScaleNearestNeighbor(image, parameters.scale);
  if (!scaled.has_value() || !scaled->IsValid()) {
    return {std::nullopt, "preprocess_scale_overflow"};
  }

  TitlePreprocessVariant variant;
  variant.id = MakeVariantId(parameters);
  variant.parameters = applied;
  variant.image = std::move(*scaled);
  if (!variant.IsValid()) {
    return {std::nullopt, "invalid_preprocess_output"};
  }
  return {std::move(variant), "ok"};
}

TitlePreprocessEnumerationResult TitlePreprocessor::EnumerateVariants(
    const detector::OwningBgraCrop& title_crop) {
  TitlePreprocessEnumerationResult result;
  if (!title_crop.IsValid()) {
    result.reason = "invalid_title_crop";
    return result;
  }
  result.variants.reserve(18U);
  constexpr std::array threshold_modes{
      TitleThresholdMode::None, TitleThresholdMode::Fixed,
      TitleThresholdMode::Otsu};
  for (const bool contrast : {false, true}) {
    for (const auto threshold_mode : threshold_modes) {
      for (std::uint32_t scale = 1U; scale <= 3U; ++scale) {
        TitlePreprocessParameters parameters;
        parameters.contrast_stretch = contrast;
        parameters.threshold_mode = threshold_mode;
        parameters.fixed_threshold = 128U;
        parameters.scale = scale;
        auto processed = Process(title_crop, parameters);
        if (!processed.ok()) {
          result.variants.clear();
          result.reason = processed.reason;
          return result;
        }
        result.variants.push_back(std::move(*processed.value));
      }
    }
  }
  result.reason = "ok";
  return result;
}

}  // namespace lol_assistant::vision
